"""OIDC login against a fake issuer (local RSA key, JWKS and token endpoint mocked)."""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import fse_ui.auth
from fse_ui.app import create_app
from fse_ui.settings import OidcSettings
from tests.conftest import ORIGIN, make_settings

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
CLIENT_ID = "client-123"
DOMAIN = "https://fse-test.auth.us-east-1.amazoncognito.com"


class FakeIssuer:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.claims: dict[str, Any] = {}
        self.sign_with: Any = self.key
        self.token_status = 200
        self.token_calls: list[httpx.Request] = []

    def jwks(self) -> dict[str, Any]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "user-1",
            "email": "op@example.com",
            "iat": now,
            "exp": now + 3600,
            "token_use": "id",
            "nonce": nonce,
            **self.claims,
        }
        return jwt.encode(claims, self.sign_with, algorithm="RS256", headers={"kid": self.kid})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json=self.jwks())
        if request.url.path == "/oauth2/token":
            self.token_calls.append(request)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "id_token": self.id_token(self.nonce),
                    "access_token": "a",
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(404)

    nonce = ""


@pytest.fixture
def issuer() -> FakeIssuer:
    return FakeIssuer()


@pytest.fixture
def client(home: Path, issuer: FakeIssuer) -> Any:
    oidc = OidcSettings(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="s3cret",
        authorize_url=f"{DOMAIN}/oauth2/authorize",
        token_url=f"{DOMAIN}/oauth2/token",
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        logout_url=f"{DOMAIN}/logout",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    app = create_app(make_settings(home, dev_auth=False, oidc=oidc), http_client=http)
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def _start(client: TestClient, issuer: FakeIssuer) -> dict[str, str]:
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 303
    url = urllib.parse.urlparse(r.headers["location"])
    assert url.netloc == urllib.parse.urlparse(DOMAIN).netloc
    q = dict(urllib.parse.parse_qsl(url.query))
    assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
    assert q["redirect_uri"] == f"{ORIGIN}/auth/callback"
    issuer.nonce = q["nonce"]
    return q


def test_login_flow_opens_a_session(client: TestClient, issuer: FakeIssuer) -> None:
    q = _start(client, issuer)
    r = client.get(f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False)
    assert r.status_code == 303, r.text
    assert "httponly" in r.headers["set-cookie"].lower()
    body = issuer.token_calls[0].content.decode()
    assert "code_verifier=" in body and "code=abc" in body
    assert issuer.token_calls[0].headers["authorization"].startswith("Basic ")
    me = client.get("/api/session").json()
    assert me["user"]["email"] == "op@example.com"


def test_replayed_state_is_rejected(client: TestClient, issuer: FakeIssuer) -> None:
    q = _start(client, issuer)
    assert (
        client.get(
            f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False
        ).status_code
        == 303
    )
    client.cookies.clear()
    r = client.get(f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False)
    assert r.status_code == 400
    assert "unknown, used or expired" in r.json()["detail"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"aud": "someone-else"}, "InvalidAudienceError"),
        ({"iss": "https://evil.example.com"}, "InvalidIssuerError"),
        ({"exp": int(time.time()) - 3600}, "ExpiredSignatureError"),
        ({"token_use": "access"}, "not an ID token"),
        ({"nonce": "wrong"}, "nonce does not match"),
    ],
)
def test_bad_claims_are_rejected(
    client: TestClient, issuer: FakeIssuer, change: dict[str, Any], message: str
) -> None:
    q = _start(client, issuer)
    issuer.claims = change
    r = client.get(f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False)
    assert r.status_code == 400
    assert message in r.json()["detail"]
    assert client.get("/api/session").status_code == 401


def test_bad_signature_is_rejected(client: TestClient, issuer: FakeIssuer) -> None:
    q = _start(client, issuer)
    issuer.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    r = client.get(f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False)
    assert r.status_code == 400
    assert "InvalidSignatureError" in r.json()["detail"]


def test_refused_token_request(client: TestClient, issuer: FakeIssuer) -> None:
    q = _start(client, issuer)
    issuer.token_status = 400
    r = client.get(f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False)
    assert r.status_code == 400 and "HTTP 400" in r.json()["detail"]


def _logged_in(client: TestClient, issuer: FakeIssuer) -> str:
    q = _start(client, issuer)
    client.get(f"/auth/callback?code=abc&state={q['state']}", follow_redirects=False)
    csrf: str = client.get("/api/session").json()["csrf"]
    return csrf


def test_mutations_need_the_csrf_token(client: TestClient, issuer: FakeIssuer) -> None:
    csrf = _logged_in(client, issuer)
    body = {"command": "fse scan-secrets", "values": {}}
    assert client.post("/api/jobs", json=body).status_code == 403
    assert client.post("/api/jobs", json=body, headers={"X-FSE-CSRF": "nope"}).status_code == 403
    audit = client.get("/api/audit").json()
    assert audit[0]["result"] == "http 403" and audit[0]["user"] == "op@example.com"
    r = client.post("/auth/logout", headers={"X-FSE-CSRF": csrf})
    assert r.status_code == 200
    assert r.json()["logout_url"].startswith(f"{DOMAIN}/logout?client_id={CLIENT_ID}")
    assert client.get("/api/session").status_code == 401


def test_websocket_needs_session_and_origin(client: TestClient, issuer: FakeIssuer) -> None:
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as no_session,
        client.websocket_connect("/api/ws/live", headers={"origin": ORIGIN}) as ws,
    ):
        ws.receive_json()
    assert no_session.value.code == 4401
    _logged_in(client, issuer)
    # The browser sends the session cookie on the upgrade; the test client needs it spelled out.
    cookie = f"fse_session={client.cookies.get('fse_session')}"
    with (
        pytest.raises(WebSocketDisconnect) as bad_origin,
        client.websocket_connect(
            "/api/ws/live", headers={"origin": "https://evil.example.com", "cookie": cookie}
        ) as ws,
    ):
        ws.receive_json()
    assert bad_origin.value.code == 4401
    with client.websocket_connect(
        "/api/ws/live", headers={"origin": ORIGIN, "cookie": cookie}
    ) as ws:
        snap = ws.receive_json()
    assert "blocks" in snap and "runner" in snap


PAPER_GETS = [
    "/api/paper/plugins",
    "/api/paper/accounts",
    *(f"/api/paper/plugins/alpha/{w}" for w in ("trades", "equity", "metrics")),
    *(f"/api/paper/accounts/topstep_a/{w}" for w in ("trades", "equity", "metrics")),
    *(f"/api/paper/accounts/implicit:alpha/{w}" for w in ("trades", "equity", "metrics")),
    *(f"/api/paper/total/{w}" for w in ("trades", "equity", "metrics")),
]


def test_paper_routes_need_a_session(client: TestClient) -> None:
    existing_get = client.get("/api/live").status_code
    existing_post = client.post("/api/halt", json={"reason": "x"}).status_code
    assert existing_get == 401
    for url in PAPER_GETS:
        assert client.get(url).status_code == existing_get, url
    r = client.post("/api/paper/accounts/topstep_a/reset", json={"reason": "x"})
    assert r.status_code == existing_post


class _Clock:
    """A wall clock the test moves by hand (monotonic stays real for the JWKS cache)."""

    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()


def test_session_expiry(home: Path, issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch) -> None:
    # A fake clock, not sleep: on a loaded CI runner the login alone can take
    # longer than a 1 s session, which made this test flaky.
    clock = _Clock()
    monkeypatch.setattr(fse_ui.auth, "time", clock)
    oidc = OidcSettings(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="x",
        authorize_url=f"{DOMAIN}/oauth2/authorize",
        token_url=f"{DOMAIN}/oauth2/token",
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    app = create_app(
        make_settings(home, dev_auth=False, oidc=oidc, session_ttl_s=60), http_client=http
    )
    with TestClient(app, base_url=ORIGIN) as c:
        _logged_in(c, issuer)
        assert c.get("/api/session").status_code == 200
        clock.now += 59
        assert c.get("/api/session").status_code == 200
        clock.now += 2
        assert c.get("/api/session").status_code == 401
