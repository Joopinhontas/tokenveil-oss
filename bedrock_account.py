"""Liaison et appel de Claude via Amazon Bedrock, par utilisateur authentifié
sur la webapp. Même logique que vertex_account.py côté GCP : facturation et
gouvernance sur le compte cloud AWS de l'entreprise plutôt que sur un
abonnement personnel Claude Pro/Max. N'affecte pas le flow Claude Pro/Max
existant (claude_account.py, inchangé).

Chaque utilisateur lie ses propres identifiants AWS (access key + secret key
d'un utilisateur IAM avec accès Bedrock). Stockés chiffrés. API sans état
comme Gemini/Vertex : l'historique est reconstruit à chaque appel.
"""
import json
import os
import re

import anthropic
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

CONFIG_BASE = os.environ.get(
    "BEDROCK_ACCOUNTS_DIR", os.path.join(os.path.dirname(__file__), "data", "bedrock-accounts")
)
os.makedirs(CONFIG_BASE, exist_ok=True)

_fernet = Fernet(os.environ["ANON_DB_KEY"].encode())

# Claude sur Amazon Bedrock — identifiants de modèle spécifiques à la
# plateforme (différents des noms utilisés par l'API Anthropic directe).
ALLOWED_MODELS = {
    "anthropic.claude-opus-4-5-20251101-v1:0",
    "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
}
DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"


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
    return "aws_keys" if is_linked(username) else None


def unlink_account(username: str):
    path = _creds_path(username)
    if os.path.exists(path):
        os.remove(path)


def link_credentials(username: str, aws_access_key: str, aws_secret_key: str, aws_region: str):
    """Valide les identifiants avec un appel minimal avant de les
    sauvegarder, pour ne jamais stocker une config invalide silencieusement."""
    aws_access_key = aws_access_key.strip()
    aws_secret_key = aws_secret_key.strip()
    aws_region = aws_region.strip()
    if not aws_access_key or not aws_secret_key or not aws_region:
        raise RuntimeError("Access key, secret key et région requis.")

    try:
        client = _build_client(aws_access_key, aws_secret_key, aws_region)
        client.messages.create(model=DEFAULT_MODEL, max_tokens=8, messages=[{"role": "user", "content": "ping"}])
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    blob = json.dumps({
        "aws_access_key": aws_access_key, "aws_secret_key": aws_secret_key, "aws_region": aws_region,
    })
    path = _creds_path(username)
    with open(path, "wb") as f:
        f.write(_fernet.encrypt(blob.encode()))
    os.chmod(path, 0o600)


def _load_credentials(username: str) -> dict:
    with open(_creds_path(username), "rb") as f:
        return json.loads(_fernet.decrypt(f.read()).decode())


def _build_client(aws_access_key: str, aws_secret_key: str, aws_region: str) -> anthropic.AnthropicBedrock:
    return anthropic.AnthropicBedrock(
        aws_access_key=aws_access_key, aws_secret_key=aws_secret_key, aws_region=aws_region,
    )


def _clean_error(raw: str) -> str:
    lowered = raw.lower()
    if "accessdenied" in lowered or "403" in raw or "not authorized" in lowered:
        return "Identifiants AWS sans accès à Bedrock (permission IAM manquante : bedrock:InvokeModel)."
    if "404" in raw or "not found" in lowered or "validationexception" in lowered:
        return "Modèle Claude non activé dans cette région Bedrock (active-le dans Model access)."
    if "429" in raw or "throttl" in lowered:
        return "Limite de débit Bedrock atteinte, réessaie plus tard."
    return f"Erreur Bedrock : {raw[:300]}"


def run_prompt(username: str, system_prompt: str, message: str, history: list = None,
                model: str = DEFAULT_MODEL) -> dict:
    if not is_linked(username):
        raise RuntimeError("Aucun compte Bedrock lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    cfg = _load_credentials(username)
    client = _build_client(cfg["aws_access_key"], cfg["aws_secret_key"], cfg["aws_region"])
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
        raise RuntimeError("Aucun compte Bedrock lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    cfg = _load_credentials(username)
    client = _build_client(cfg["aws_access_key"], cfg["aws_secret_key"], cfg["aws_region"])
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
