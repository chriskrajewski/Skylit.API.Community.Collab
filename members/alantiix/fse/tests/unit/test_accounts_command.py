"""``fse accounts``: the read-only ProjectX account lookup.

Every request goes to a respx gateway (no network); every value is fake.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest
import respx

from fse import cli
from fse.commands.accounts import EXIT_BROKER, JSON_KIND, lookup
from fse.logio import LogWriter, Redactor
from fse.projectx.session import API_KEY_VARIABLE, EXIT_CREDENTIALS, USERNAME_VARIABLE
from fse.secrets.env import EnvView
from fse.timekit import parse_rfc3339
from tests.fakes.clock import FakeClock
from tests.fakes.projectx_gateway import BASE, TOKEN, FakeGateway

T0 = parse_rfc3339("2026-03-02T14:35:00Z")
CREDS = {USERNAME_VARIABLE: "fake-projectx-user-0000", API_KEY_VARIABLE: "fake-projectx-key-0000"}
BOTH = {"PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "202"}
ACCOUNT_NAME = "50KTC-SKU-V2-DLL-243531-88267128"


@dataclass(frozen=True)
class Outcome:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


@pytest.fixture
def gateway(respx_router: respx.MockRouter) -> FakeGateway:
    gw = FakeGateway()
    gw.install(respx_router)
    return gw


async def accounts(values: dict[str, str], *, as_json: bool = False, creds: bool = True) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    writer = LogWriter(Redactor(list(CREDS.values())), stdout=out, stderr=err)
    env = EnvView({**(CREDS if creds else {}), **values}, {})
    clock = FakeClock(T0)
    code = await clock.run(lookup(env, writer, clock, as_json=as_json, base_url=BASE))
    result = Outcome(code, out.getvalue(), err.getvalue())
    for secret in (*CREDS.values(), TOKEN):
        assert secret not in result.text
    return result


async def json_line(values: dict[str, str]) -> dict[str, object]:
    result = await accounts(values, as_json=True)
    assert result.code == 0
    assert result.err == ""
    lines = result.out.splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


@pytest.mark.asyncio
async def test_table_lists_accounts_with_roles(gateway: FakeGateway) -> None:
    result = await accounts(BOTH)
    assert result.code == 0
    rows = [line.split() for line in result.out.splitlines()]
    assert rows[0] == ["ID", "NAME", "CAN", "TRADE", "ROLE"]
    assert rows[1:] == [
        ["101", "FAKE-PRACTICE", "yes", "practice"],
        ["202", "FAKE-COMBINE", "yes", "combine"],
    ]
    assert result.err.splitlines() == [
        "accounts: PRACTICE_ACCOUNT_ID and COMBINE_ACCOUNT_ID match listed accounts"
    ]
    assert [path for path, _ in gateway.calls] == ["Auth/loginKey", "Account/search"]
    assert not gateway.order_paths()


@pytest.mark.asyncio
async def test_table_with_no_accounts(gateway: FakeGateway) -> None:
    gateway.accounts = []
    result = await accounts(BOTH)
    assert result.code == 0
    assert result.out.splitlines()[-1] == "no active accounts"
    assert "warning: PRACTICE_ACCOUNT_ID matches no account" in result.err


@pytest.mark.asyncio
async def test_json_line(gateway: FakeGateway) -> None:
    parsed = await json_line(BOTH)
    assert parsed == {
        "kind": JSON_KIND,
        "accounts": [
            {"id": "101", "name": "FAKE-PRACTICE", "can_trade": True, "role": "practice"},
            {"id": "202", "name": "FAKE-COMBINE", "can_trade": True, "role": "combine"},
        ],
        "diagnostics": [],
    }


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {},
            [
                ("unset", "PRACTICE_ACCOUNT_ID", "PRACTICE_ACCOUNT_ID is unset"),
                ("unset", "COMBINE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID is unset"),
            ],
        ),
        (
            {"PRACTICE_ACCOUNT_ID": "PLACEHOLDER_PRACTICE", "COMBINE_ACCOUNT_ID": "202"},
            [("unset", "PRACTICE_ACCOUNT_ID", "PRACTICE_ACCOUNT_ID is unset")],
        ),
        (
            {"PRACTICE_ACCOUNT_ID": ACCOUNT_NAME, "COMBINE_ACCOUNT_ID": "202"},
            [
                (
                    "not_numeric",
                    "PRACTICE_ACCOUNT_ID",
                    "PRACTICE_ACCOUNT_ID is not a numeric account id; "
                    "use the numeric id, not the account name",
                )
            ],
        ),
        (
            {"PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "999"},
            [
                (
                    "no_match",
                    "COMBINE_ACCOUNT_ID",
                    "COMBINE_ACCOUNT_ID matches no account the Broker_Adapter resolved",
                )
            ],
        ),
        (
            {"PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "101"},
            [("same", "PRACTICE_ACCOUNT_ID", "PRACTICE_ACCOUNT_ID equals COMBINE_ACCOUNT_ID")],
        ),
    ],
)
@pytest.mark.asyncio
async def test_diagnostics(
    gateway: FakeGateway, values: dict[str, str], expected: list[tuple[str, str, str]]
) -> None:
    parsed = await json_line(values)
    found = parsed["diagnostics"]
    assert isinstance(found, list)
    assert [(d["code"], d["variable"], d["message"]) for d in found] == expected
    table = await accounts(values)
    assert table.code == 0
    assert table.err.splitlines() == [f"warning: {message}" for _, _, message in expected]
    for value in values.values():
        if not value.isdigit():
            assert value not in json.dumps(parsed)
            assert value not in table.text


@pytest.mark.asyncio
async def test_same_account_has_both_roles(gateway: FakeGateway) -> None:
    parsed = await json_line({"PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "101"})
    found = parsed["accounts"]
    assert isinstance(found, list)
    assert [a["role"] for a in found] == ["practice,combine", ""]
    table = await accounts({"PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "101"})
    assert table.out.splitlines()[2].split()[-1] == "-"


@pytest.mark.asyncio
async def test_missing_credentials_exit_3_without_a_request(gateway: FakeGateway) -> None:
    result = await accounts(BOTH, creds=False)
    assert result.code == EXIT_CREDENTIALS == 3
    assert result.out == ""
    assert result.err.splitlines() == [
        "error: PROJECTX_USERNAME and PROJECTX_API_KEY are blank; "
        "set them in Settings -> Secrets or the Project .env"
    ]
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_gateway_down_exits_4(gateway: FakeGateway) -> None:
    gateway.down = True
    result = await accounts(BOTH, as_json=True)
    assert result.code == EXIT_BROKER == 4
    assert result.out == ""
    assert result.err.startswith("error: cannot list the ProjectX accounts: /api/Account/search")
    assert {path for path, _ in gateway.calls} <= {"Auth/loginKey", "Account/search"}
    assert not gateway.order_paths()


def test_cli_help() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(["accounts", "--help"], project_dir=None, environ={}, stdout=out, stderr=err)
    assert code == 0
    assert "--json" in out.getvalue()
    sub = next(a for a in cli.build_parser()._actions if a.dest == "command")
    assert sub.choices is not None
    assert "accounts" in sub.choices
