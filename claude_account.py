"""Liaison et appel du compte Claude Pro/Max (Claude Code CLI, auth OAuth) par
utilisateur authentifié sur la webapp. Chaque utilisateur a son propre dossier
de credentials (CLAUDE_CONFIG_DIR), isolé des autres : le CLI tourne donc avec
le compte Anthropic personnel de l'utilisateur courant, jamais avec une clé API
partagée. La liaison (`claude setup-token`) est un flow OAuth interactif en TUI
(pas d'option non-interactive), piloté ici via un pseudo-terminal (pty) pour en
extraire l'URL d'autorisation et lui réinjecter le code que l'utilisateur colle
dans la webapp.
"""
import json
import os
import pty
import re
import subprocess
import time

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

CONFIG_BASE = os.environ.get(
    "CLAUDE_ACCOUNTS_DIR", os.path.join(os.path.dirname(__file__), "data", "claude-accounts")
)
os.makedirs(CONFIG_BASE, exist_ok=True)

_fernet = Fernet(os.environ["ANON_DB_KEY"].encode())

_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07]*\x07|[()#][0-9A-Za-z])")
_URL_RE = re.compile(r"https://claude\.com/cai/oauth/authorize\?[^\s]+")
# token long-lived généré par `claude setup-token`, affiché en clair dans le
# terminal (jamais écrit sur disque par le CLI lui-même) ; on le capture et le
# stocke nous-mêmes, chiffré, pour le réinjecter via CLAUDE_CODE_OAUTH_TOKEN.
_TOKEN_RE = re.compile(r"sk-ant-oat01-[A-Za-z0-9_\-]+")

# sessions de liaison en cours, en mémoire (process unique) : username -> dict
_link_sessions = {}


def _config_dir(username: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", username)
    path = os.path.join(CONFIG_BASE, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _token_path(username: str) -> str:
    return os.path.join(_config_dir(username), "oauth_token.enc")


def is_linked(username: str) -> bool:
    return os.path.exists(_token_path(username))


def _save_token(username: str, token: str):
    with open(_token_path(username), "wb") as f:
        f.write(_fernet.encrypt(token.encode()))
    os.chmod(_token_path(username), 0o600)


def _load_token(username: str) -> str:
    with open(_token_path(username), "rb") as f:
        return _fernet.decrypt(f.read()).decode()


def unlink_account(username: str):
    path = _token_path(username)
    if os.path.exists(path):
        os.remove(path)
    _link_sessions.pop(username, None)


def _read_available(fd, timeout=0.3) -> str:
    import select

    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], max(0, deadline - time.time()))
        if not r:
            break
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data.decode(errors="ignore"))
        deadline = time.time() + 0.2  # un peu plus de temps si le flux continue
    return "".join(chunks)


def start_link(username: str) -> dict:
    """Démarre `claude setup-token` dans un pty isolé et renvoie l'URL OAuth
    à présenter à l'utilisateur. La session reste ouverte en mémoire en
    attendant submit_code()."""
    existing = _link_sessions.get(username)
    if existing and existing["proc"].poll() is None:
        if existing.get("url"):
            return {"url": existing["url"]}

    config_dir = _config_dir(username)
    master_fd, slave_fd = pty.openpty()
    try:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 500, 0, 0))
    except Exception:
        pass

    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir, "TERM": "xterm-256color"}
    proc = subprocess.Popen(
        ["claude", "setup-token"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    buffer = ""
    url = None
    deadline = time.time() + 20
    while time.time() < deadline and url is None:
        if proc.poll() is not None:
            break
        buffer += _ANSI_RE.sub("", _read_available(master_fd, timeout=0.5))
        match = _URL_RE.search(buffer.replace("\r", ""))
        if match:
            url = match.group(0)

    if url is None:
        _terminate(proc, master_fd)
        raise RuntimeError("Impossible de récupérer le lien d'autorisation Claude (timeout).")

    _link_sessions[username] = {"proc": proc, "master_fd": master_fd, "url": url}
    return {"url": url}


def submit_code(username: str, code: str) -> dict:
    session = _link_sessions.get(username)
    if not session:
        raise RuntimeError("Aucune liaison en cours pour cet utilisateur, recommence.")

    proc = session["proc"]
    master_fd = session["master_fd"]
    os.write(master_fd, (code.strip() + "\n").encode())

    buffer = ""
    token = None
    deadline = time.time() + 25
    while time.time() < deadline:
        buffer += _ANSI_RE.sub("", _read_available(master_fd, timeout=0.5))
        match = _TOKEN_RE.search(buffer)
        if match:
            token = match.group(0)
            break
        if proc.poll() is not None:
            break

    _terminate(proc, master_fd)
    _link_sessions.pop(username, None)

    if not token:
        try:
            with open("/tmp/claude_link_debug.log", "a") as f:
                f.write(f"--- submit_code failure for {username} ---\n{buffer}\n")
        except OSError:
            pass
        lowered = buffer.lower()
        if "invalid" in lowered or "expired" in lowered or "error" in lowered:
            raise RuntimeError("Code invalide ou expiré, recommence la liaison.")
        raise RuntimeError("La liaison n'a pas abouti, recommence.")

    _save_token(username, token)
    return {"linked": True}


def cancel_link(username: str):
    session = _link_sessions.pop(username, None)
    if session:
        _terminate(session["proc"], session["master_fd"])


def _terminate(proc, master_fd):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def _format_error_payload(payload: dict) -> str:
    """Transforme le JSON brut renvoyé par le CLI en message lisible. Le CLI
    encapsule parfois une erreur API (429 quota, 401...) dans un payload
    `result` succinct ("You've hit your session limit · resets 2:10pm
    (Europe/Paris)") qu'on préfère afficher tel quel plutôt que le JSON brut."""
    status = payload.get("api_error_status")
    text = (payload.get("result") or "").strip()
    if status == 429 or "session limit" in text.lower() or "rate limit" in text.lower():
        return text or "Limite de quota Claude atteinte, réessaie plus tard."
    return text or "Erreur Claude inconnue."


def _clean_cli_error(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict) and "result" in payload:
        return _format_error_payload(payload)
    return raw


ALLOWED_MODELS = {
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "claude-sonnet-4-6"


def run_prompt(username: str, system_prompt: str, message: str, session_id: str = None,
                model: str = DEFAULT_MODEL) -> dict:
    """Envoie un message à Claude via le CLI, authentifié avec le compte
    Pro/Max lié de `username`. Retourne {"text": ..., "session_id": ...}."""
    if not is_linked(username):
        raise RuntimeError("Aucun compte Claude lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        # liste blanche stricte : `model` vient du payload JSON du frontend,
        # jamais d'interpolation directe d'une valeur arbitraire dans les
        # arguments du CLI
        model = DEFAULT_MODEL

    config_dir = _config_dir(username)
    token = _load_token(username)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir, "CLAUDE_CODE_OAUTH_TOKEN": token}
    env.pop("ANTHROPIC_API_KEY", None)

    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system_prompt,
    ]
    if session_id:
        cmd += ["--resume", session_id]
    else:
        session_id = _new_uuid()
        cmd += ["--session-id", session_id]
    cmd.append(message)

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(_clean_cli_error(result.stderr.strip() or result.stdout.strip()))

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Réponse Claude CLI illisible: {result.stdout[:300]}")

    if payload.get("is_error"):
        raise RuntimeError(_format_error_payload(payload))

    return {
        "text": payload.get("result", ""),
        "session_id": payload.get("session_id", session_id),
    }


def stream_prompt(username: str, system_prompt: str, message: str, session_id: str = None,
                   model: str = DEFAULT_MODEL):
    """Variante streaming de run_prompt : générateur qui yield des morceaux
    de texte (deltas) au fur et à mesure, puis un dict final {"done": True,
    "text": <texte complet>, "session_id": ...}. Repose sur le mode NDJSON
    --output-format stream-json (un événement JSON par ligne), avec
    --include-partial-messages pour recevoir les deltas de texte au lieu
    d'attendre le message complet."""
    if not is_linked(username):
        raise RuntimeError("Aucun compte Claude lié pour cet utilisateur.")
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    config_dir = _config_dir(username)
    token = _load_token(username)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir, "CLAUDE_CODE_OAUTH_TOKEN": token}
    env.pop("ANTHROPIC_API_KEY", None)

    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", model,
        "--system-prompt", system_prompt,
    ]
    if session_id:
        cmd += ["--resume", session_id]
    else:
        session_id = _new_uuid()
        cmd += ["--session-id", session_id]
    cmd.append(message)

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    full_text = ""
    final_session_id = session_id
    error_to_raise = None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "stream_event":
            delta = event.get("event", {}).get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                full_text += text
                yield {"delta": text}
        elif event.get("type") == "result":
            final_session_id = event.get("session_id", final_session_id)
            if event.get("is_error"):
                error_to_raise = RuntimeError(_format_error_payload(event))
            elif not full_text:
                full_text = event.get("result", "")

    stderr = proc.stderr.read() if proc.stderr else ""
    proc.wait(timeout=10)
    if error_to_raise:
        raise error_to_raise
    if proc.returncode != 0 and not full_text:
        raise RuntimeError(_clean_cli_error(stderr.strip()))

    yield {"done": True, "text": full_text, "session_id": final_session_id}


def get_auth_status(username: str) -> dict:
    """Interroge `claude auth status` pour confirmer que les prompts passent
    bien par l'abonnement OAuth de l'utilisateur (et non une clé API). Le
    token setup-token est volontairement scope inférence-only par Anthropic :
    impossible d'obtenir email/plan/usage avec ce token (testé : 403
    permission_error sur /api/oauth/profile et /api/oauth/usage)."""
    if not is_linked(username):
        return {"loggedIn": False}

    config_dir = _config_dir(username)
    token = _load_token(username)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir, "CLAUDE_CODE_OAUTH_TOKEN": token}
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        result = subprocess.run(
            ["claude", "auth", "status", "--json"], env=env, capture_output=True, text=True, timeout=15
        )
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"loggedIn": False}


def _new_uuid() -> str:
    import uuid

    return str(uuid.uuid4())
