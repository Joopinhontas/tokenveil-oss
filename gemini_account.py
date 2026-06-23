"""Liaison et appel du compte Gemini par utilisateur authentifié sur la
webapp, via clé API personnelle (aistudio.google.com, connexion avec compte
Google). Gratuit sur les modèles Flash, sans carte bancaire ; Gemini 2.5 Pro
nécessite la facturation activée côté Google (pas évitable depuis l'app).

Pas d'équivalent OAuth type Claude Pro/Max ici : deux pistes explorées et
abandonnées après tests réels —
  - Gemini CLI : login OAuth individuel officiellement coupé par Google le
    18 juin 2026 ("This client is no longer supported for Gemini Code Assist
    for individuals"), remplacé par Antigravity.
  - Antigravity CLI (`agy`) : flow équivalent existe (URL+code à coller),
    mais son onboarding au premier lancement (choix de couleur, conditions
    d'utilisation...) s'est montré non-déterministe en test — écrans
    différents à chaque essai, pas automatisable de façon fiable par
    introspection du terminal.

L'API Gemini est sans état : l'historique de conversation est reconstruit et
réinjecté dans le prompt à chaque appel plutôt que de compter sur une
continuité serveur.
"""
import os
import re

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

load_dotenv()

CONFIG_BASE = os.environ.get(
    "GEMINI_ACCOUNTS_DIR", os.path.join(os.path.dirname(__file__), "data", "gemini-accounts")
)
os.makedirs(CONFIG_BASE, exist_ok=True)

_fernet = Fernet(os.environ["ANON_DB_KEY"].encode())

ALLOWED_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",  # plus gratuit depuis avril 2026, nécessite facturation activée côté Google
}
DEFAULT_MODEL = "gemini-2.5-flash"


def _user_dir(username: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", username)
    path = os.path.join(CONFIG_BASE, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _api_key_path(username: str) -> str:
    return os.path.join(_user_dir(username), "api_key.enc")


def is_linked(username: str) -> bool:
    return os.path.exists(_api_key_path(username))


def link_method(username: str) -> str | None:
    return "api_key" if is_linked(username) else None


def unlink_account(username: str):
    path = _api_key_path(username)
    if os.path.exists(path):
        os.remove(path)


def link_api_key(username: str, api_key: str):
    """Valide la clé avec un appel minimal avant de la sauvegarder, pour ne
    jamais stocker une clé invalide silencieusement."""
    api_key = api_key.strip()
    if not api_key:
        raise RuntimeError("Clé API vide.")
    client = genai.Client(api_key=api_key)
    try:
        client.models.generate_content(model=DEFAULT_MODEL, contents="ping")
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    path = _api_key_path(username)
    with open(path, "wb") as f:
        f.write(_fernet.encrypt(api_key.encode()))
    os.chmod(path, 0o600)


def _load_api_key(username: str) -> str:
    with open(_api_key_path(username), "rb") as f:
        return _fernet.decrypt(f.read()).decode()


def run_prompt(username: str, system_prompt: str, message: str, history: list = None,
                model: str = DEFAULT_MODEL) -> dict:
    """Envoie un message à Gemini. Sans état : `history` (liste de
    {"role": "user"|"assistant", "content": str} déjà anonymisés) est
    reconstruite à chaque appel et réinjectée — nécessaire aussi bien pour la
    continuité normale que pour reprendre le fil après un changement de
    modèle IA en cours de conversation."""
    if not is_linked(username):
        raise RuntimeError("Aucun compte Gemini lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    client = genai.Client(api_key=_load_api_key(username))

    contents = []
    for turn in (history or []):
        role = "model" if turn["role"] == "assistant" else "user"
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=turn["content"])]))
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=message)]))

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
        )
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    return {"text": response.text or ""}


def stream_prompt(username: str, system_prompt: str, message: str, history: list = None,
                   model: str = DEFAULT_MODEL):
    """Variante streaming de run_prompt : générateur qui yield des deltas de
    texte au fur et à mesure, puis un dict final {"done": True, "text": ...}."""
    if not is_linked(username):
        raise RuntimeError("Aucun compte Gemini lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    client = genai.Client(api_key=_load_api_key(username))

    contents = []
    for turn in (history or []):
        role = "model" if turn["role"] == "assistant" else "user"
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=turn["content"])]))
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=message)]))

    full_text = ""
    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
        ):
            text = chunk.text or ""
            if text:
                full_text += text
                yield {"delta": text}
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    yield {"done": True, "text": full_text}


def _clean_error(raw: str) -> str:
    lowered = raw.lower()
    if "429" in raw or "quota" in lowered or "rate" in lowered:
        return "Limite de quota Gemini atteinte (gratuit : 1500 req/jour sur Flash), réessaie plus tard."
    if "503" in raw or "unavailable" in lowered or "high demand" in lowered:
        return "Gemini est temporairement surchargé côté Google, réessaie dans un instant."
    if "403" in raw or "permission" in lowered or "api key not valid" in lowered:
        return "Clé API Gemini invalide ou refusée."
    return f"Erreur Gemini : {raw[:300]}"
