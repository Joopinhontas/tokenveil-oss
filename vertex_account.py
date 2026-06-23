"""Liaison et appel de Claude via Vertex AI (Google Cloud), par utilisateur
authentifié sur la webapp. Pensé pour les entreprises qui veulent facturer
Claude sur leur compte cloud GCP existant plutôt que sur un abonnement
personnel Claude Pro/Max — gouvernance et facturation centralisées côté
client, sans rien changer au flow Claude Pro/Max existant (claude_account.py,
inchangé).

Chaque utilisateur lie son propre service account GCP (JSON de clé de
service account, colle le contenu du fichier téléchargé depuis la console
GCP — IAM > Comptes de service > Clés). Stocké chiffré, jamais en clair sur
disque. L'API Vertex est sans état comme Gemini : l'historique est
reconstruit et réinjecté à chaque appel.
"""
import json
import os
import re

import anthropic
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from google.oauth2 import service_account

load_dotenv()

CONFIG_BASE = os.environ.get(
    "VERTEX_ACCOUNTS_DIR", os.path.join(os.path.dirname(__file__), "data", "vertex-accounts")
)
os.makedirs(CONFIG_BASE, exist_ok=True)

_fernet = Fernet(os.environ["ANON_DB_KEY"].encode())

# Claude sur Vertex AI Model Garden — noms de modèles spécifiques à la
# plateforme (différents des noms utilisés par l'API Anthropic directe).
ALLOWED_MODELS = {
    "claude-opus-4-5@20251101",
    "claude-sonnet-4-5@20250929",
    "claude-haiku-4-5@20251001",
}
DEFAULT_MODEL = "claude-sonnet-4-5@20250929"


def _user_dir(username: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", username)
    path = os.path.join(CONFIG_BASE, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _creds_path(username: str) -> str:
    return os.path.join(_user_dir(username), "credentials.enc")


def is_linked(username: str) -> bool:
    return os.path.exists(_creds_path(username))


def link_method(username: str) -> str | None:
    return "service_account" if is_linked(username) else None


def unlink_account(username: str):
    path = _creds_path(username)
    if os.path.exists(path):
        os.remove(path)


def link_credentials(username: str, project_id: str, region: str, service_account_json: str):
    """Valide les identifiants avec un appel minimal avant de les
    sauvegarder, pour ne jamais stocker une config invalide silencieusement."""
    project_id = project_id.strip()
    region = region.strip()
    if not project_id or not region:
        raise RuntimeError("Project ID et région requis.")
    try:
        sa_info = json.loads(service_account_json)
    except json.JSONDecodeError:
        raise RuntimeError("JSON de service account invalide.")

    try:
        client = _build_client(project_id, region, sa_info)
        client.messages.create(model=DEFAULT_MODEL, max_tokens=8, messages=[{"role": "user", "content": "ping"}])
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    blob = json.dumps({"project_id": project_id, "region": region, "service_account": sa_info})
    path = _creds_path(username)
    with open(path, "wb") as f:
        f.write(_fernet.encrypt(blob.encode()))
    os.chmod(path, 0o600)


def _load_credentials(username: str) -> dict:
    with open(_creds_path(username), "rb") as f:
        return json.loads(_fernet.decrypt(f.read()).decode())


def _build_client(project_id: str, region: str, sa_info: dict) -> anthropic.AnthropicVertex:
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return anthropic.AnthropicVertex(project_id=project_id, region=region, credentials=creds)


def _clean_error(raw: str) -> str:
    lowered = raw.lower()
    if "403" in raw or "permission" in lowered:
        return "Service account sans accès à Vertex AI (rôle IAM manquant : aiplatform.user)."
    if "404" in raw or "not found" in lowered or "not supported" in lowered:
        return "Modèle Claude non activé sur ce projet/région Vertex (active-le dans Model Garden)."
    if "429" in raw or "quota" in lowered or "rate" in lowered:
        return "Limite de quota Vertex atteinte, réessaie plus tard."
    return f"Erreur Vertex AI : {raw[:300]}"


def run_prompt(username: str, system_prompt: str, message: str, history: list = None,
                model: str = DEFAULT_MODEL) -> dict:
    if not is_linked(username):
        raise RuntimeError("Aucun compte Vertex AI lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    cfg = _load_credentials(username)
    client = _build_client(cfg["project_id"], cfg["region"], cfg["service_account"])
    messages = [{"role": h["role"], "content": h["content"]} for h in (history or [])]
    messages.append({"role": "user", "content": message})

    try:
        response = client.messages.create(model=model, max_tokens=4096, system=system_prompt, messages=messages)
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    text = "".join(block.text for block in response.content if block.type == "text")
    return {"text": text}


def stream_prompt(username: str, system_prompt: str, message: str, history: list = None,
                   model: str = DEFAULT_MODEL):
    if not is_linked(username):
        raise RuntimeError("Aucun compte Vertex AI lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    cfg = _load_credentials(username)
    client = _build_client(cfg["project_id"], cfg["region"], cfg["service_account"])
    messages = [{"role": h["role"], "content": h["content"]} for h in (history or [])]
    messages.append({"role": "user", "content": message})

    full_text = ""
    try:
        with client.messages.stream(model=model, max_tokens=4096, system=system_prompt, messages=messages) as stream:
            for text in stream.text_stream:
                full_text += text
                yield {"delta": text}
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    yield {"done": True, "text": full_text}
