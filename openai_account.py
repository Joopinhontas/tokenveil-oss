"""Liaison et appel du compte OpenAI par utilisateur authentifié sur la
webapp, via clé API personnelle ou d'entreprise (platform.openai.com).

Pas d'équivalent OAuth type Claude Pro/Max ici : contrairement à Anthropic,
OpenAI sépare strictement l'abonnement consumer (ChatGPT Plus/Pro/Team) de
la facturation API. Il n'existe pas de mécanisme public équivalent à
`claude setup-token` pour faire passer un abonnement ChatGPT personnel par
l'API. Le seul flow OAuth existant côté OpenAI est celui de Codex CLI, scopé
au produit Codex (agent de code) et non prévu pour être réutilisé par une
appli tierce — risque de fragilité/ToS du même ordre que les pistes Gemini
CLI/Antigravity déjà testées et abandonnées (voir gemini_account.py). Clé
API uniquement, donc : seule méthode officiellement supportée pour un usage
tiers, valable aussi bien pour un compte perso que pour la facturation
centralisée d'une entreprise.

API sans état comme Gemini/Vertex/Bedrock : l'historique est reconstruit et
réinjecté à chaque appel.
"""
import os
import re

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CONFIG_BASE = os.environ.get(
    "OPENAI_ACCOUNTS_DIR", os.path.join(os.path.dirname(__file__), "data", "openai-accounts")
)
os.makedirs(CONFIG_BASE, exist_ok=True)

_fernet = Fernet(os.environ["ANON_DB_KEY"].encode())

ALLOWED_MODELS = {
    "gpt-5.1",
    "gpt-5.1-mini",
}
DEFAULT_MODEL = "gpt-5.1-mini"


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
    client = OpenAI(api_key=api_key)
    try:
        client.chat.completions.create(model=DEFAULT_MODEL, max_tokens=8, messages=[{"role": "user", "content": "ping"}])
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
    if not is_linked(username):
        raise RuntimeError("Aucun compte OpenAI lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    client = OpenAI(api_key=_load_api_key(username))
    messages = [{"role": "system", "content": system_prompt}]
    for h in (history or []):
        messages.append({"role": "assistant" if h["role"] == "assistant" else "user", "content": h["content"]})
    messages.append({"role": "user", "content": message})

    try:
        response = client.chat.completions.create(model=model, messages=messages)
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    return {"text": response.choices[0].message.content or ""}


def stream_prompt(username: str, system_prompt: str, message: str, history: list = None,
                   model: str = DEFAULT_MODEL):
    if not is_linked(username):
        raise RuntimeError("Aucun compte OpenAI lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    client = OpenAI(api_key=_load_api_key(username))
    messages = [{"role": "system", "content": system_prompt}]
    for h in (history or []):
        messages.append({"role": "assistant" if h["role"] == "assistant" else "user", "content": h["content"]})
    messages.append({"role": "user", "content": message})

    full_text = ""
    try:
        stream = client.chat.completions.create(model=model, messages=messages, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                full_text += delta
                yield {"delta": delta}
    except Exception as e:
        raise RuntimeError(_clean_error(str(e)))

    yield {"done": True, "text": full_text}


def _clean_error(raw: str) -> str:
    lowered = raw.lower()
    if "401" in raw or "incorrect api key" in lowered or "invalid_api_key" in lowered:
        return "Clé API OpenAI invalide ou révoquée."
    if "429" in raw or "rate limit" in lowered or "quota" in lowered:
        return "Limite de quota OpenAI atteinte, réessaie plus tard."
    if "insufficient_quota" in lowered or "billing" in lowered:
        return "Compte OpenAI sans crédit/facturation active."
    if "503" in raw or "overloaded" in lowered:
        return "OpenAI est temporairement surchargé, réessaie dans un instant."
    return f"Erreur OpenAI : {raw[:300]}"
