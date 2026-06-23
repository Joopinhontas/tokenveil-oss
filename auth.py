"""Authentification pour la webapp : backend local (DB + .env) ou LDAP.

Bascule via AUTH_BACKEND=local|ldap, modifiable depuis l'UI admin (stocké en
base via db.set_setting) ou via le fichier .env — la base prend le pas sur le
fichier quand un réglage y est défini, le fichier reste une méthode de
bootstrap/secours.

Mode local : comptes gérés en base (créés depuis l'UI admin, mots de passe
hashés PBKDF2), avec repli sur WEBAPP_USERS/WEBAPP_USER du .env si le compte
n'existe pas en base.

Mode LDAP : bind+search (compatible OpenLDAP et Active Directory) : on
recherche d'abord le DN de l'utilisateur avec un compte de service (ou en
anonyme), puis on tente un bind avec ce DN et le mot de passe fourni. Si le
bind réussit, l'utilisateur est authentifié — son mot de passe ne quitte
jamais ce process et n'est jamais stocké.
"""
import os

from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

import db

LDAP_SETTING_KEYS = [
    "LDAP_SERVER", "LDAP_BASE_DN", "LDAP_SEARCH_FILTER", "LDAP_BIND_DN",
    "LDAP_BIND_PASSWORD", "LDAP_USE_SSL", "LDAP_USER_DN_TEMPLATE",
    "LDAP_REQUIRE_GROUP_DN",
]


def _setting(key: str, default=None):
    """Base d'abord, fichier .env en repli."""
    value = db.get_setting(key)
    if value is not None and value != "":
        return value
    return os.environ.get(key, default)


def get_auth_backend() -> str:
    return (_setting("AUTH_BACKEND", "local") or "local").strip().lower()


def get_ldap_config() -> dict:
    return {key: _setting(key) for key in LDAP_SETTING_KEYS}


def set_auth_backend(backend: str):
    db.set_setting("AUTH_BACKEND", backend.strip().lower())


def set_ldap_config(config: dict):
    for key in LDAP_SETTING_KEYS:
        if key in config and config[key] is not None:
            db.set_setting(key, config[key])


def _local_accounts_from_env() -> dict:
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
    if os.environ.get("WEBAPP_USER"):
        return {os.environ["WEBAPP_USER"]: os.environ.get("WEBAPP_PASSWORD", "")}
    return {}


def _authenticate_local(username: str, password: str) -> bool:
    import secrets

    password_hash = db.get_local_user_hash(username)
    if password_hash:
        return db.verify_password(password, password_hash)

    # repli .env : permet de garder des comptes "fichier" en parallèle des
    # comptes créés depuis l'UI admin, sans avoir à les dupliquer en base.
    env_accounts = _local_accounts_from_env()
    expected = env_accounts.get(username)
    if expected is None:
        secrets.compare_digest(password, "")  # temps constant même si username inconnu
        return False
    return secrets.compare_digest(password, expected)


def _authenticate_ldap(username: str, password: str) -> bool:
    if not password:
        # un bind LDAP avec mot de passe vide réussit souvent en "bind anonyme"
        # côté serveur : on refuse explicitement ce cas.
        return False

    cfg = get_ldap_config()
    server_uri = cfg["LDAP_SERVER"]
    base_dn = cfg["LDAP_BASE_DN"]
    search_filter_template = cfg["LDAP_SEARCH_FILTER"] or "(uid={username})"
    bind_dn = cfg["LDAP_BIND_DN"] or None
    bind_password = cfg["LDAP_BIND_PASSWORD"] or None
    use_ssl = str(cfg["LDAP_USE_SSL"] or "false").strip().lower() == "true" or (server_uri or "").startswith(
        "ldaps://"
    )
    user_dn_template = cfg["LDAP_USER_DN_TEMPLATE"]
    require_group_dn = cfg["LDAP_REQUIRE_GROUP_DN"]

    safe_username = escape_filter_chars(username)
    server = Server(server_uri, use_ssl=use_ssl, get_info=ALL)

    try:
        if user_dn_template:
            # mode simple : DN construit directement, pas de recherche préalable
            user_dn = user_dn_template.format(username=safe_username)
        else:
            # mode bind+search : on cherche le DN avec un compte de service
            # (ou anonyme si LDAP_BIND_DN absent), nécessaire pour AD où le
            # login utilisateur (sAMAccountName) n'est pas le DN.
            search_conn = Connection(
                server, user=bind_dn, password=bind_password, auto_bind=True
            )
            search_filter = search_filter_template.format(username=safe_username)
            search_conn.search(search_base=base_dn, search_filter=search_filter, attributes=["cn"])
            if len(search_conn.entries) != 1:
                search_conn.unbind()
                return False
            user_dn = search_conn.entries[0].entry_dn
            search_conn.unbind()

        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)

        if require_group_dn:
            user_conn.search(
                search_base=require_group_dn,
                search_filter=f"(member={escape_filter_chars(user_dn)})",
                attributes=["cn"],
                search_scope="BASE",
            )
            is_member = len(user_conn.entries) > 0
            user_conn.unbind()
            return is_member

        user_conn.unbind()
        return True
    except LDAPException:
        return False


def test_ldap_connection(config: dict = None) -> dict:
    """Tente une connexion (bind du compte de service, ou anonyme) sans
    authentifier d'utilisateur — pour le bouton "Tester" de l'UI admin."""
    cfg = config or get_ldap_config()
    server_uri = cfg.get("LDAP_SERVER")
    if not server_uri:
        return {"ok": False, "error": "LDAP_SERVER manquant."}
    use_ssl = str(cfg.get("LDAP_USE_SSL") or "false").strip().lower() == "true" or server_uri.startswith("ldaps://")
    try:
        server = Server(server_uri, use_ssl=use_ssl, get_info=ALL, connect_timeout=5)
        conn = Connection(
            server,
            user=cfg.get("LDAP_BIND_DN") or None,
            password=cfg.get("LDAP_BIND_PASSWORD") or None,
            auto_bind=True,
            receive_timeout=5,
        )
        conn.unbind()
        return {"ok": True}
    except LDAPException as e:
        return {"ok": False, "error": str(e)}


def authenticate(username: str, password: str) -> bool:
    if get_auth_backend() == "ldap":
        return _authenticate_ldap(username, password)
    return _authenticate_local(username, password)


def _ldap_server_conn(cfg: dict) -> "Connection":
    server_uri = cfg["LDAP_SERVER"]
    use_ssl = str(cfg["LDAP_USE_SSL"] or "false").strip().lower() == "true" or (server_uri or "").startswith(
        "ldaps://"
    )
    server = Server(server_uri, use_ssl=use_ssl, get_info=ALL, connect_timeout=5)
    return Connection(
        server, user=cfg.get("LDAP_BIND_DN") or None, password=cfg.get("LDAP_BIND_PASSWORD") or None,
        auto_bind=True, receive_timeout=5,
    )


def count_group_members(group_dn: str) -> int | None:
    """Compte les membres d'un groupe LDAP quelconque (utilisé pour le
    groupe global LDAP_REQUIRE_GROUP_DN comme pour chaque groupe de tenant).
    Retourne None si le groupe n'existe pas ou si la requête échoue
    (réseau/permissions) : dans ce cas l'appelant ne doit PAS bloquer
    l'accès sur une valeur inconnue."""
    if not group_dn:
        return None
    cfg = get_ldap_config()
    if not cfg.get("LDAP_SERVER"):
        return None
    try:
        conn = _ldap_server_conn(cfg)
        # member (groupOfNames/AD) et uniqueMember (groupOfUniqueNames) sont les
        # deux schémas de groupe les plus courants ; memberUid (posixGroup) n'a
        # pas de DN d'entrée donc pas de recherche BASE possible ici.
        conn.search(
            search_base=group_dn, search_filter="(objectClass=*)", search_scope="BASE",
            attributes=["member", "uniqueMember"],
        )
        if not conn.entries:
            conn.unbind()
            return None
        entry = conn.entries[0]
        members = set()
        for attr in ("member", "uniqueMember"):
            if attr in entry:
                members.update(str(v) for v in entry[attr].values)
        conn.unbind()
        return len(members)
    except LDAPException:
        return None


def count_ldap_group_members() -> int | None:
    """Compte les membres du groupe global LDAP_REQUIRE_GROUP_DN — c'est ce
    nombre qui est comparé à max_seats de la licence quand aucun tenant
    n'est configuré (mode mono-groupe historique)."""
    cfg = get_ldap_config()
    return count_group_members(cfg.get("LDAP_REQUIRE_GROUP_DN"))


def _resolve_user_dn(cfg: dict, username: str) -> str | None:
    """Résout le DN d'un utilisateur via le compte de service, sans bind en
    tant qu'utilisateur (pas besoin de son mot de passe) — utilisé pour la
    résolution de tenant après une authentification déjà réussie."""
    safe_username = escape_filter_chars(username)
    user_dn_template = cfg.get("LDAP_USER_DN_TEMPLATE")
    if user_dn_template:
        return user_dn_template.format(username=safe_username)
    if not cfg.get("LDAP_SERVER"):
        return None
    try:
        conn = _ldap_server_conn(cfg)
        search_filter = (cfg.get("LDAP_SEARCH_FILTER") or "(uid={username})").format(username=safe_username)
        conn.search(search_base=cfg.get("LDAP_BASE_DN"), search_filter=search_filter, attributes=["cn"])
        dn = conn.entries[0].entry_dn if len(conn.entries) == 1 else None
        conn.unbind()
        return dn
    except LDAPException:
        return None


def get_user_tenant(username: str) -> dict | None:
    """Renvoie le premier tenant (db.list_ldap_tenants()) dont le groupe
    LDAP contient cet utilisateur, ou None si aucun tenant n'est configuré
    ou si l'utilisateur n'appartient à aucun groupe de tenant. Un
    utilisateur dans plusieurs groupes de tenant est rattaché au premier
    trouvé (ordre alphabétique par nom, voir db.list_ldap_tenants)."""
    import db
    tenants = db.list_ldap_tenants()
    if not tenants:
        return None
    cfg = get_ldap_config()
    if not cfg.get("LDAP_SERVER"):
        return None
    user_dn = _resolve_user_dn(cfg, username)
    if not user_dn:
        return None
    try:
        conn = _ldap_server_conn(cfg)
        for tenant in tenants:
            conn.search(
                search_base=tenant["group_dn"],
                search_filter=f"(member={escape_filter_chars(user_dn)})",
                attributes=["cn"], search_scope="BASE",
            )
            if len(conn.entries) > 0:
                conn.unbind()
                return tenant
        conn.unbind()
        return None
    except LDAPException:
        return None
