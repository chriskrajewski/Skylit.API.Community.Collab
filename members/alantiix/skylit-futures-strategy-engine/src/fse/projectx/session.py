"""The ProjectX session: API-key login and the bearer token (design §24, Req 1.9 and 24.7).

Source: https://gateway.docs.projectx.com/docs/getting-started/authenticate/authenticate-api-key/
(wire format in :mod:`fse.projectx.models`; content rephrased for compliance
with licensing restrictions).

- **Credentials come only from the EnvView (Req 24.7).** The username and API
  key are read from ``PROJECTX_USERNAME`` and ``PROJECTX_API_KEY`` at login
  time. Nothing here takes a credential as an argument, so neither the
  Strategy_Config nor the command line can supply one. A blank variable stops
  the login before any request is sent.
- **Redaction (Req 1.9).** The session registers the username and API key with
  the Redactor before the login request, and each session token the moment it
  is decoded, before it is stored or used, on success and on failure alike.
- **Exit 3.** Any login failure raises :class:`ProjectXLoginError`, whose
  ``exit_code`` is 3 (design "Exit codes": ProjectX login failure). A refused
  request with a fresh token raises :class:`ProjectXAuthError`, also exit 3.
  Messages are built from constants, variable names, HTTP statuses and
  documented error-code names only. The server's ``errorMessage`` is never
  copied, because usernames are matched without regard to case and an echoed
  username in another case would slip past the Redactor. Exceptions from httpx
  are not chained (``from None``).
- **Token reuse.** A token is valid for 24 hours and a new login does not
  revoke one in use, so the session logs in once, reuses the token, and logs in
  again when it is 23 hours old or after a ProjectX 401 (:meth:`ProjectXSession.refresh`).
  Logins are serialized, so concurrent callers share one login.
- **One host.** The token is only ever sent to the session's own HTTPS base URL;
  :class:`~fse.projectx.bars.ProjectXBars` uses the session's client and URL.
"""

from __future__ import annotations

import asyncio
import json
from typing import ClassVar, Final

import httpx

from fse.clock import Clock
from fse.logio.redact import Redactor
from fse.projectx.models import LOGIN_ERROR_NAMES, LoginResponse, MalformedResponseError
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "API_KEY_VARIABLE",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_S",
    "EXIT_CREDENTIALS",
    "LOGIN_PATH",
    "TOKEN_REFRESH_AGE_NS",
    "USERNAME_VARIABLE",
    "ProjectXAuthError",
    "ProjectXLoginError",
    "ProjectXSession",
]

# Design "Exit codes": 3 = credentials or access, which includes a ProjectX login failure.
EXIT_CREDENTIALS: Final = 3

DEFAULT_BASE_URL: Final = "https://api.topstepx.com"
"""The TopstepX Gateway API URL. Other firms publish their own connection URLs."""

LOGIN_PATH: Final = "/api/Auth/loginKey"
USERNAME_VARIABLE: Final = "PROJECTX_USERNAME"
API_KEY_VARIABLE: Final = "PROJECTX_API_KEY"

DEFAULT_TIMEOUT_S: Final = 60.0
TOKEN_REFRESH_AGE_NS: Final = 23 * 3600 * NS_PER_SECOND
"""Log in again once a token is this old; ProjectX tokens are valid for 24 hours."""

_CHECK: Final = f"check {USERNAME_VARIABLE} and {API_KEY_VARIABLE}"


class ProjectXAuthError(Exception):
    """ProjectX refused the credentials or the access. The message holds no secret value."""

    exit_code: ClassVar[int] = EXIT_CREDENTIALS


class ProjectXLoginError(ProjectXAuthError):
    """The ProjectX login could not be attempted or did not return a token."""


class ProjectXSession:
    """Logs in with the EnvView credentials and hands out the current session token."""

    __slots__ = (
        "_base_url",
        "_clock",
        "_env",
        "_http",
        "_issued_at",
        "_lock",
        "_logins",
        "_redactor",
        "_refresh_age_ns",
        "_timeout_s",
        "_token",
    )

    def __init__(
        self,
        env: EnvView,
        redactor: Redactor,
        http: httpx.AsyncClient,
        clock: Clock,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        refresh_age_ns: int = TOKEN_REFRESH_AGE_NS,
    ) -> None:
        """``redactor`` is the Log_Writer's (``writer.redactor``); the caller owns ``http``."""
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s}")
        if isinstance(refresh_age_ns, bool) or refresh_age_ns <= 0:
            raise ValueError(f"refresh_age_ns must be a positive int, got {refresh_age_ns!r}")
        self._base_url = _check_base_url(base_url)
        self._env = env
        self._redactor = redactor
        self._http = http
        self._clock = clock
        self._timeout_s = timeout_s
        self._refresh_age_ns = refresh_age_ns
        self._token: str | None = None
        self._issued_at: Instant = 0
        self._logins = 0
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    @property
    def logins(self) -> int:
        """Successful logins so far."""
        return self._logins

    def url(self, path: str) -> str:
        """The absolute URL of ``path`` (``/api/...``) on this session's host."""
        if not path.startswith("/"):
            raise ValueError("a ProjectX path must start with '/'")
        return self._base_url + path

    def missing_credentials(self) -> tuple[str, ...]:
        """The names of the blank credential variables, in a fixed order."""
        names = (USERNAME_VARIABLE, API_KEY_VARIABLE)
        return tuple(name for name in names if self._env.get(name) is None)

    @staticmethod
    def auth_headers(token: str) -> dict[str, str]:
        """Request headers that carry ``token`` as a bearer token."""
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def token(self) -> str:
        """The current token, logging in first when there is none or it is due for renewal."""
        async with self._lock:
            if self._token is not None and not self._due():
                return self._token
            return await self._login()

    async def login(self) -> str:
        """Log in now, even with a current token, and return the new token."""
        async with self._lock:
            return await self._login()

    async def refresh(self, stale: str) -> str:
        """A token to use after ProjectX refused ``stale`` with HTTP 401.

        Logs in again unless another caller already replaced ``stale`` with a
        token that is not yet due for renewal.
        """
        async with self._lock:
            if self._token is not None and self._token != stale and not self._due():
                return self._token
            return await self._login()

    def _due(self) -> bool:
        return self._clock.now() - self._issued_at >= self._refresh_age_ns

    async def _login(self) -> str:
        """POST ``loginKey``. Call with the lock held."""
        username = self._env.get(USERNAME_VARIABLE)
        api_key = self._env.get(API_KEY_VARIABLE)
        if username is None or api_key is None:
            missing = self.missing_credentials()
            verb, pronoun = ("is", "it") if len(missing) == 1 else ("are", "them")
            raise ProjectXLoginError(
                f"{' and '.join(missing)} {verb} blank; set {pronoun} in the shell environment "
                "or the Project .env file. No ProjectX login request was sent."
            )
        # Normally already registered (Redactor.from_env); repeated here so a
        # bare Redactor still hides both values.
        self._redactor.add(username)
        self._redactor.add(api_key)
        self._token = None
        issued_at = self._clock.now()  # before the request: the token can only be younger
        try:
            response = await self._http.post(
                self.url(LOGIN_PATH),
                json={"userName": username, "apiKey": api_key},
                headers={"Accept": "application/json"},
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProjectXLoginError(
                f"ProjectX login failed: no response within {self._timeout_s:g} s "
                f"({type(exc).__name__})"
            ) from None
        except (httpx.HTTPError, httpx.StreamError) as exc:
            raise ProjectXLoginError(f"ProjectX login failed: {type(exc).__name__}") from None

        body = _json_body(response)
        self._register_token(body)
        status = response.status_code
        if not 200 <= status <= 299:
            raise ProjectXLoginError(f"ProjectX login was refused with HTTP {status}; {_CHECK}")
        try:
            parsed = LoginResponse.parse(body)
        except MalformedResponseError:
            raise ProjectXLoginError(
                "ProjectX login returned a response without the documented fields"
            ) from None
        if not parsed.success or parsed.error_code != 0 or parsed.token is None:
            raise ProjectXLoginError(f"ProjectX login failed: {_failure_label(parsed)}; {_CHECK}")
        self._token = parsed.token
        self._issued_at = issued_at
        self._logins += 1
        return parsed.token

    def _register_token(self, body: object) -> None:
        """Register any token in ``body`` with the Redactor before anything else reads it."""
        if isinstance(body, dict):
            token = body.get("token")
            if isinstance(token, str):
                self._redactor.add(token)  # a blank value is ignored

    def __repr__(self) -> str:
        # Never show the token or the credentials.
        return f"ProjectXSession(base_url={self._base_url!r}, has_token={self._token is not None})"


def _json_body(response: httpx.Response) -> object:
    """The decoded body, or ``None`` when it is not JSON (the decode error is dropped)."""
    try:
        return json.loads(response.content)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError keep the body text
        return None


def _failure_label(parsed: LoginResponse) -> str:
    if parsed.success and parsed.error_code == 0:
        return "no token in the response"
    name = LOGIN_ERROR_NAMES.get(parsed.error_code)
    label = f"errorCode {parsed.error_code}"
    return f"{label} ({name})" if name else label


def _check_base_url(base_url: str) -> str:
    """``base_url`` without a trailing ``/``; HTTPS, a host, and nothing that could leak."""
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL:
        raise ValueError("the ProjectX base URL is not a valid URL") from None
    if url.scheme != "https" or not url.host:
        raise ValueError("the ProjectX base URL must be an https:// URL with a host")
    if url.userinfo or url.query or url.fragment:
        raise ValueError("the ProjectX base URL must not hold user info, a query or a fragment")
    return str(url).rstrip("/")
