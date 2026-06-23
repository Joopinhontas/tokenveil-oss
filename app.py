"""WebApp FastAPI : chat type Open WebUI mais avec anonymisation transparente
des données sensibles avant envoi à Claude, et désanonymisation à l'affichage.
Les messages stockés en base sont les versions anonymisées ; le mapping
token<->valeur réelle est chiffré au repos (voir db.py)."""
import asyncio
import json
import os
import re
import secrets

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import claude_account
import gemini_account
import vertex_account
import bedrock_account
import openai_account
import mistral_account
import db
import license as license_mod
from anon_engine import AnonSession, scan_coverage

load_dotenv()

db.init_db()

app = FastAPI(title="TokenVeil")


@app.on_event("startup")
def _warm_up_anon_engine():
    # charge les modèles spaCy (fr+en) au démarrage du serveur plutôt qu'à la
    # première requête : sans ça, le tout premier appel à l'aperçu temps réel
    # (ou n'importe quel envoi de message) après un restart paie le coût de
    # chargement à froid — mesuré à ~5-9s — et donne l'impression que la
    # fonctionnalité elle-même est lente, alors qu'elle est instantanée une
    # fois le modèle chaud (~8ms).
    import anon_engine
    anon_engine.get_analyzer()


@app.on_event("startup")
def _load_license():
    license_mod.state.reload()
    asyncio.create_task(license_mod.phone_home_loop())


@app.get("/healthz")
def healthz():
    """Pour le healthcheck Docker et la supervision en déploiement client :
    pas d'authentification requise, juste confirmer que le process répond."""
    return {"status": "ok"}


LANGUAGE = os.environ.get("ANON_LANGUAGE", "fr")

SESSION_COOKIE = "anon_session"
# sessions en mémoire (process unique, comme _link_sessions dans
# claude_account.py) : token -> username. Perdues au restart du serveur,
# acceptable pour une alpha — force juste un nouveau login.
_sessions = {}

SYSTEM_PROMPT = (
    "Tu reçois un texte contenant des tokens de la forme <TYPE_n> (ex: <IP_ADDRESS_1>, "
    "<PERSON_2>, <CUSTOMER_REF_3>). Ce sont des espaces réservés pour des données "
    "anonymisées. Tu DOIS les recopier EXACTEMENT tels quels dans ta réponse, sans les "
    "traduire, reformuler, changer la casse ou les paraphraser. Traite-les comme des "
    "identifiants opaques.\n\n"
    "Ces tokens peuvent aussi apparaître COLLÉS à l'intérieur d'un identifiant de code, "
    "d'un nom de variable ou d'une chaîne (ex: ErrorInObject<FIRST_NAME_1>, "
    "user_<CUSTOMER_REF_2>_session). Dans ce cas, traite l'ensemble comme un identifiant "
    "ou une chaîne de caractères valide : ne sépare pas le token de son contexte, ne "
    "cherche jamais à deviner la valeur réelle masquée derrière, et base ton analyse "
    "uniquement sur la structure logique, algorithmique ou contextuelle du code/log — "
    "jamais sur le contenu de l'identifiant anonymisé lui-même.\n\n"
    "Quand l'utilisateur colle des logs techniques (erreurs, stack traces, événements "
    "système), ne te contente PAS de faire un état des lieux ou un résumé descriptif. "
    "Diagnostique la cause probable et propose directement les actions concrètes pour "
    "résoudre le problème : commandes à lancer, fichiers de config à vérifier, points de "
    "blocage les plus probables en priorité. Va droit au fix, pas à l'inventaire."
)

# Nom complet de chaque langue pour l'instruction donnée au LLM — ajouter une
# langue ne demande qu'une ligne ici (+ son entrée dans le dictionnaire i18n
# front), rien d'autre à changer dans le code.
LANGUAGE_NAMES = {
    "fr": "French", "en": "English",
}


def build_system_prompt(username: str) -> str:
    """Le prompt système est fixe, mais la langue de réponse suit la
    préférence d'interface de l'utilisateur (réglée dans Profil > Général),
    pas la langue du message envoyé — un message en anglais collé par un
    utilisateur en mode FR doit revenir en français, et inversement."""
    lang = db.get_user_preferences(username).get("language", "fr")
    lang_name = LANGUAGE_NAMES.get(lang, lang)
    return (
        f"{SYSTEM_PROMPT}\n\nYou MUST always answer in {lang_name}, regardless of "
        f"the language the user's message is written in."
    )


# Endpoints accessibles même sans licence valide (au-delà de la grace
# period) : juste ce qu'il faut pour qu'un admin atteigne le panel licence
# et en installe une nouvelle, plus le strict minimum pour que l'UI sache
# qui est connecté et puisse se déconnecter.
LICENSE_EXEMPT_PATHS = {"/api/admin/license", "/api/account", "/api/logout"}


def check_auth(request: Request, anon_session: str = Cookie(default=None)) -> str:
    username = _sessions.get(anon_session) if anon_session else None
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if request.url.path not in LICENSE_EXEMPT_PATHS and license_mod.grace_exhausted():
        raise HTTPException(
            status_code=403,
            detail="Aucune licence TokenVeil valide (délai de grâce de 15 jours dépassé). "
                   "Un administrateur doit en installer une dans Administration > Licence.",
        )
    return username


def require_admin(username: str = Depends(check_auth)) -> str:
    if db.get_user_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs.")
    return username


def _assert_owns(conversation_id: int, username: str):
    # 404 plutôt que 403 : ne pas confirmer à un autre utilisateur que cette
    # conversation existe.
    if db.get_conversation_owner(conversation_id) != username:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")


class LoginPayload(BaseModel):
    username: str
    password: str


class NewConversation(BaseModel):
    title: str = "Nouvelle conversation"


class ConversationPatch(BaseModel):
    title: str | None = None
    favorite: bool | None = None


class NewMessage(BaseModel):
    content: str
    anonymize: bool = False
    provider: str = "claude"
    model: str = claude_account.DEFAULT_MODEL


class GeminiLinkPayload(BaseModel):
    api_key: str


class ClaudeApiKeyPayload(BaseModel):
    api_key: str


class VertexLinkPayload(BaseModel):
    project_id: str
    region: str
    service_account_json: str


class BedrockLinkPayload(BaseModel):
    aws_access_key: str
    aws_secret_key: str
    aws_region: str


class OpenAiLinkPayload(BaseModel):
    api_key: str


class MistralLinkPayload(BaseModel):
    api_key: str


# providers "sans état" (API directe, sans session serveur type --resume) :
# l'historique anonymisé est reconstruit et réinjecté à chaque appel, comme
# pour Gemini. Claude reste à part (branche dédiée plus bas) car son mode
# OAuth/CLI a une vraie continuité de session côté serveur Anthropic.
STATELESS_PROVIDERS = {
    "gemini": gemini_account,
    "vertex": vertex_account,
    "bedrock": bedrock_account,
    "openai": openai_account,
    "mistral": mistral_account,
}


class LinkCode(BaseModel):
    code: str


class PreviewPayload(BaseModel):
    content: str


class CustomTermPayload(BaseModel):
    term: str
    is_regex: bool = False
    label: str = "CUSTOM_TERM"
    locked: bool = False


class NewLocalUser(BaseModel):
    username: str
    password: str
    role: str = "user"


class RolePayload(BaseModel):
    role: str


class LicenseTokenPayload(BaseModel):
    token: str


class EntitySettingsPayload(BaseModel):
    disabled: list[str] = []


class LdapConfigPayload(BaseModel):
    LDAP_SERVER: str = ""
    LDAP_BASE_DN: str = ""
    LDAP_SEARCH_FILTER: str = ""
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""
    LDAP_USE_SSL: str = "false"
    LDAP_USER_DN_TEMPLATE: str = ""
    LDAP_REQUIRE_GROUP_DN: str = ""


class LdapTenantPayload(BaseModel):
    name: str
    group_dn: str
    max_seats: int


class AuthBackendPayload(BaseModel):
    backend: str


class BenchmarkPayload(BaseModel):
    text: str


class FuzzPayload(BaseModel):
    n: int = 2000
    seed: int | None = None


@app.post("/api/login")
def api_login(payload: LoginPayload, response: Response):
    if not auth.authenticate(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    # Pas de blocage de connexion ici : sans licence valide, le gate se fait
    # par endpoint dans check_auth (tout sauf /api/admin/license), pour que
    # n'importe quel admin puisse toujours se connecter et réparer.
    is_new_session = payload.username not in _sessions.values()
    if is_new_session and license_mod.over_seat_limit():
        raise HTTPException(
            status_code=403,
            detail=f"Limite de licence atteinte ({license_mod.state.max_seats} utilisateurs max). "
                   f"Contactez votre administrateur pour augmenter le quota.",
        )
    # Plafond par tenant : indépendant de la limite globale de licence
    # ci-dessus — un tenant peut avoir sa propre sous-allocation de sièges
    # (ex: "Équipe support : 10 sièges max sur les 50 licenciés").
    if is_new_session and auth.get_auth_backend() == "ldap":
        tenant = auth.get_user_tenant(payload.username)
        if tenant:
            tenant_count = auth.count_group_members(tenant["group_dn"])
            if tenant_count is not None and tenant_count > tenant["max_seats"]:
                raise HTTPException(
                    status_code=403,
                    detail=f"Limite de sièges atteinte pour \"{tenant['name']}\" ({tenant['max_seats']} max). "
                           f"Contactez votre administrateur pour augmenter le quota de ce groupe.",
                )
    token = secrets.token_urlsafe(32)
    _sessions[token] = payload.username
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
    )
    return {"username": payload.username}


@app.post("/api/logout")
def api_logout(response: Response, anon_session: str = Cookie(default=None)):
    _sessions.pop(anon_session, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/account")
def api_account_status(username: str = Depends(check_auth)):
    linked = claude_account.is_linked(username)
    auth_status = claude_account.get_auth_status(username) if linked else {}
    return {
        "username": username,
        "role": db.get_user_role(username),
        "linked": linked,
        "claude_link_method": claude_account.link_method(username),
        "auth_method": auth_status.get("authMethod"),
        "api_provider": auth_status.get("apiProvider"),
        "gemini_linked": gemini_account.is_linked(username),
        "gemini_link_method": gemini_account.link_method(username),
        "vertex_linked": vertex_account.is_linked(username),
        "bedrock_linked": bedrock_account.is_linked(username),
        "openai_linked": openai_account.is_linked(username),
        "mistral_linked": mistral_account.is_linked(username),
    }


@app.post("/api/account/claude/api-key")
def api_claude_link_api_key(payload: ClaudeApiKeyPayload, username: str = Depends(check_auth)):
    """Liaison Claude par clé API classique (console.anthropic.com), pour
    les comptes facturés à l'usage plutôt qu'un abonnement Pro/Max. N'affecte
    pas le flow OAuth/CLI existant : une seule méthode active à la fois,
    chacune retire l'autre automatiquement (voir claude_account.py)."""
    try:
        claude_account.link_api_key(username, payload.api_key)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"linked": True}


@app.post("/api/account/vertex/link")
def api_vertex_link(payload: VertexLinkPayload, username: str = Depends(check_auth)):
    try:
        vertex_account.link_credentials(username, payload.project_id, payload.region, payload.service_account_json)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"linked": True}


@app.delete("/api/account/vertex/link")
def api_vertex_unlink(username: str = Depends(check_auth)):
    vertex_account.unlink_account(username)
    return {"ok": True}


@app.post("/api/account/bedrock/link")
def api_bedrock_link(payload: BedrockLinkPayload, username: str = Depends(check_auth)):
    try:
        bedrock_account.link_credentials(username, payload.aws_access_key, payload.aws_secret_key, payload.aws_region)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"linked": True}


@app.delete("/api/account/bedrock/link")
def api_bedrock_unlink(username: str = Depends(check_auth)):
    bedrock_account.unlink_account(username)
    return {"ok": True}


@app.post("/api/account/openai/link")
def api_openai_link(payload: OpenAiLinkPayload, username: str = Depends(check_auth)):
    """Liaison OpenAI par clé API (platform.openai.com) : seule méthode
    officiellement supportée pour un usage tiers, l'API et l'abonnement
    ChatGPT consumer étant des systèmes de facturation séparés chez OpenAI."""
    try:
        openai_account.link_api_key(username, payload.api_key)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"linked": True}


@app.delete("/api/account/openai/link")
def api_openai_unlink(username: str = Depends(check_auth)):
    openai_account.unlink_account(username)
    return {"ok": True}


@app.post("/api/account/mistral/link")
def api_mistral_link(payload: MistralLinkPayload, username: str = Depends(check_auth)):
    try:
        mistral_account.link_api_key(username, payload.api_key)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"linked": True}


@app.delete("/api/account/mistral/link")
def api_mistral_unlink(username: str = Depends(check_auth)):
    mistral_account.unlink_account(username)
    return {"ok": True}


@app.post("/api/account/gemini/link")
def api_gemini_link_api_key(payload: GeminiLinkPayload, username: str = Depends(check_auth)):
    """Liaison Gemini par clé API (aistudio.google.com) : option de repli,
    gratuite sur les modèles Flash, mais sans lien avec un abonnement perso
    (Pro reste payant). Validée par un appel test avant sauvegarde chiffrée."""
    try:
        gemini_account.link_api_key(username, payload.api_key)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"linked": True}


@app.delete("/api/account/gemini/link")
def api_gemini_unlink(username: str = Depends(check_auth)):
    gemini_account.unlink_account(username)
    return {"ok": True}


PREFERENCE_KEYS = {
    "display_name", "avatar_color", "avatar_emoji", "theme",
    "auto_preview", "compact_mode", "anonymize_default", "language",
}


@app.get("/api/preferences")
def api_get_preferences(username: str = Depends(check_auth)):
    return db.get_user_preferences(username)


@app.post("/api/preferences")
def api_set_preferences(payload: dict, username: str = Depends(check_auth)):
    """Préférences personnelles (avatar, nom d'affichage, thème, comportement
    de l'aperçu...) — pas une politique de sécurité, chaque utilisateur gère
    les siennes, aucun droit admin requis."""
    unknown = set(payload) - PREFERENCE_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Clé(s) inconnue(s) : {', '.join(unknown)}")
    def _to_str(v):
        # bool -> "true"/"false" en minuscule : str(False) donne "False" en
        # Python, ce qui ne matchait jamais les comparaisons JS côté front.
        return str(v).lower() if isinstance(v, bool) else str(v)

    db.set_user_preferences(username, {k: _to_str(v) for k, v in payload.items()})
    return db.get_user_preferences(username)


@app.post("/api/account/link/start")
def api_account_link_start(username: str = Depends(check_auth)):
    try:
        return claude_account.start_link(username)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/account/link/submit")
def api_account_link_submit(payload: LinkCode, username: str = Depends(check_auth)):
    try:
        return claude_account.submit_code(username, payload.code)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/account/link/cancel")
def api_account_link_cancel(username: str = Depends(check_auth)):
    claude_account.cancel_link(username)
    return {"ok": True}


@app.delete("/api/account/link")
def api_account_unlink(username: str = Depends(check_auth)):
    claude_account.unlink_account(username)
    return {"ok": True}


@app.get("/api/scan-script")
def api_download_scan_script(_: str = Depends(require_admin)):
    """Script autonome à exécuter sur le serveur à protéger (pas celui de la
    webapp) pour lister les candidats à l'auto-détection — voir tools/scan_infra.py.
    Réservé aux admins, comme le reste de l'onglet Auto-détection."""
    path = os.path.join(os.path.dirname(__file__), "tools", "scan_infra.py")
    return FileResponse(path, media_type="text/x-python", filename="scan_infra.py")


@app.get("/api/custom-terms")
def api_list_custom_terms(username: str = Depends(check_auth)):
    # l'admin voit tout (qui a ajouté quoi, la portée de chaque terme) pour
    # pouvoir gérer/déployer ; un utilisateur normal ne voit que ce qui le
    # concerne (termes globaux + ses propres propositions).
    if db.get_user_role(username) == "admin":
        return db.list_custom_terms()
    return db.list_custom_terms_for_user(username)


@app.post("/api/custom-terms")
def api_add_custom_term(payload: CustomTermPayload, username: str = Depends(check_auth)):
    term = payload.term.strip()
    if not term:
        raise HTTPException(status_code=400, detail="Le terme ne peut pas être vide.")
    label = re.sub(r"[^A-Za-z0-9_]", "_", payload.label.strip().upper()) or "CUSTOM_TERM"
    if payload.is_regex:
        try:
            re.compile(term)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Regex invalide : {e}")
    is_admin = db.get_user_role(username) == "admin"
    # seul un admin peut créer un terme verrouillé ou déployé globalement
    # d'emblée ; un utilisateur normal propose un terme privé (actif pour lui
    # seulement) en attendant qu'un admin le déploie pour tout le monde.
    locked = payload.locked and is_admin
    scope_username = None if is_admin else username
    term_id = db.add_custom_term(
        term, payload.is_regex, label, created_by=username, locked=locked, scope_username=scope_username
    )
    return {
        "id": term_id, "term": term, "is_regex": payload.is_regex, "label": label,
        "locked": locked, "scope_username": scope_username,
    }


@app.delete("/api/custom-terms/{term_id}")
def api_delete_custom_term(term_id: int, username: str = Depends(check_auth)):
    if db.is_custom_term_locked(term_id) and db.get_user_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Ce mot-clé est verrouillé par un administrateur.")
    db.delete_custom_term(term_id)
    return {"ok": True}


@app.post("/api/custom-terms/{term_id}/lock")
def api_lock_custom_term(term_id: int, _: str = Depends(require_admin)):
    db.set_custom_term_locked(term_id, True)
    return {"ok": True}


@app.post("/api/custom-terms/{term_id}/unlock")
def api_unlock_custom_term(term_id: int, _: str = Depends(require_admin)):
    db.set_custom_term_locked(term_id, False)
    return {"ok": True}


class BulkLockPayload(BaseModel):
    ids: list[int]
    locked: bool


@app.post("/api/custom-terms/bulk-lock")
def api_bulk_lock_custom_terms(payload: BulkLockPayload, _: str = Depends(require_admin)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Aucun terme sélectionné.")
    db.bulk_set_custom_terms_locked(payload.ids, payload.locked)
    return {"ok": True, "count": len(payload.ids)}


class DeployPayload(BaseModel):
    scope: str  # "all" | "creator" | un username précis


@app.post("/api/custom-terms/{term_id}/deploy")
def api_deploy_custom_term(term_id: int, payload: DeployPayload, _: str = Depends(require_admin)):
    terms = {t["id"]: t for t in db.list_custom_terms()}
    term = terms.get(term_id)
    if not term:
        raise HTTPException(status_code=404, detail="Terme introuvable.")
    if payload.scope == "all":
        db.set_custom_term_scope(term_id, None)
    elif payload.scope == "creator":
        db.set_custom_term_scope(term_id, term["created_by"])
    else:
        db.set_custom_term_scope(term_id, payload.scope)
    return {"ok": True}


class ImportTerm(BaseModel):
    term: str
    is_regex: bool = False
    label: str = "CUSTOM_TERM"
    locked: bool = False
    scope_username: str | None = None


class ImportPayload(BaseModel):
    terms: list[ImportTerm]


@app.post("/api/custom-terms/import")
def api_import_custom_terms(payload: ImportPayload, username: str = Depends(require_admin)):
    imported, skipped = 0, 0
    for t in payload.terms:
        term = t.term.strip()
        if not term:
            skipped += 1
            continue
        if t.is_regex:
            try:
                re.compile(term)
            except re.error:
                skipped += 1
                continue
        label = re.sub(r"[^A-Za-z0-9_]", "_", t.label.strip().upper()) or "CUSTOM_TERM"
        db.add_custom_term(
            term, t.is_regex, label, created_by=username, locked=t.locked, scope_username=t.scope_username
        )
        imported += 1
    return {"ok": True, "imported": imported, "skipped": skipped}


# ============================================================ ADMIN

@app.get("/api/admin/users")
def api_admin_list_users(_: str = Depends(require_admin)):
    return db.list_local_users()


@app.post("/api/admin/users")
def api_admin_create_user(payload: NewLocalUser, _: str = Depends(require_admin)):
    username = payload.username.strip()
    if not username or not payload.password:
        raise HTTPException(status_code=400, detail="Identifiant et mot de passe requis.")
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Rôle invalide.")
    if db.user_exists_local(username):
        raise HTTPException(status_code=409, detail="Ce compte existe déjà.")
    if not license_mod.state.is_valid:
        raise HTTPException(status_code=403, detail="Licence invalide ou expirée : impossible de créer un compte.")
    if not license_mod.seat_available():
        raise HTTPException(
            status_code=403,
            detail=f"Limite de sièges atteinte ({license_mod.state.max_seats}). Contactez votre fournisseur pour étendre la licence.",
        )
    db.create_local_user(username, payload.password, payload.role)
    return {"username": username, "role": payload.role}


@app.get("/api/admin/license")
def api_admin_license_status(_: str = Depends(require_admin)):
    summary = license_mod.state.status_summary()
    summary["seats_used"] = license_mod.seats_used()
    return summary


@app.post("/api/admin/license")
async def api_admin_install_license(payload: LicenseTokenPayload, _: str = Depends(require_admin)):
    try:
        license_mod.install_token(payload.token)
    except Exception:
        raise HTTPException(status_code=400, detail="Licence invalide : signature ou format incorrect.")
    await license_mod.phone_home_once()
    summary = license_mod.state.status_summary()
    summary["seats_used"] = license_mod.seats_used()
    return summary


@app.delete("/api/admin/license")
def api_admin_uninstall_license(_: str = Depends(require_admin)):
    """Retire la licence de cette instance — décommissionnement propre avant
    migration vers un nouveau serveur. Le license_id reste valable, mais
    reste lié à cette instance côté serveur de licences tant que le
    fournisseur n'a pas fait reset-instance pour le débloquer."""
    license_mod.uninstall()
    summary = license_mod.state.status_summary()
    summary["seats_used"] = license_mod.seats_used()
    return summary


@app.get("/api/admin/entity-settings")
def api_admin_get_entity_settings(_: str = Depends(require_admin)):
    from anon_engine import ENTITIES_OF_INTEREST
    return {
        "available": ENTITIES_OF_INTEREST,
        "disabled": db.get_disabled_entities(),
    }


@app.post("/api/admin/entity-settings")
def api_admin_set_entity_settings(payload: EntitySettingsPayload, _: str = Depends(require_admin)):
    from anon_engine import ENTITIES_OF_INTEREST
    unknown = set(payload.disabled) - set(ENTITIES_OF_INTEREST)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Catégorie(s) inconnue(s) : {', '.join(unknown)}")
    db.set_disabled_entities(payload.disabled)
    return {"disabled": payload.disabled}


@app.delete("/api/admin/users/{username}")
def api_admin_delete_user(username: str, admin_username: str = Depends(require_admin)):
    if username == admin_username:
        raise HTTPException(status_code=400, detail="Tu ne peux pas supprimer ton propre compte.")
    db.delete_local_user(username)
    return {"ok": True}


@app.patch("/api/admin/users/{username}/role")
def api_admin_set_role(username: str, payload: RolePayload, admin_username: str = Depends(require_admin)):
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Rôle invalide.")
    if username == admin_username and payload.role != "admin":
        raise HTTPException(status_code=400, detail="Tu ne peux pas te retirer tes propres droits admin.")
    db.set_user_role(username, payload.role)
    return {"ok": True}


@app.get("/api/admin/auth-backend")
def api_admin_get_auth_backend(_: str = Depends(require_admin)):
    return {"backend": auth.get_auth_backend()}


@app.post("/api/admin/auth-backend")
def api_admin_set_auth_backend(payload: AuthBackendPayload, _: str = Depends(require_admin)):
    if payload.backend not in ("local", "ldap"):
        raise HTTPException(status_code=400, detail="Backend invalide.")
    auth.set_auth_backend(payload.backend)
    return {"ok": True}


@app.get("/api/admin/ldap-config")
def api_admin_get_ldap_config(_: str = Depends(require_admin)):
    cfg = auth.get_ldap_config()
    cfg["LDAP_BIND_PASSWORD"] = "••••••••" if cfg.get("LDAP_BIND_PASSWORD") else ""
    return cfg


@app.post("/api/admin/ldap-config")
def api_admin_set_ldap_config(payload: LdapConfigPayload, _: str = Depends(require_admin)):
    config = payload.dict()
    if config.get("LDAP_BIND_PASSWORD") == "••••••••":
        config.pop("LDAP_BIND_PASSWORD")  # valeur masquée renvoyée telle quelle : on ne l'écrase pas
    auth.set_ldap_config(config)
    return {"ok": True}


@app.post("/api/admin/ldap-config/test")
def api_admin_test_ldap(payload: LdapConfigPayload, _: str = Depends(require_admin)):
    config = payload.dict()
    if config.get("LDAP_BIND_PASSWORD") == "••••••••":
        config["LDAP_BIND_PASSWORD"] = auth.get_ldap_config().get("LDAP_BIND_PASSWORD")
    return auth.test_ldap_connection(config)


@app.get("/api/admin/ldap-tenants")
def api_admin_list_ldap_tenants(_: str = Depends(require_admin)):
    tenants = db.list_ldap_tenants()
    for tenant in tenants:
        tenant["current_count"] = auth.count_group_members(tenant["group_dn"])
    return tenants


@app.post("/api/admin/ldap-tenants")
def api_admin_create_ldap_tenant(payload: LdapTenantPayload, _: str = Depends(require_admin)):
    if not payload.name.strip() or not payload.group_dn.strip():
        raise HTTPException(status_code=400, detail="Nom et DN de groupe requis.")
    if payload.max_seats < 1:
        raise HTTPException(status_code=400, detail="max_seats doit être >= 1.")
    tenant_id = db.create_ldap_tenant(payload.name.strip(), payload.group_dn.strip(), payload.max_seats)
    return {"id": tenant_id, "name": payload.name, "group_dn": payload.group_dn, "max_seats": payload.max_seats}


@app.patch("/api/admin/ldap-tenants/{tenant_id}")
def api_admin_update_ldap_tenant(tenant_id: int, payload: LdapTenantPayload, _: str = Depends(require_admin)):
    if not payload.name.strip() or not payload.group_dn.strip():
        raise HTTPException(status_code=400, detail="Nom et DN de groupe requis.")
    if payload.max_seats < 1:
        raise HTTPException(status_code=400, detail="max_seats doit être >= 1.")
    db.update_ldap_tenant(tenant_id, payload.name.strip(), payload.group_dn.strip(), payload.max_seats)
    return {"ok": True}


@app.delete("/api/admin/ldap-tenants/{tenant_id}")
def api_admin_delete_ldap_tenant(tenant_id: int, _: str = Depends(require_admin)):
    db.delete_ldap_tenant(tenant_id)
    return {"ok": True}


@app.post("/api/admin/benchmark")
def api_admin_benchmark(payload: BenchmarkPayload, username: str = Depends(require_admin)):
    """Benchmark sur des logs réels non-annotés : anonymise le texte collé et
    mesure la couverture via un scanner indépendant du moteur (voir
    anon_engine.scan_coverage). Ne sauvegarde rien — lecture seule."""
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Texte vide.")
    session = AnonSession(language=LANGUAGE, custom_terms=db.list_custom_terms_for_user(username), disabled_entities=db.get_disabled_entities())
    anonymized = session.anonymize(payload.text)
    report = scan_coverage(payload.text, anonymized)
    report["anonymized_preview"] = anonymized[:4000]
    return report


@app.get("/api/admin/audit-log")
def api_admin_audit_log(limit: int = 200, _: str = Depends(require_admin)):
    """Journal d'audit des décisions d'anonymisation : qui, quand, combien de
    quoi a été remplacé. Jamais la valeur réelle ni le contenu du message —
    seulement les compteurs par catégorie, pour la conformité RGPD/RSSI."""
    return db.list_audit_events(limit=min(max(limit, 1), 1000))


@app.post("/api/admin/fuzz")
def api_admin_fuzz(payload: FuzzPayload, _: str = Depends(require_admin)):
    """Fuzzing aléatoire (tools/fuzz_anon.py) : génère un échantillon de
    lignes synthétiques avec placement aléatoire de données sensibles, à
    seed différente à chaque appel (sauf si fournie). Contrairement au
    ground truth écrit à la main, personne ne connaît les cas à l'avance —
    c'est la mesure honnête de la couverture, pas le benchmark "vendeur"."""
    n = max(100, min(payload.n, 20000))
    seed = payload.seed if payload.seed is not None else secrets.randbelow(2**31)
    from tools.fuzz_anon import run_fuzz
    result = run_fuzz(n, seed)
    # données 100% synthétiques générées par le fuzzer lui-même (jamais de
    # vraies données client), mais on masque quand même la valeur dans le
    # rapport — convention identique au reste de l'app, même en démo.
    result["leak_examples"] = [
        {"value_preview": v[:3] + "…" + v[-2:] if len(v) > 6 else "•••", "line": line[:90]}
        for line, v, anon in result.pop("leak_examples", [])
    ]
    result["over_examples"] = [
        {"value": v, "line": line[:90]} for line, v, anon in result.pop("over_examples", [])
    ]
    return result


@app.get("/api/conversations")
def api_list_conversations(username: str = Depends(check_auth)):
    return db.list_conversations(username)


@app.post("/api/conversations")
def api_create_conversation(payload: NewConversation, username: str = Depends(check_auth)):
    conv_id = db.create_conversation(payload.title, username)
    return {"id": conv_id, "title": payload.title}


@app.delete("/api/conversations/{conversation_id}")
def api_delete_conversation(conversation_id: int, username: str = Depends(check_auth)):
    _assert_owns(conversation_id, username)
    db.delete_conversation(conversation_id)
    return {"ok": True}


@app.patch("/api/conversations/{conversation_id}")
def api_patch_conversation(conversation_id: int, payload: ConversationPatch, username: str = Depends(check_auth)):
    _assert_owns(conversation_id, username)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Titre vide.")
        db.rename_conversation(conversation_id, title[:80])
    if payload.favorite is not None:
        db.set_conversation_favorite(conversation_id, payload.favorite)
    return {"ok": True}


@app.get("/api/conversations/{conversation_id}/messages")
def api_get_messages(conversation_id: int, username: str = Depends(check_auth)):
    _assert_owns(conversation_id, username)
    state = db.get_conversation_mapping(conversation_id)
    session = AnonSession.from_state(state, language=LANGUAGE, custom_terms=db.list_custom_terms_for_user(username), disabled_entities=db.get_disabled_entities())
    messages = db.get_messages(conversation_id)
    out = []
    for m in messages:
        out.append(
            {
                "role": m["role"],
                "content": session.deanonymize(m["anonymized_content"]),
                "anonymized_content": m["anonymized_content"],
                "created_at": m["created_at"],
                "provider": m["provider"],
                "model": m["model"],
            }
        )
    return out


@app.post("/api/conversations/{conversation_id}/anonymize-preview")
def api_anonymize_preview(conversation_id: int, payload: PreviewPayload, username: str = Depends(check_auth)):
    """Aperçu en lecture seule : montre ce qui serait réellement envoyé à
    Claude (texte anonymisé), sans rien persister. Réutilise le mapping de la
    conversation pour que les tokens restent stables avec les messages déjà
    envoyés, mais ne sauvegarde pas les nouveaux tokens générés ici."""
    if conversation_id:
        _assert_owns(conversation_id, username)
    state = db.get_conversation_mapping(conversation_id) if conversation_id else None
    session = AnonSession.from_state(state or {}, language=LANGUAGE, custom_terms=db.list_custom_terms_for_user(username), disabled_entities=db.get_disabled_entities())
    return {"anonymized": session.anonymize(payload.content)}


@app.post("/api/conversations/{conversation_id}/messages")
def api_send_message(conversation_id: int, payload: NewMessage, username: str = Depends(check_auth)):
    _assert_owns(conversation_id, username)
    provider = payload.provider if payload.provider in ("claude", "gemini", "vertex", "bedrock", "openai", "mistral") else "claude"

    if provider == "claude" and not claude_account.is_linked(username):
        raise HTTPException(
            status_code=409,
            detail="Aucun compte Claude lié. Ouvre Préférences pour lier ton compte Pro/Max.",
        )
    if provider in STATELESS_PROVIDERS and not STATELESS_PROVIDERS[provider].is_linked(username):
        raise HTTPException(
            status_code=409,
            detail=f"Aucun compte {provider.capitalize()} lié. Ouvre Préférences > Comptes IA pour le lier.",
        )

    state = db.get_conversation_mapping(conversation_id)
    session = AnonSession.from_state(state, language=LANGUAGE, custom_terms=db.list_custom_terms_for_user(username), disabled_entities=db.get_disabled_entities())

    if payload.anonymize:
        counts_before = dict(session.counters)
        anonymized_user_msg = session.anonymize(payload.content)
        counts_after = session.counters
        delta = {k: counts_after[k] - counts_before.get(k, 0) for k in counts_after}
        db.add_audit_event(username, conversation_id, {k: v for k, v in delta.items() if v > 0})
    else:
        anonymized_user_msg = payload.content
    db.add_message(conversation_id, "user", anonymized_user_msg)

    # historique anonymisé de la conversation (hors message qu'on vient
    # d'ajouter) : reconstruit pour les providers sans état à chaque appel,
    # et réinjecté pour Claude OAuth/CLI UNIQUEMENT si on vient de changer
    # d'IA (sinon sa propre session --resume porte déjà tout le contexte,
    # pas besoin de ressasher l'historique à chaque tour).
    prior_messages = db.get_messages(conversation_id)[:-1]
    history = [{"role": m["role"], "content": m["anonymized_content"]} for m in prior_messages]
    last_provider = next((m["provider"] for m in reversed(prior_messages) if m["role"] == "assistant"), None)
    switched_provider = last_provider is not None and last_provider != provider

    try:
        if provider in STATELESS_PROVIDERS:
            result = STATELESS_PROVIDERS[provider].run_prompt(
                username, build_system_prompt(username), anonymized_user_msg, history=history, model=payload.model,
            )
        else:
            claude_session_id = None if switched_provider else db.get_claude_session_id(conversation_id)
            claude_message = anonymized_user_msg
            if switched_provider and history:
                transcript = "\n".join(
                    f"{'Utilisateur' if h['role'] == 'user' else 'Assistant'}: {h['content']}" for h in history
                )
                claude_message = (
                    f"[Contexte de la conversation précédente, avec une autre IA]\n{transcript}\n\n"
                    f"[Nouveau message]\n{anonymized_user_msg}"
                )
            result = claude_account.run_prompt(
                username, build_system_prompt(username), claude_message,
                session_id=claude_session_id, model=payload.model, history=history,
            )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    anonymized_reply = result["text"]
    db.add_message(conversation_id, "assistant", anonymized_reply, provider=provider, model=payload.model)
    db.save_conversation_mapping(conversation_id, session.to_state())
    if provider == "claude" and result.get("session_id"):
        db.save_claude_session_id(conversation_id, result["session_id"])

    conversations = {c["id"]: c for c in db.list_conversations(username)}
    if conversations.get(conversation_id, {}).get("title") == "Nouvelle conversation":
        title = payload.content.strip()[:50] or "Conversation"
        db.rename_conversation(conversation_id, title)

    return {
        "user_message": payload.content,
        "user_message_anonymized": anonymized_user_msg,
        "assistant_message": session.deanonymize(anonymized_reply),
        "assistant_message_anonymized": anonymized_reply,
    }


@app.post("/api/conversations/{conversation_id}/messages/stream")
def api_send_message_stream(conversation_id: int, payload: NewMessage, username: str = Depends(check_auth)):
    """Variante streaming : Server-Sent Events, un événement par delta de
    texte généré par l'IA, pour un affichage progressif côté client (comme
    claude.ai/gemini). Désanonymise le texte ACCUMULÉ à chaque événement
    (pas juste le delta) : un placeholder type <EMAIL_ADDRESS_1> peut être
    coupé en deux par un chunk, se réécrire correctement dès le suivant."""
    _assert_owns(conversation_id, username)
    provider = payload.provider if payload.provider in ("claude", "gemini", "vertex", "bedrock", "openai", "mistral") else "claude"

    if provider == "claude" and not claude_account.is_linked(username):
        raise HTTPException(status_code=409, detail="Aucun compte Claude lié. Ouvre Préférences pour lier ton compte Pro/Max.")
    if provider in STATELESS_PROVIDERS and not STATELESS_PROVIDERS[provider].is_linked(username):
        raise HTTPException(status_code=409, detail=f"Aucun compte {provider.capitalize()} lié. Ouvre Préférences > Comptes IA pour le lier.")

    state = db.get_conversation_mapping(conversation_id)
    session = AnonSession.from_state(state, language=LANGUAGE, custom_terms=db.list_custom_terms_for_user(username), disabled_entities=db.get_disabled_entities())

    if payload.anonymize:
        counts_before = dict(session.counters)
        anonymized_user_msg = session.anonymize(payload.content)
        counts_after = session.counters
        delta_counts = {k: counts_after[k] - counts_before.get(k, 0) for k in counts_after}
        db.add_audit_event(username, conversation_id, {k: v for k, v in delta_counts.items() if v > 0})
    else:
        anonymized_user_msg = payload.content
    db.add_message(conversation_id, "user", anonymized_user_msg)

    prior_messages = db.get_messages(conversation_id)[:-1]
    history = [{"role": m["role"], "content": m["anonymized_content"]} for m in prior_messages]
    last_provider = next((m["provider"] for m in reversed(prior_messages) if m["role"] == "assistant"), None)
    switched_provider = last_provider is not None and last_provider != provider

    def event_stream():
        try:
            if provider in STATELESS_PROVIDERS:
                generator = STATELESS_PROVIDERS[provider].stream_prompt(
                    username, build_system_prompt(username), anonymized_user_msg, history=history, model=payload.model,
                )
            else:
                claude_session_id = None if switched_provider else db.get_claude_session_id(conversation_id)
                claude_message = anonymized_user_msg
                if switched_provider and history:
                    transcript = "\n".join(
                        f"{'Utilisateur' if h['role'] == 'user' else 'Assistant'}: {h['content']}" for h in history
                    )
                    claude_message = (
                        f"[Contexte de la conversation précédente, avec une autre IA]\n{transcript}\n\n"
                        f"[Nouveau message]\n{anonymized_user_msg}"
                    )
                generator = claude_account.stream_prompt(
                    username, build_system_prompt(username), claude_message,
                    session_id=claude_session_id, model=payload.model, history=history,
                )

            result_session_id = None
            full_text = ""
            for chunk in generator:
                if chunk.get("done"):
                    full_text = chunk["text"]
                    result_session_id = chunk.get("session_id")
                    break
                full_text += chunk["delta"]
                yield f"data: {json.dumps({'text': session.deanonymize(full_text)})}\n\n"

            db.add_message(conversation_id, "assistant", full_text, provider=provider, model=payload.model)
            db.save_conversation_mapping(conversation_id, session.to_state())
            if provider == "claude" and result_session_id:
                db.save_claude_session_id(conversation_id, result_session_id)

            conversations = {c["id"]: c for c in db.list_conversations(username)}
            if conversations.get(conversation_id, {}).get("title") == "Nouvelle conversation":
                title = payload.content.strip()[:50] or "Conversation"
                db.rename_conversation(conversation_id, title)

            yield f"data: {json.dumps({'done': True, 'text': session.deanonymize(full_text), 'text_anonymized': full_text, 'user_message_anonymized': anonymized_user_msg if payload.anonymize else None})}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
