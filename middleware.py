"""Middlewares transverses : en-têtes de sécurité HTTP et limitation de débit.

Séparés d'app.py (déjà volumineux) et volontairement autonomes : aucune
dépendance à la logique métier, juste Starlette. Les deux sont montés dans
app.py au démarrage.
"""
import hashlib
import os
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


# ── En-têtes de sécurité ──────────────────────────────────────────────────────

# 'unsafe-inline' reste nécessaire (script ET style) : l'UI est un seul fichier
# HTML avec de gros blocs <script>/<style> inline et des gestionnaires onclick —
# passer à un CSP à nonce imposerait de tout réécrire. Le gain principal du CSP
# est ailleurs et acquis ici : plus AUCUNE origine tierce autorisée (tout est
# servi en local, voir static/vendor), donc un CDN compromis ne peut plus
# injecter de script, et 'unsafe-inline' ne concerne que du code same-origin
# déjà livré par nous. frame-ancestors 'none' = anti-clickjacking.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # HSTS seulement quand on sait qu'on est derrière du TLS (COOKIE_SECURE) —
    # sinon on épinglerait du HTTPS sur un déploiement HTTP de test/dev.
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, hsts: bool = False):
        super().__init__(app)
        self._headers = dict(_SECURITY_HEADERS)
        if hsts:
            self._headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for k, v in self._headers.items():
            response.headers.setdefault(k, v)
        return response


# ── Limitation de débit ───────────────────────────────────────────────────────

RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").strip().lower() != "false"

# Fenêtre glissante de 60 s, plafonds par catégorie de route. Généreux là où
# c'est de l'usage interactif légitime (extension = un dev qui code), strict là
# où un abus coûte cher (LLM) ou vise l'auth (login). Surchargeable par .env
# pour un déploiement à forte charge.
_WINDOW_SECONDS = 60
_LIMITS = {
    "auth": int(os.environ.get("RATE_LIMIT_AUTH_PER_MIN", "10")),      # /api/login : complète le lockout par compte
    "llm": int(os.environ.get("RATE_LIMIT_LLM_PER_MIN", "40")),        # appels IA + fichiers (coûteux)
    "default": int(os.environ.get("RATE_LIMIT_DEFAULT_PER_MIN", "150")),
}

# Jamais limité : supervision (healthcheck Docker + monitoring client) et les
# assets statiques servis en local (polices, JS vendored, favicon...) — les
# limiter casserait le chargement de l'UI derrière un seul point de sortie NAT.
_EXEMPT_PREFIXES = ("/healthz", "/vendor/", "/favicon", "/download/extension")
_EXEMPT_SUFFIXES = (".css", ".js", ".woff2", ".woff", ".svg", ".png", ".ico", ".map", ".html")

_LLM_MARKERS = ("/api/extension/chat", "/api/files/", "/api/file-anon/", "/anonymize-preview")


def _category(path: str, method: str) -> str | None:
    if path.startswith(_EXEMPT_PREFIXES) or path.endswith(_EXEMPT_SUFFIXES):
        return None
    if path == "/" or path == "/index.html" or path == "/login.html":
        return None
    if path == "/api/login":
        return "auth"
    if any(m in path for m in _LLM_MARKERS):
        return "llm"
    # messages d'une conversation (envoi + stream) : coûteux (appel IA)
    if path.startswith("/api/conversations/") and path.rstrip("/").endswith(("messages", "stream")):
        return "llm"
    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fenêtre glissante en mémoire (process unique, comme le lockout login).
    Identité du client : clé API > cookie de session > IP. Fail-open : un bug
    du limiteur ne doit jamais bloquer une requête légitime — sécurité par
    disponibilité d'abord, ce n'est pas un contrôle anti-DDoS (ça, c'est le
    rôle du reverse proxy/WAF en amont), juste un garde-fou anti-abus applicatif."""

    def __init__(self, app):
        super().__init__(app)
        # (category, client_id) -> deque[timestamps]
        self._hits: dict[tuple, deque] = {}
        self._last_gc = time.monotonic()

    def _client_id(self, request) -> str:
        api_key = request.headers.get("x-tv-api-key")
        if api_key:
            return "k:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
        cookie = request.cookies.get("anon_session")
        if cookie:
            return "s:" + hashlib.sha256(cookie.encode()).hexdigest()[:16]
        client = request.client
        return "i:" + (client.host if client else "unknown")

    def _gc(self, now: float):
        # purge périodique des seaux vides pour ne pas fuir de la mémoire sur
        # des clients ponctuels (une IP vue une fois ne doit pas rester en dict).
        if now - self._last_gc < 120:
            return
        cutoff = now - _WINDOW_SECONDS
        empty = [k for k, dq in self._hits.items() if not dq or dq[-1] < cutoff]
        for k in empty:
            del self._hits[k]
        self._last_gc = now

    async def dispatch(self, request, call_next):
        if not RATE_LIMIT_ENABLED:
            return await call_next(request)
        try:
            category = _category(request.url.path, request.method)
            if category is None:
                return await call_next(request)
            limit = _LIMITS[category]
            now = time.monotonic()
            self._gc(now)
            key = (category, self._client_id(request))
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            cutoff = now - _WINDOW_SECONDS
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry = max(1, int(_WINDOW_SECONDS - (now - dq[0])))
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Trop de requêtes. Réessaie dans {retry}s."},
                    headers={"Retry-After": str(retry)},
                )
            dq.append(now)
        except Exception:
            # fail-open : jamais bloquer une requête à cause du limiteur lui-même
            return await call_next(request)
        return await call_next(request)
