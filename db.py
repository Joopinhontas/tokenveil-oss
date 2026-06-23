"""Stockage SQLite des conversations. Le mapping token<->valeur réelle de
chaque conversation est chiffré au repos (Fernet) ; les messages stockés sont
les versions anonymisées (tokens), jamais les données brutes."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("ANON_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "conversations.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_fernet = Fernet(os.environ["ANON_DB_KEY"].encode())


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _, salt, digest = password_hash.split("$")
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return hmac.compare_digest(computed, digest)


def _env_bootstrap_accounts() -> dict:
    raw = os.environ.get("WEBAPP_USERS", "").strip()
    if raw:
        accounts = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            user, _, pwd = pair.partition(":")
            accounts[user.strip()] = pwd.strip()
        return accounts
    return {os.environ.get("WEBAPP_USER", "admin"): os.environ.get("WEBAPP_PASSWORD", "admin")}


def init_db():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                mapping_encrypted BLOB,
                created_at REAL NOT NULL,
                username TEXT,
                claude_session_id TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                anonymized_content TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )"""
        )
        existing_cols = {row["name"] for row in c.execute("PRAGMA table_info(conversations)")}
        if "username" not in existing_cols:
            c.execute("ALTER TABLE conversations ADD COLUMN username TEXT")
        if "claude_session_id" not in existing_cols:
            c.execute("ALTER TABLE conversations ADD COLUMN claude_session_id TEXT")
        if "favorite" not in existing_cols:
            c.execute("ALTER TABLE conversations ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")

        msg_cols = {row["name"] for row in c.execute("PRAGMA table_info(messages)")}
        if "provider" not in msg_cols:
            # provider/model utilisés pour CETTE réponse (pertinent surtout
            # côté assistant) : permet de reconstruire l'historique multi-IA
            # correctement quand on change de modèle en cours de conversation,
            # et d'afficher quelle IA a répondu à chaque message.
            c.execute("ALTER TABLE messages ADD COLUMN provider TEXT")
        if "model" not in msg_cols:
            c.execute("ALTER TABLE messages ADD COLUMN model TEXT")

        # termes custom à toujours anonymiser (mots-clés métier propres à
        # l'entreprise déployant le proxy : noms de produits internes,
        # codenames, services maison... que la NER générique ne peut pas
        # connaître à l'avance). Config globale au déploiement, pas par
        # utilisateur : c'est une politique d'anonymisation, pas une préférence.
        c.execute(
            """CREATE TABLE IF NOT EXISTS custom_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                is_regex INTEGER NOT NULL DEFAULT 0,
                label TEXT NOT NULL DEFAULT 'CUSTOM_TERM',
                created_by TEXT,
                created_at REAL NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0
            )"""
        )
        ct_cols = {row["name"] for row in c.execute("PRAGMA table_info(custom_terms)")}
        if "locked" not in ct_cols:
            c.execute("ALTER TABLE custom_terms ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
        if "scope_username" not in ct_cols:
            # NULL = déployé pour tout le monde. Sinon, actif uniquement pour
            # cet utilisateur (ex: proposition d'un utilisateur non-admin, en
            # attente de déploiement global par un admin).
            c.execute("ALTER TABLE custom_terms ADD COLUMN scope_username TEXT")

        # comptes locaux gérés en base (créés depuis l'UI admin), en plus du
        # bootstrap par fichier .env (WEBAPP_USERS) qui reste possible.
        c.execute(
            """CREATE TABLE IF NOT EXISTS local_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )

        # rôle par utilisateur, indépendant du backend d'auth (local ou LDAP) :
        # un username LDAP peut aussi se voir attribuer un rôle admin depuis
        # l'UI, sans avoir de compte local.
        c.execute(
            """CREATE TABLE IF NOT EXISTS user_roles (
                username TEXT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'user'
            )"""
        )

        # réglages applicatifs modifiables depuis l'UI (config LDAP, backend
        # d'auth...), qui prennent le pas sur les variables d'environnement du
        # .env quand ils sont définis — permet la config par IHM sans toucher
        # au fichier, tout en gardant le fichier comme méthode de secours/bootstrap.
        c.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )

        # préférences personnelles par utilisateur (avatar, nom d'affichage,
        # thème, comportement de l'aperçu...) — jamais une politique de
        # sécurité, juste du confort, donc chaque utilisateur gère les siennes.
        c.execute(
            """CREATE TABLE IF NOT EXISTS user_preferences (
                username TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (username, key)
            )"""
        )

        # audit trail des décisions d'anonymisation : QUOI a été remplacé
        # (catégorie + nombre), jamais la valeur réelle ni le texte original.
        # Sert à la conformité (RGPD/RSSI) : prouver que l'anonymisation a
        # bien eu lieu sur chaque message envoyé, sans recréer un risque de
        # fuite dans le journal lui-même.
        c.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                conversation_id INTEGER,
                created_at REAL NOT NULL,
                entity_counts TEXT NOT NULL,
                total_replaced INTEGER NOT NULL
            )"""
        )

        # bootstrap : si aucun utilisateur en base, on importe les comptes
        # WEBAPP_USERS/WEBAPP_USER du .env en admin (préserve l'accès complet
        # qu'ils avaient avant l'introduction des rôles).
        has_users = c.execute("SELECT 1 FROM local_users LIMIT 1").fetchone()
        has_roles = c.execute("SELECT 1 FROM user_roles LIMIT 1").fetchone()
        if not has_users and not has_roles:
            for username, password in _env_bootstrap_accounts().items():
                c.execute(
                    "INSERT OR IGNORE INTO local_users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, _hash_password(password), time.time()),
                )
                c.execute(
                    "INSERT OR IGNORE INTO user_roles (username, role) VALUES (?, 'admin')",
                    (username,),
                )


def list_custom_terms():
    with _conn() as c:
        rows = c.execute(
            "SELECT id, term, is_regex, label, created_by, created_at, locked, scope_username "
            "FROM custom_terms ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_custom_terms_for_user(username: str):
    """Termes actifs pour CET utilisateur : globaux (scope_username NULL) +
    ceux qui lui sont spécifiquement déployés. Utilisé pour l'anonymisation
    réelle — jamais pour l'affichage admin (qui voit tout, cf list_custom_terms)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, term, is_regex, label, created_by, created_at, locked, scope_username "
            "FROM custom_terms WHERE scope_username IS NULL OR scope_username = ? "
            "ORDER BY created_at DESC",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_custom_term(term: str, is_regex: bool, label: str, created_by: str = None,
                     locked: bool = False, scope_username: str = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO custom_terms (term, is_regex, label, created_by, created_at, locked, scope_username) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (term, int(is_regex), label, created_by, time.time(), int(locked), scope_username),
        )
        return cur.lastrowid


def is_custom_term_locked(term_id: int) -> bool:
    with _conn() as c:
        row = c.execute("SELECT locked FROM custom_terms WHERE id = ?", (term_id,)).fetchone()
        return bool(row and row["locked"])


def bulk_set_custom_terms_locked(term_ids: list, locked: bool):
    with _conn() as c:
        c.executemany(
            "UPDATE custom_terms SET locked = ? WHERE id = ?",
            [(int(locked), tid) for tid in term_ids],
        )


def set_custom_term_scope(term_id: int, scope_username: str = None):
    with _conn() as c:
        c.execute("UPDATE custom_terms SET scope_username = ? WHERE id = ?", (scope_username, term_id))


def set_custom_term_locked(term_id: int, locked: bool):
    with _conn() as c:
        c.execute("UPDATE custom_terms SET locked = ? WHERE id = ?", (int(locked), term_id))


def delete_custom_term(term_id: int):
    with _conn() as c:
        c.execute("DELETE FROM custom_terms WHERE id = ?", (term_id,))


# --- Utilisateurs locaux (gérés via l'UI admin) ---

def list_local_users():
    with _conn() as c:
        rows = c.execute(
            """SELECT local_users.username, local_users.created_at,
                      COALESCE(user_roles.role, 'user') AS role
               FROM local_users
               LEFT JOIN user_roles ON user_roles.username = local_users.username
               ORDER BY local_users.created_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_local_user_hash(username: str):
    with _conn() as c:
        row = c.execute("SELECT password_hash FROM local_users WHERE username = ?", (username,)).fetchone()
        return row["password_hash"] if row else None


def create_local_user(username: str, password: str, role: str = "user"):
    with _conn() as c:
        c.execute(
            "INSERT INTO local_users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, _hash_password(password), time.time()),
        )
        c.execute(
            "INSERT INTO user_roles (username, role) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET role = excluded.role",
            (username, role),
        )


def delete_local_user(username: str):
    with _conn() as c:
        c.execute("DELETE FROM local_users WHERE username = ?", (username,))
        c.execute("DELETE FROM user_roles WHERE username = ?", (username,))


def user_exists_local(username: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM local_users WHERE username = ?", (username,)).fetchone() is not None


# --- Rôles (local ET LDAP : indépendant du backend d'authentification) ---

def get_user_role(username: str) -> str:
    with _conn() as c:
        row = c.execute("SELECT role FROM user_roles WHERE username = ?", (username,)).fetchone()
        return row["role"] if row else "user"


def set_user_role(username: str, role: str):
    with _conn() as c:
        c.execute(
            "INSERT INTO user_roles (username, role) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET role = excluded.role",
            (username, role),
        )


# --- Réglages applicatifs (LDAP, backend d'auth...) modifiables via l'UI ---

def get_setting(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with _conn() as c:
        c.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_settings(keys: list) -> dict:
    with _conn() as c:
        rows = c.execute(
            f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?' * len(keys))})", keys
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}


@contextmanager
def _conn():
    # WAL : les lecteurs ne bloquent plus les écrivains (et inversement) —
    # en mode rollback-journal par défaut, une seule écriture verrouille le
    # fichier ENTIER, ce qui devient un vrai problème dès plusieurs users
    # actifs en même temps (chacun écrit à chaque message + ligne d'audit).
    # busy_timeout : si un verrou persiste malgré tout (writer concurrent),
    # on attend au lieu d'échouer immédiatement en "database is locked".
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _encrypt_mapping(mapping: dict) -> bytes:
    return _fernet.encrypt(json.dumps(mapping).encode())


def _decrypt_mapping(blob) -> dict:
    if not blob:
        return {"value_to_token": {}, "token_to_value": {}, "counters": {}}
    return json.loads(_fernet.decrypt(bytes(blob)).decode())


def create_conversation(title: str, username: str = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO conversations (title, mapping_encrypted, created_at, username) VALUES (?, ?, ?, ?)",
            (title, _encrypt_mapping({"value_to_token": {}, "token_to_value": {}, "counters": {}}), time.time(), username),
        )
        return cur.lastrowid


def list_conversations(username: str = None):
    with _conn() as c:
        if username is None:
            rows = c.execute(
                "SELECT id, title, created_at, favorite FROM conversations "
                "ORDER BY favorite DESC, created_at DESC"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, title, created_at, favorite FROM conversations WHERE username = ? "
                "ORDER BY favorite DESC, created_at DESC",
                (username,),
            ).fetchall()
        return [dict(r) for r in rows]


def set_conversation_favorite(conversation_id: int, favorite: bool):
    with _conn() as c:
        c.execute("UPDATE conversations SET favorite = ? WHERE id = ?", (int(favorite), conversation_id))


def get_conversation_owner(conversation_id: int):
    with _conn() as c:
        row = c.execute(
            "SELECT username FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row["username"] if row else None


def get_claude_session_id(conversation_id: int):
    with _conn() as c:
        row = c.execute(
            "SELECT claude_session_id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row["claude_session_id"] if row else None


def save_claude_session_id(conversation_id: int, session_id: str):
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET claude_session_id = ? WHERE id = ?", (session_id, conversation_id)
        )


def get_conversation_mapping(conversation_id: int) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT mapping_encrypted FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("conversation introuvable")
        return _decrypt_mapping(row["mapping_encrypted"])


def save_conversation_mapping(conversation_id: int, mapping: dict):
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET mapping_encrypted = ? WHERE id = ?",
            (_encrypt_mapping(mapping), conversation_id),
        )


def add_message(conversation_id: int, role: str, anonymized_content: str,
                 provider: str = None, model: str = None):
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (conversation_id, role, anonymized_content, created_at, provider, model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, anonymized_content, time.time(), provider, model),
        )


def get_messages(conversation_id: int):
    with _conn() as c:
        rows = c.execute(
            "SELECT role, anonymized_content, created_at, provider, model FROM messages "
            "WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_conversation(conversation_id: int):
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        c.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def rename_conversation(conversation_id: int, title: str):
    with _conn() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))


def get_user_preferences(username: str) -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT key, value FROM user_preferences WHERE username = ?", (username,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_user_preferences(username: str, prefs: dict):
    with _conn() as c:
        c.executemany(
            "INSERT INTO user_preferences (username, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(username, key) DO UPDATE SET value = excluded.value",
            [(username, k, v) for k, v in prefs.items()],
        )


def add_audit_event(username: str, conversation_id: int, entity_counts: dict):
    """entity_counts : {"PERSON": 2, "EMAIL_ADDRESS": 1, ...} — jamais la
    valeur réelle anonymisée, juste le type et le nombre d'occurrences."""
    total = sum(entity_counts.values())
    if total == 0:
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO audit_log (username, conversation_id, created_at, entity_counts, total_replaced) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, conversation_id, time.time(), json.dumps(entity_counts), total),
        )


def list_audit_events(limit: int = 200, username: str = None):
    with _conn() as c:
        if username:
            rows = c.execute(
                "SELECT id, username, conversation_id, created_at, entity_counts, total_replaced "
                "FROM audit_log WHERE username = ? ORDER BY id DESC LIMIT ?",
                (username, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, username, conversation_id, created_at, entity_counts, total_replaced "
                "FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["entity_counts"] = json.loads(d["entity_counts"])
            out.append(d)
        return out
