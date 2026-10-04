"""Unit tests for ``fse.projectx.session``: login, token redaction and exit 3.

Every request is mocked with respx and every credential is a fake value from
the ``fake_secrets`` fixture; nothing reaches ProjectX. Time is virtual.

**Validates: Requirements 1.9, 24.7**
"""

from __future__ import annotations

import io
import json
import traceback
from collections.abc import Mapping

import httpx
import pytest
import respx

from fse import cli
from fse.logio import REDACTED, LogWriter, Redactor
from fse.projectx.session import (
    API_KEY_VARIABLE,
    EXIT_CREDENTIALS,
    LOGIN_PATH,
    TOKEN_REFRESH_AGE_NS,
    USERNAME_VARIABLE,
    ProjectXLoginError,
    ProjectXSession,
)
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND
from tests.conftest import FakeEnv
from tests.fakes.clock import FakeClock

BASE = "https://api.projectx.invalid"
LOGIN_URL = BASE + LOGIN_PATH
T0 = 1_772_492_400 * NS_PER_SECOND
TOKEN = "fake-session-token-0000"
TOKEN_2 = "fake-session-token-0001"


def ok(token: str = TOKEN) -> httpx.Response:
    return httpx.Response(
        200, json={"token": token, "success": True, "errorCode": 0, "errorMessage": None}
    )


def env_of(values: Mapping[str, str]) -> EnvView:
    return EnvView(dict(values), {})


def secrets_of(fake: FakeEnv, *tokens: str) -> list[str]:
    return [fake.values[USERNAME_VARIABLE], fake.values[API_KEY_VARIABLE], *tokens]


def assert_no_secret(text: str, secrets: list[str]) -> None:
    for value in secrets:
        assert value not in text


def full_text(exc: BaseException) -> str:
    """Everything an uncaught error would print: message, repr and traceback."""
    return f"{exc}\n{exc!r}\n{''.join(traceback.format_exception(exc))}"


# ---------------------------------------------------------------- success


@pytest.mark.asyncio
async def test_login_uses_env_credentials_and_registers_the_token(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv
) -> None:
    route = respx_router.post(LOGIN_URL).mock(return_value=ok())
    redactor = Redactor()  # bare: the session must register every value itself
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(
            env_of(fake_secrets.values), redactor, http, FakeClock(T0), base_url=BASE
        )
        assert await session.token() == TOKEN
    request = route.calls.last.request
    assert json.loads(request.content) == {
        "userName": fake_secrets.values[USERNAME_VARIABLE],
        "apiKey": fake_secrets.values[API_KEY_VARIABLE],
    }
    assert "authorization" not in request.headers
    for value in secrets_of(fake_secrets, TOKEN):
        assert redactor.redact(f"x {value} y") == f"x {REDACTED} y"
    assert TOKEN not in repr(session)
    assert session.logins == 1


@pytest.mark.asyncio
async def test_log_writer_output_hides_the_session_token(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=ok())
    env = env_of(fake_secrets.values)
    out, err = io.StringIO(), io.StringIO()
    writer = LogWriter.from_env(env, stdout=out, stderr=err)
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, writer.redactor, http, FakeClock(T0), base_url=BASE)
        token = await session.token()
    writer.echo(f"token={token}")
    writer.error(json.dumps(ProjectXSession.auth_headers(token)))
    assert TOKEN not in out.getvalue() + err.getvalue()
    assert REDACTED in out.getvalue()


@pytest.mark.asyncio
async def test_token_is_reused_until_due_then_renewed(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv
) -> None:
    route = respx_router.post(LOGIN_URL).mock(side_effect=[ok(TOKEN), ok(TOKEN_2)])
    clock = FakeClock(T0)
    redactor = Redactor()
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env_of(fake_secrets.values), redactor, http, clock, base_url=BASE)
        assert await session.token() == TOKEN
        clock.advance_to(T0 + TOKEN_REFRESH_AGE_NS - 1)
        assert await session.token() == TOKEN
        assert route.call_count == 1
        clock.advance_to(T0 + TOKEN_REFRESH_AGE_NS)
        assert await session.token() == TOKEN_2
    assert route.call_count == 2
    assert redactor.redact(TOKEN_2) == REDACTED


@pytest.mark.asyncio
async def test_refresh_logs_in_again_only_for_the_current_token(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv
) -> None:
    route = respx_router.post(LOGIN_URL).mock(side_effect=[ok(TOKEN), ok(TOKEN_2)])
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(
            env_of(fake_secrets.values), Redactor(), http, FakeClock(T0), base_url=BASE
        )
        await session.token()
        assert await session.refresh(TOKEN) == TOKEN_2
        # A caller still holding the first token gets the renewed one, no new login.
        assert await session.refresh(TOKEN) == TOKEN_2
    assert route.call_count == 2


# ---------------------------------------------------------------- blank credentials


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blank", "expected"),
    [
        ((USERNAME_VARIABLE,), f"{USERNAME_VARIABLE} is blank"),
        ((API_KEY_VARIABLE,), f"{API_KEY_VARIABLE} is blank"),
        ((USERNAME_VARIABLE, API_KEY_VARIABLE), f"{USERNAME_VARIABLE} and {API_KEY_VARIABLE} are"),
    ],
)
async def test_blank_credentials_send_no_request(
    respx_router: respx.MockRouter,
    fake_secrets: FakeEnv,
    blank: tuple[str, ...],
    expected: str,
) -> None:
    route = respx_router.post(LOGIN_URL).mock(return_value=ok())
    values = {k: v for k, v in fake_secrets.values.items() if k not in blank}
    values.update(dict.fromkeys(blank, "   "))  # whitespace counts as blank
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env_of(values), Redactor(), http, FakeClock(T0), base_url=BASE)
        with pytest.raises(ProjectXLoginError) as info:
            await session.token()
    assert route.call_count == 0
    assert expected in str(info.value)
    assert info.value.exit_code == EXIT_CREDENTIALS == 3
    assert_no_secret(full_text(info.value), secrets_of(fake_secrets))


# ---------------------------------------------------------------- failures exit 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(
                200,
                json={
                    "token": "fake-echo-token-0000",
                    "success": False,
                    "errorCode": 3,
                    "errorMessage": "fake-projectx-user-0000 FAKE-PROJECTX-USER-0000",
                },
            ),
            "errorCode 3 (InvalidCredentials)",
        ),
        (
            httpx.Response(
                200,
                json={"token": None, "success": False, "errorCode": 7, "errorMessage": "sign"},
            ),
            "errorCode 7 (AgreementsNotSigned)",
        ),
        (
            httpx.Response(200, json={"token": None, "success": False, "errorCode": 42}),
            "errorCode 42;",
        ),
        (
            httpx.Response(200, json={"token": "", "success": True, "errorCode": 0}),
            "no token",
        ),
        (httpx.Response(400, json={"errors": {"apiKey": ["required"]}}), "HTTP 400"),
        (httpx.Response(500, text="fake-projectx-key-0000"), "HTTP 500"),
        (httpx.Response(200, text="<html>fake-session-token-0000</html>"), "documented fields"),
        (httpx.Response(200, json={"token": TOKEN}), "documented fields"),
    ],
)
async def test_login_failure_exits_3_without_secrets(
    respx_router: respx.MockRouter,
    fake_secrets: FakeEnv,
    response: httpx.Response,
    expected: str,
) -> None:
    respx_router.post(LOGIN_URL).mock(return_value=response)
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(
            env_of(fake_secrets.values), Redactor(), http, FakeClock(T0), base_url=BASE
        )
        with pytest.raises(ProjectXLoginError) as info:
            await session.token()
    exc = info.value
    assert expected in str(exc)
    assert exc.exit_code == 3
    assert exc.__cause__ is None
    # Usernames match without regard to case, so no case of a secret may appear.
    secrets = secrets_of(fake_secrets, TOKEN, "fake-echo-token-0000")
    assert_no_secret(full_text(exc).lower(), [s.lower() for s in secrets])


@pytest.mark.asyncio
async def test_token_in_a_failed_login_body_is_registered_too(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv
) -> None:
    echo = "fake-echo-token-0000"
    respx_router.post(LOGIN_URL).mock(
        return_value=httpx.Response(200, json={"token": echo, "success": False, "errorCode": 3})
    )
    env = env_of(fake_secrets.values)
    err = io.StringIO()
    writer = LogWriter.from_env(env, stdout=io.StringIO(), stderr=err)
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, writer.redactor, http, FakeClock(T0), base_url=BASE)
        with pytest.raises(ProjectXLoginError) as info:
            await session.token()
    writer.error(f"{echo} {fake_secrets.values[USERNAME_VARIABLE]}")
    writer.exception(info.value)
    assert echo not in err.getvalue()
    assert_no_secret(err.getvalue(), secrets_of(fake_secrets, echo))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("fake-projectx-key-0000"), "ConnectError"),
        (httpx.ReadTimeout("fake-projectx-user-0000"), "ReadTimeout"),
    ],
)
async def test_transport_failure_exits_3_without_chaining(
    respx_router: respx.MockRouter,
    fake_secrets: FakeEnv,
    error: Exception,
    expected: str,
) -> None:
    respx_router.post(LOGIN_URL).mock(side_effect=error)
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(
            env_of(fake_secrets.values), Redactor(), http, FakeClock(T0), base_url=BASE
        )
        with pytest.raises(ProjectXLoginError) as info:
            await session.token()
    assert expected in str(info.value)
    assert info.value.exit_code == 3
    assert info.value.__suppress_context__
    assert_no_secret(full_text(info.value), secrets_of(fake_secrets))


def test_login_error_maps_to_the_cli_credentials_status() -> None:
    assert ProjectXLoginError.exit_code == cli.EXIT_CREDENTIALS


# ---------------------------------------------------------------- construction


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.projectx.invalid",
        "https://user:pass@api.projectx.invalid",
        "https://api.projectx.invalid/?key=1",
        "not a url",
    ],
)
async def test_base_url_must_be_plain_https(base_url: str) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="base URL"):
            ProjectXSession(EnvView({}, {}), Redactor(), http, FakeClock(T0), base_url=base_url)


@pytest.mark.asyncio
async def test_trailing_slash_is_dropped() -> None:
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(
            EnvView({}, {}), Redactor(), http, FakeClock(T0), base_url=BASE + "/"
        )
    assert session.url(LOGIN_PATH) == LOGIN_URL
