"""Login (OIDC authorization code + PKCE), server-side sessions and CSRF.

- ``/auth/login`` stores ``state``, ``nonce`` and the PKCE verifier (single use,
  10 minutes) and redirects to the Cognito Hosted UI.
- ``/auth/callback`` exchanges the code with the client secret, verifies the ID
  token against the issuer's JWKS (signature, ``iss``, ``aud``, ``exp``,
  ``nonce``, ``token_use``) and opens a session.
- The session cookie carries a random token; only its SHA-256 is stored.
- Every mutation must send the session's CSRF token in ``X-FSE-CSRF``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt
from fastapi import HTTPException, Request, WebSocket

from fse_ui.db import Db
from fse_ui.settings import OidcSettings, Settings

PENDING_TTL_S = 600
JWKS_MIN_REFRESH_S = 60
CSRF_HEADER = "X-FSE-CSRF"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEV_USER = ("dev-operator", "operator@localhost")


class AuthError(Exception):
    """A login step failed; the message is safe to show (no token content)."""


SERVICE_SCOPES = frozenset({"cards:read", "cards:draft", "cards:backtest"})
_CARD_ID = r"[A-Za-z0-9_-]{1,80}"


@dataclass(frozen=True, slots=True)
class Principal:
    sub: str
    email: str
    csrf: str
    session_hash: str
    kind: Literal["session", "service"] = "session"
    scopes: frozenset[str] = frozenset()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


class JwksCache:
    """The issuer's signing keys by ``kid``; refetched on an unknown ``kid`` (rotation)."""

    def __init__(self, url: str, client: httpx.AsyncClient) -> None:
        self._url = url
        self._client = client
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0

    async def key(self, kid: str) -> jwt.PyJWK:
        if kid not in self._keys and time.monotonic() - self._fetched_at >= JWKS_MIN_REFRESH_S:
            await self._refresh()
        if kid not in self._keys and not self._keys:
            await self._refresh()
        try:
            return self._keys[kid]
        except KeyError:
            raise AuthError("the ID token is signed with an unknown key") from None

    async def _refresh(self) -> None:
        try:
            response = await self._client.get(self._url, timeout=10)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthError(f"cannot read the issuer keys ({type(exc).__name__})") from None
        keys: dict[str, jwt.PyJWK] = {}
        for item in data.get("keys", []):
            kid = item.get("kid")
            if isinstance(kid, str) and item.get("kty") == "RSA":
                keys[kid] = jwt.PyJWK(item)
        self._keys = keys
        self._fetched_at = time.monotonic()


class AuthService:
    def __init__(self, settings: Settings, db: Db, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._db = db
        self._client = client
        self._jwks = None if settings.oidc is None else JwksCache(settings.oidc.jwks_url, client)

    @property
    def redirect_uri(self) -> str:
        return f"{self._settings.public_url}/auth/callback"

    def _oidc(self) -> OidcSettings:
        if self._settings.oidc is None:
            raise AuthError("OIDC login is not configured")
        return self._settings.oidc

    # ------------------------------------------------------------ login

    def start_login(self) -> str:
        """The Hosted UI URL for a new login; its state is stored for the callback."""
        oidc = self._oidc()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        now = time.time()
        self._db.execute("DELETE FROM auth_pending WHERE created_at < ?", (now - PENDING_TTL_S,))
        self._db.execute(
            "INSERT INTO auth_pending (state, nonce, verifier, created_at) VALUES (?, ?, ?, ?)",
            (state, nonce, verifier, now),
        )
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": oidc.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": oidc.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{oidc.authorize_url}?{query}"

    async def complete_login(self, code: str, state: str) -> str:
        """Exchange ``code``, verify the ID token, open a session; the raw session token."""
        oidc = self._oidc()
        with self._db.tx() as c:
            row = c.execute(
                "SELECT nonce, verifier, created_at FROM auth_pending WHERE state = ?", (state,)
            ).fetchone()
            c.execute("DELETE FROM auth_pending WHERE state = ?", (state,))  # single use
        if row is None or time.time() - row["created_at"] > PENDING_TTL_S:
            raise AuthError("the login state is unknown, used or expired; log in again")
        try:
            response = await self._client.post(
                oidc.token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": oidc.client_id,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": row["verifier"],
                },
                auth=(oidc.client_id, oidc.client_secret),
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"the token request failed ({type(exc).__name__})") from None
        if response.status_code != 200:
            raise AuthError(f"the token request was refused (HTTP {response.status_code})")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise AuthError("the token response has no ID token")
        claims = await self.verify_id_token(id_token, nonce=row["nonce"])
        return self.create_session(str(claims["sub"]), str(claims.get("email", claims["sub"])))

    async def verify_id_token(self, token: str, *, nonce: str) -> dict[str, Any]:
        oidc = self._oidc()
        assert self._jwks is not None
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("the ID token is malformed") from None
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthError("the ID token uses an unexpected algorithm or has no key id")
        key = await self._jwks.key(header["kid"])
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=oidc.client_id,
                issuer=oidc.issuer,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"the ID token is invalid: {type(exc).__name__}") from None
        if claims.get("token_use", "id") != "id":
            raise AuthError("the token is not an ID token")
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise AuthError("the ID token nonce does not match this login")
        return claims

    def logout_url(self) -> str | None:
        oidc = self._settings.oidc
        if oidc is None or oidc.logout_url is None:
            return None
        query = urllib.parse.urlencode(
            {"client_id": oidc.client_id, "logout_uri": f"{self._settings.public_url}/"}
        )
        return f"{oidc.logout_url}?{query}"

    # ------------------------------------------------------------ sessions

    def create_session(self, sub: str, email: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self._db.execute(
            "INSERT INTO sessions (id_hash, sub, email, csrf, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _hash(token),
                sub,
                email,
                secrets.token_urlsafe(24),
                now,
                now + self._settings.session_ttl_s,
            ),
        )
        return token

    def lookup(self, token: str | None) -> Principal | None:
        if not token:
            return None
        row = self._db.one(
            "SELECT id_hash, sub, email, csrf, expires_at FROM sessions WHERE id_hash = ?",
            (_hash(token),),
        )
        if row is None or row["expires_at"] < time.time():
            return None
        return Principal(row["sub"], row["email"], row["csrf"], row["id_hash"])

    def end_session(self, principal: Principal) -> None:
        self._db.execute("DELETE FROM sessions WHERE id_hash = ?", (principal.session_hash,))

    # ------------------------------------------------------------ service tokens

    def mint_service_token(
        self,
        who: Principal,
        name: str,
        scopes: Sequence[str],
        ttl_s: int | None,
    ) -> tuple[str, dict[str, Any]]:
        """A new bearer token. The raw value is returned once; only its hash is stored."""
        unknown = sorted(set(scopes) - SERVICE_SCOPES)
        if unknown or not scopes:
            raise ValueError("scopes must be cards:read, cards:draft, cards:backtest")
        token = "fse_" + secrets.token_urlsafe(32)
        now = time.time()
        expires = None if ttl_s is None else now + ttl_s
        digest = _hash(token)
        self._db.execute(
            "INSERT INTO service_tokens "
            "(id_hash, name, sub, email, scopes, created_at, expires_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (digest, name, who.sub, who.email, json.dumps(sorted(set(scopes))), now, expires),
        )
        return token, {
            "id_hash": digest,
            "name": name,
            "scopes": sorted(set(scopes)),
            "created_at": now,
            "expires_at": expires,
        }

    def lookup_service(self, token: str | None) -> Principal | None:
        if not token or not token.startswith("fse_"):
            return None
        row = self._db.one(
            "SELECT id_hash, sub, email, scopes, expires_at, revoked_at "
            "FROM service_tokens WHERE id_hash = ?",
            (_hash(token),),
        )
        if row is None or row["revoked_at"] is not None:
            return None
        expires = row["expires_at"]
        if expires is not None and float(expires) < time.time():
            return None
        try:
            scopes = frozenset(json.loads(row["scopes"]))
        except (TypeError, ValueError):
            return None
        if not scopes <= SERVICE_SCOPES:
            return None
        return Principal(
            row["sub"], row["email"], "", row["id_hash"], kind="service", scopes=scopes
        )

    def list_service_tokens(self) -> list[dict[str, Any]]:
        rows = self._db.all(
            "SELECT id_hash, name, sub, email, scopes, created_at, expires_at, revoked_at "
            "FROM service_tokens ORDER BY created_at DESC"
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                scopes = json.loads(row["scopes"])
            except (TypeError, ValueError):
                scopes = []
            out.append(
                {
                    "id_hash": row["id_hash"],
                    "name": row["name"],
                    "sub": row["sub"],
                    "email": row["email"],
                    "scopes": scopes,
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "revoked_at": row["revoked_at"],
                }
            )
        return out

    def revoke_service_token(self, id_hash: str) -> bool:
        now = time.time()
        changed = self._db.execute(
            "UPDATE service_tokens SET revoked_at = ? "
            "WHERE id_hash = ? AND revoked_at IS NULL",
            (now, id_hash),
        )
        return changed > 0


# ---------------------------------------------------------------- dependencies


def _service(request: Request | WebSocket) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


_JOB_ID = r"[A-Za-z0-9]{8,40}"


def _reads_cards(scopes: frozenset[str]) -> bool:
    return "cards:read" in scopes or "cards:backtest" in scopes


def service_allowed(method: str, path: str, scopes: frozenset[str]) -> bool:
    """Card drafts, card backtests, and card reads. Nothing else, including minting.

    A token that can read or backtest a card may also read that card and the
    job it launched (``GET /api/cards/{id}``, its ``/job`` proxy, and
    ``GET /api/jobs/{id}`` plus ``/log``). The jobs routes still refuse a job
    that no card owns.
    """
    path = path.rstrip("/") or "/"
    if method == "GET" and _reads_cards(scopes):
        if re.fullmatch(rf"/api/cards/{_CARD_ID}(?:/job(?:/log)?)?", path):
            return True
        if re.fullmatch(rf"/api/jobs/{_JOB_ID}(?:/log)?", path):
            return True
    if method == "GET" and "cards:read" in scopes:
        if path in {"/api/cards", "/api/schemas/card"}:
            return True
        if re.fullmatch(rf"/api/cards/{_CARD_ID}/(?:render|report)", path):
            return True
    draft = path in {"/api/cards", "/api/cards/validate"}
    if method == "POST" and "cards:draft" in scopes and draft:
        return True
    if method == "POST" and "cards:backtest" in scopes:
        return re.fullmatch(rf"/api/cards/{_CARD_ID}/(?:backtest|judge)", path) is not None
    return False


def principal(request: Request) -> Principal:
    """The logged-in operator, or a service token on the card routes.

    A bearer token is checked on its own: a bad one is 401 and does not fall
    through to the session cookie. Bearer calls skip CSRF. Cookie mutations
    still need ``X-FSE-CSRF``.
    """
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() == "bearer":
        found = _service(request).lookup_service(raw.strip())
        if found is None:
            raise HTTPException(status_code=401, detail="invalid service token")
        request.state.principal = found
        if not service_allowed(request.method, request.url.path, found.scopes):
            raise HTTPException(
                status_code=403, detail="service token is not allowed on this path"
            )
        return found
    settings: Settings = request.app.state.settings
    found = _service(request).lookup(request.cookies.get(settings.cookie_name))
    if found is None:
        raise HTTPException(status_code=401, detail="not logged in or the session expired")
    request.state.principal = found  # the audit row names the operator even on a 403
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, found.csrf):
            raise HTTPException(status_code=403, detail="missing or wrong CSRF token")
    return found


def websocket_principal(ws: WebSocket) -> Principal | None:
    """The operator of a WebSocket, or ``None`` (wrong Origin or no session)."""
    settings: Settings = ws.app.state.settings
    if ws.headers.get("origin", "") != settings.public_url:
        return None
    return _service(ws).lookup(ws.cookies.get(settings.cookie_name))
