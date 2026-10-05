"""Property 82: Narrator input and fallback.

*For any* Finding_Card and configured secrets, the Narrator input contains
only card fields, no secret value and no broker account id; and for any
Narrator outcome (error, empty, over-length, timeout), the message sent is the
unchanged code-built card with the narration-unavailable note.

Each example draws Secret_Variable values (URL-unsafe characters included),
two broker account ids (set as ``PRACTICE_ACCOUNT_ID`` and
``COMBINE_ACCOUNT_ID``, which are not Secret_Variables here), and a
Finding_Card whose text fields may embed a secret and whose ``notes`` may
embed an account id. The Notifier, with the Narrator on, delivers the card's
message. The Narrator endpoint is a respx route (``assert_all_mocked``) that
records the request and answers with the drawn outcome: prose within the cap,
an HTTP error status, a network error, a malformed reply, empty or blank
prose, prose over ``max_chars``, no reply within ``timeout_s`` (on a fake
clock), or no ``base_url`` configured.

Checked: the user message is the card's allow-listed fields
(:data:`~fse.notify.finding_card.NARRATOR_FIELDS`, so no ``notes``) after
redaction, the system message is ``prompts/narrator.md``, and the request
body holds no secret value (raw or URL-encoded) and no account id. With
prose, the one message sent holds the prose and the unchanged card; on any
other outcome it is the unchanged card plus the narration-unavailable note,
and the failure is logged.

**Validates: Requirements 25.10, 25.12**
"""

from __future__ import annotations

import asyncio
import io
import json
import string
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from urllib.parse import quote

import httpx
import respx
from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.notify import NarratorConfig, NotifyConfig
from fse.engine.step import KingFlip
from fse.logio import LogWriter, Redactor
from fse.notify.finding_card import (
    NARRATOR_FIELDS,
    UNAVAILABLE,
    FindingCard,
    LevelField,
    MapField,
    OrderField,
    PositionField,
    SetupField,
    WatchField,
    render_card,
)
from fse.notify.narrator import NARRATION_UNAVAILABLE, load_prompt
from fse.notify.notifier import NOTIFIER_LOG_FILE_NAME, Message, Notifier
from fse.secrets.env import SECRET_VARIABLES, EnvView
from fse.timekit import NS_PER_SECOND
from tests.fakes.clock import FakeClock

BASE: Final = "https://narrator.invalid/v1"
ENDPOINT: Final = f"{BASE}/chat/completions"
START: Final = 1_772_460_000 * NS_PER_SECOND
OUTCOMES: Final = (
    "prose",
    "http_error",
    "network_error",
    "malformed",
    "empty",
    "too_long",
    "timeout",
    "no_base_url",
)
_WORD_CHARS: Final = string.ascii_letters + " _-:."
_SECRET_CHARS: Final = string.ascii_lowercase + string.digits + "-_/:.%+=&?~ "


# ---------------------------------------------------------------- strategies


def secret_values() -> st.SearchStrategy[list[str]]:
    """Distinct secret values, URL-unsafe characters included."""
    one = st.text(_SECRET_CHARS, min_size=6, max_size=20).map(lambda s: f"sk{s}x")
    return st.lists(one, min_size=1, max_size=len(SECRET_VARIABLES) - 1, unique=True)


def account_ids() -> st.SearchStrategy[tuple[str, str]]:
    one = st.integers(100_000_000, 999_999_999).map(str)
    return st.tuples(one, one).filter(lambda ids: ids[0] != ids[1])


@st.composite
def cards(draw: st.DrawFn, secrets: list[str], ids: tuple[str, str]) -> FindingCard:
    """A Finding_Card whose text may hold a secret and whose notes may hold an account id."""
    plain = st.text(_WORD_CHARS, max_size=12)

    def text(*extra: str) -> st.SearchStrategy[str]:
        inner = st.sampled_from((*secrets, *extra))
        return plain | st.builds(lambda a, s, b: a + s + b, plain, inner, plain)

    price = st.integers(0, 80_000).map(lambda q: q / 4)
    shown = price | st.integers(0, 50) | st.just(UNAVAILABLE)
    level = st.builds(LevelField, shown, shown)
    maps = st.builds(
        MapField,
        text(),
        st.sampled_from(("gamma", "vanna")),
        text(),
        level,
        level,
        level,
        st.lists(level, max_size=2).map(tuple) | st.just(UNAVAILABLE),
        st.lists(st.tuples(level, level), max_size=2).map(tuple) | st.just(UNAVAILABLE),
    )
    order = st.builds(
        OrderField,
        text(),
        text(),
        st.sampled_from(("buy", "sell")),
        st.sampled_from(("limit", "stop", "market")),
        st.integers(1, 10),
        price | st.just("market"),
        text(),
    )
    flip = st.builds(KingFlip, text(), st.sampled_from(("gamma", "vanna")), price, price)
    return FindingCard(
        kind=draw(st.sampled_from(("decision", "premarket"))),
        t=draw(st.integers(START, START + 400 * 86_400 * NS_PER_SECOND)),
        order_mode=draw(st.sampled_from(("paper", "practice", "combine"))),
        map=tuple(draw(st.lists(maps, max_size=3))),
        spots=tuple(draw(st.lists(st.tuples(text(), shown), max_size=3))),
        king_flips=tuple(draw(st.lists(flip, max_size=2))),
        regime=draw(text()),
        map_grade=draw(text()),
        trinity=tuple(draw(st.lists(st.tuples(text(), text()), max_size=3))),
        setups=tuple(
            draw(
                st.lists(
                    st.builds(
                        SetupField,
                        text(),
                        st.sampled_from(("A_Plus", "Alert_2R", "Pass")),
                        st.lists(text(), max_size=3).map(tuple),
                    ),
                    max_size=5,
                )
            )
        ),
        setups_not_listed=draw(st.integers(0, 20)),
        working_orders=tuple(draw(st.lists(order, max_size=3))),
        armed=tuple(draw(st.lists(order, max_size=2))),
        cancelled=tuple(draw(st.lists(text(), max_size=2))),
        positions=tuple(
            draw(st.lists(st.builds(PositionField, text(), text(), shown, shown, shown, shown)))
        ),
        watch=tuple(draw(st.lists(st.builds(WatchField, text(), shown, shown, shown)))),
        notes=tuple(draw(st.lists(text(*ids), max_size=3))),
    )


@st.composite
def cases(draw: st.DrawFn) -> tuple[list[str], tuple[str, str], FindingCard]:
    secrets = draw(secret_values(), label="secrets")
    ids = draw(account_ids(), label="account ids")
    return secrets, ids, draw(cards(secrets, ids), label="card")


# ---------------------------------------------------------------- the run


def _reply(content: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _deliver(
    env: EnvView, cfg: NarratorConfig, card: FindingCard, outcome: str, prose: str, out: Path
) -> tuple[Message, list[httpx.Request], str]:
    """The message the Notifier sends for ``card``, the Narrator requests, the Notifier log."""
    clock = FakeClock(START)
    writer = LogWriter.from_env(env, stdout=io.StringIO(), stderr=io.StringIO())
    notifier = Notifier(NotifyConfig(sinks=("file",), narrator=cfg), env, writer, clock, out)
    requests: list[httpx.Request] = []

    async def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        match outcome:
            case "prose":
                return _reply(prose)
            case "http_error":
                return httpx.Response(500, json={"error": "server"})
            case "network_error":
                raise httpx.ConnectError("refused", request=request)
            case "malformed":
                return httpx.Response(200, json={"choices": []})
            case "empty":
                return _reply(" \n\t" if len(prose) % 2 else "")
            case "too_long":
                return _reply("x" * (cfg.max_chars + 1 + len(prose)))
            case _:
                await clock.sleep((cfg.timeout_s + 1) * NS_PER_SECOND)
                return _reply("late")

    async def main() -> Message:
        try:
            return await clock.run(notifier.deliver(Message.card(card)))
        finally:
            await notifier.aclose()

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(ENDPOINT).mock(side_effect=answer)
        sent = asyncio.run(main())
    log = out / NOTIFIER_LOG_FILE_NAME
    return sent, requests, log.read_text(encoding="utf-8") if log.exists() else ""


def _check_request(
    request: httpx.Request, card: FindingCard, secrets: list[str], ids: tuple[str, str]
) -> None:
    body = request.content.decode("utf-8")
    for value in secrets:
        assert value not in body
        assert quote(value, safe="") not in body
    for account in ids:
        assert account not in body
    messages = json.loads(body)["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == load_prompt()
    fields = json.loads(messages[1]["content"])
    assert set(fields) == set(NARRATOR_FIELDS)
    assert "notes" not in fields
    expected = Redactor(secrets).redact_json(card.to_narrator_fields())
    assert fields == json.loads(json.dumps(expected))


# Feature: skylit-futures-strategy-engine, Property 82: Narrator input and fallback
@given(
    case=cases(),
    outcome=st.sampled_from(OUTCOMES),
    timeout_s=st.integers(1, 60),
    max_chars=st.integers(1, 2_000),
    prose_text=st.text(string.ascii_letters + string.digits + " .,", min_size=1, max_size=80),
    key_set=st.booleans(),
)
def test_narrator_gets_only_redacted_card_fields_and_falls_back_to_the_card(
    case: tuple[list[str], tuple[str, str], FindingCard],
    outcome: str,
    timeout_s: int,
    max_chars: int,
    prose_text: str,
    key_set: bool,
) -> None:
    secrets, ids, card = case
    names = [n for n in SECRET_VARIABLES if key_set or n != "NARRATOR_API_KEY"]
    shell = {n: secrets[i % len(secrets)] for i, n in enumerate(names)}
    shell["PRACTICE_ACCOUNT_ID"], shell["COMBINE_ACCOUNT_ID"] = ids
    env = EnvView(shell, {})
    prose = prose_text.strip()[:max_chars].strip() or "x"
    cfg = NarratorConfig(
        enabled=True,
        base_url=None if outcome == "no_base_url" else BASE,
        model="test-model",
        timeout_s=timeout_s,
        max_chars=max_chars,
    )
    with TemporaryDirectory(prefix="fse-p82-") as tmp:
        sent, requests, log = _deliver(env, cfg, card, outcome, prose, Path(tmp))

    assert len(requests) == (0 if outcome == "no_base_url" else 1)
    for request in requests:
        _check_request(request, card, secrets, ids)
        assert ("authorization" in request.headers) == key_set
    assert sent.kind == f"card:{card.kind}"
    if outcome == "prose":
        assert sent.body == {"narration": prose, "card": card.to_jsonable()}
        assert sent.text == f"{prose}\n\n{render_card(card)}"
        assert "narration_failed" not in log
    else:
        assert sent.body == {
            "narration": None,
            "narration_note": NARRATION_UNAVAILABLE,
            "card": card.to_jsonable(),
        }
        assert sent.text == f"{render_card(card)}\nNarration: {NARRATION_UNAVAILABLE}"
        assert "narration_failed" in log
    event(f"outcome: {outcome}")
