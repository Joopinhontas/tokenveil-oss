"""Vérification de licence TokenVeil : signature Ed25519 + phone-home anti-piratage.

Le client ne peut PAS forger de licence (pas de clé privée ici, juste la
publique). Le phone-home détecte la copie d'une licence sur 2 instances :
le serveur de licences lie license_id <-> instance_id à la première
vérification réussie et signale "instance_mismatch" si un autre instance_id
se présente ensuite avec le même license_id.
"""
import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger("license")

PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAtegpRvh8mRsXuAEP22jRyYMCmpUmh8nrauKJzQcmPBY=
-----END PUBLIC KEY-----
"""

DATA_DIR = os.environ.get("ANON_DATA_DIR", "data")
LICENSE_FILE = os.path.join(DATA_DIR, "license.lic")
INSTANCE_ID_FILE = os.path.join(DATA_DIR, "instance_id")
NO_LICENSE_SINCE_FILE = os.path.join(DATA_DIR, "no_license_since")

LICENSE_SERVER_URL = os.environ.get("LICENSE_SERVER_URL", "").rstrip("/")
PHONE_HOME_INTERVAL_SECONDS = int(os.environ.get("LICENSE_PHONE_HOME_INTERVAL", str(24 * 3600)))
GRACE_PERIOD_DAYS = int(os.environ.get("LICENSE_GRACE_PERIOD_DAYS", "14"))
NO_LICENSE_GRACE_DAYS = int(os.environ.get("LICENSE_NO_LICENSE_GRACE_DAYS", "15"))

_public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)


MISMATCH_STRIKES_BEFORE_BLOCK = 2


class LicenseState:
    def __init__(self):
        self.payload: dict | None = None
        self.error: str | None = None
        self.phone_home_status: str = "not_checked"
        self.last_phone_home_ok: datetime | None = None
        self.mismatch_strikes: int = 0

    def reload(self):
        token = _read_token()
        if token is None:
            self.payload, self.error = None, "missing"
            return
        try:
            self.payload = _verify_token(token)
            self.error = None
        except (InvalidSignature, ValueError) as e:
            self.payload, self.error = None, str(e) or "invalid_signature"

    @property
    def license_id(self) -> str | None:
        return self.payload["license_id"] if self.payload else None

    @property
    def max_seats(self) -> int:
        return self.payload["max_seats"] if self.payload else 0

    @property
    def is_expired(self) -> bool:
        if not self.payload:
            return True
        return datetime.fromisoformat(self.payload["expires_at"]) < datetime.now(timezone.utc)

    @property
    def is_within_grace_period(self) -> bool:
        """Tolère l'absence RÉSEAU (serveur de licences injoignable, pas de
        signal clair reçu) pendant GRACE_PERIOD_DAYS. Ne s'applique JAMAIS à
        une duplication confirmée (instance_mismatch) : dans ce cas le
        serveur A RÉPONDU, le signal est net, donc pas de grace réseau —
        voir mismatch_strikes / MISMATCH_STRIKES_BEFORE_BLOCK à la place."""
        if self.last_phone_home_ok is None:
            return True
        elapsed = datetime.now(timezone.utc) - self.last_phone_home_ok
        return elapsed.days < GRACE_PERIOD_DAYS

    @property
    def is_valid(self) -> bool:
        if self.payload is None or self.is_expired:
            return False
        if self.phone_home_status == "revoked":
            return False
        # duplication confirmée sur 2 cycles consécutifs (pas juste un blip
        # réseau ponctuel) : blocage immédiat, AUCUNE grace — une licence
        # copiée sur un 2e serveur ne doit pas continuer à tourner pendant
        # 14 jours avant d'être coupée.
        if self.mismatch_strikes >= MISMATCH_STRIKES_BEFORE_BLOCK:
            return False
        return True

    def status_summary(self) -> dict:
        return {
            "valid": self.is_valid,
            "error": self.error,
            "license_id": self.license_id,
            "customer": self.payload.get("customer") if self.payload else None,
            "max_seats": self.max_seats,
            "expires_at": self.payload.get("expires_at") if self.payload else None,
            "phone_home_status": self.phone_home_status,
            "last_phone_home_ok": self.last_phone_home_ok.isoformat() if self.last_phone_home_ok else None,
            "mismatch_strikes": self.mismatch_strikes,
        }


state = LicenseState()


def _read_token() -> str | None:
    env_key = os.environ.get("LICENSE_KEY", "").strip()
    if env_key:
        return env_key
    if os.path.exists(LICENSE_FILE):
        with open(LICENSE_FILE) as f:
            return f.read().strip()
    return None


def _b64decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _verify_token(token: str) -> dict:
    try:
        payload_b64, sig_b64 = token.strip().split(".")
    except ValueError:
        raise ValueError("malformed_token")
    payload_json = _b64decode(payload_b64)
    signature = _b64decode(sig_b64)
    _public_key.verify(signature, payload_json)
    return json.loads(payload_json)


def install_token(token: str):
    """Valide le token AVANT de l'écrire — un admin ne peut pas casser la
    licence en place en collant n'importe quoi : si la signature ou le
    format est invalide, l'ancien data/license.lic reste tel quel."""
    _verify_token(token)  # lève InvalidSignature / ValueError si invalide
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LICENSE_FILE, "w") as f:
        f.write(token.strip())
    state.reload()
    state.phone_home_status = "not_checked"
    state.last_phone_home_ok = None
    state.mismatch_strikes = 0


def uninstall():
    """Retire la licence de CETTE instance (décommissionnement propre avant
    migration vers un nouveau serveur). Ne révoque rien côté fournisseur :
    le license_id reste valable, mais lié à l'ancien instance_id côté
    serveur de licences jusqu'à ce que le fournisseur débloque/relie la
    licence à la nouvelle instance (reset-instance, action vendeur)."""
    if os.path.exists(LICENSE_FILE):
        os.remove(LICENSE_FILE)
    if os.environ.get("LICENSE_KEY"):
        logger.warning(
            "LICENSE_KEY est défini dans l'environnement : la licence sera "
            "toujours active au prochain restart tant que cette variable existe."
        )
    state.reload()
    state.phone_home_status = "not_checked"
    state.last_phone_home_ok = None
    state.mismatch_strikes = 0


def get_or_create_instance_id() -> str:
    if os.path.exists(INSTANCE_ID_FILE):
        with open(INSTANCE_ID_FILE) as f:
            return f.read().strip()
    instance_id = str(uuid.uuid4())
    os.makedirs(os.path.dirname(INSTANCE_ID_FILE), exist_ok=True)
    with open(INSTANCE_ID_FILE, "w") as f:
        f.write(instance_id)
    return instance_id


async def phone_home_once():
    if not LICENSE_SERVER_URL or not state.license_id:
        return
    instance_id = get_or_create_instance_id()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{LICENSE_SERVER_URL}/verify",
                json={"license_id": state.license_id, "instance_id": instance_id},
            )
            resp.raise_for_status()
            result = resp.json()["status"]
        state.phone_home_status = result
        if result == "ok":
            state.last_phone_home_ok = datetime.now(timezone.utc)
            state.mismatch_strikes = 0
        elif result == "instance_mismatch":
            state.mismatch_strikes += 1
            logger.warning(
                "Licence %s utilisée depuis une autre instance que celle enregistrée "
                "(strike %d/%d). Contacter le support si c'est une migration légitime.",
                state.license_id, state.mismatch_strikes, MISMATCH_STRIKES_BEFORE_BLOCK,
            )
        elif result == "revoked":
            logger.error("Licence %s révoquée par le serveur de licences.", state.license_id)
    except Exception as e:
        logger.warning("Phone-home licence indisponible (%s) — grace period en cours.", e)


async def phone_home_loop():
    while True:
        if state.payload:
            await phone_home_once()
        await asyncio.sleep(PHONE_HOME_INTERVAL_SECONDS)


def seats_used() -> int | None:
    """Mode local : nombre de comptes locaux créés. Mode LDAP : somme des
    membres de chaque groupe de tenant configuré (db.list_ldap_tenants()),
    ou repli sur le groupe global LDAP_REQUIRE_GROUP_DN si aucun tenant
    n'est défini. None si injoignable/pas configuré : l'appelant ne doit
    pas bloquer sur une valeur inconnue."""
    import auth
    import db
    if auth.get_auth_backend() == "ldap":
        tenants = db.list_ldap_tenants()
        if tenants:
            total = 0
            for tenant in tenants:
                count = auth.count_group_members(tenant["group_dn"])
                if count is None:
                    return None
                total += count
            return total
        return auth.count_ldap_group_members()
    return len(db.list_local_users())


def seat_available() -> bool:
    if not state.payload:
        return False
    used = seats_used()
    if used is None:
        return True
    return used < state.max_seats


def over_seat_limit() -> bool:
    """True seulement si on CONNAÎT le compte et qu'il dépasse la limite —
    jamais True sur une valeur inconnue (LDAP injoignable)."""
    if not state.payload:
        return False
    used = seats_used()
    return used is not None and used > state.max_seats


def grace_exhausted() -> bool:
    """True quand il n'y a pas de licence valide ET que le délai de grâce
    (NO_LICENSE_GRACE_DAYS, persisté sur disque pour survivre aux restarts)
    est dépassé. Le compteur démarre au premier constat d'absence/invalidité
    de licence et se réinitialise dès qu'une licence valide est réinstallée."""
    if state.is_valid:
        if os.path.exists(NO_LICENSE_SINCE_FILE):
            os.remove(NO_LICENSE_SINCE_FILE)
        return False

    if os.path.exists(NO_LICENSE_SINCE_FILE):
        with open(NO_LICENSE_SINCE_FILE) as f:
            since = datetime.fromisoformat(f.read().strip())
    else:
        since = datetime.now(timezone.utc)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(NO_LICENSE_SINCE_FILE, "w") as f:
            f.write(since.isoformat())

    return (datetime.now(timezone.utc) - since).days >= NO_LICENSE_GRACE_DAYS
