"""Unit tests for ``fse.logio.redact``: forms, overlaps, ``add()`` and edge cases.

Fake values only. Property 2 (redaction completeness) is a separate property
test; these are focused examples.

**Validates: Requirements 1.9**
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest

from fse.logio.canonical_json import JsonValue
from fse.logio.redact import REDACTED, Redactor
from fse.secrets.env import load_env
from tests.conftest import FakeEnv

KEY = "fake-skylit-key-0000"
UNSAFE = 'fake key/with?unsafe&chars=1+"\\\u00e9'  # space / ? & = + " \ and a non-ASCII char


def _lower_hex(text: str) -> str:
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), text)


# ---------------------------------------------------------------- forms


def test_raw_value_is_replaced_everywhere() -> None:
    r = Redactor([KEY])
    assert r.redact(f"key={KEY} again {KEY}.") == f"key={REDACTED} again {REDACTED}."


def test_text_without_secrets_is_unchanged() -> None:
    text = "no secrets here: 100% plain, a+b, C:\\path"
    assert Redactor([KEY]).redact(text) == text
    assert Redactor().redact(text) == text


@pytest.mark.parametrize(
    "encode",
    [
        lambda v: quote(v, safe=""),
        lambda v: quote(v),  # "/" left as is
        lambda v: quote_plus(v, safe=""),  # space as "+"
        lambda v: _lower_hex(quote(v, safe="")),
        lambda v: quote(v, safe="/?&=+"),  # partly encoded
    ],
    ids=["quote", "quote-path", "quote-plus", "lower-hex", "partial"],
)
def test_url_encoded_forms_are_replaced(encode: Callable[[str], str]) -> None:
    encoded = encode(UNSAFE)
    assert encoded != UNSAFE
    out = Redactor([UNSAFE]).redact(f"GET /hook?u={encoded}&x=1")
    assert out == f"GET /hook?u={REDACTED}&x=1"


def test_json_escaped_form_is_replaced_and_json_stays_valid() -> None:
    value = 'quote"back\\slash\nnew\u00e9'
    text = json.dumps({"k": f"<{value}>"})  # ensure_ascii: \" \\ \n \u00e9
    assert value not in text
    out = Redactor([value]).redact(text)
    assert json.loads(out) == {"k": f"<{REDACTED}>"}


def test_repr_escaped_form_is_replaced() -> None:
    value = "tab\tq'uote\"\x01"
    text = f"ValueError: bad value {value!r}"
    out = Redactor([value]).redact(text)
    assert out == f"ValueError: bad value '{REDACTED}'"


# ---------------------------------------------------------------- overlaps


@pytest.mark.parametrize("values", [["key", "key-extended"], ["key-extended", "key"]])
def test_longest_value_wins_whatever_the_registration_order(values: list[str]) -> None:
    assert Redactor(values).redact("a=key-extended;b=key") == f"a={REDACTED};b={REDACTED}"


def test_overlapping_values_leave_no_fragment() -> None:
    r = Redactor(["abcd", "cdef"])
    assert r.redact("x abcdef y") == f"x {REDACTED} y"


def test_self_overlapping_occurrences_merge_but_adjacent_ones_do_not() -> None:
    r = Redactor(["aa", "keyk"])
    assert r.redact("aaa") == REDACTED
    assert r.redact("keykkeyk") == REDACTED * 2


def test_occurrence_formed_at_a_marker_edge_is_replaced() -> None:
    # "zz" -> "[REDACTED]", which then meets "q" to form the value "]q".
    r = Redactor(["zz", "]q"])
    out = r.redact("zzq")
    assert "]q" not in out
    assert "zz" not in out
    assert REDACTED[:-1] in out


def test_value_inside_the_marker_terminates() -> None:
    assert Redactor(["ACT"]).redact("xACTx") == f"x{REDACTED}x"


# ---------------------------------------------------------------- registration


def test_add_registers_a_session_token() -> None:
    r = Redactor([KEY])
    token = "fake.session.token-0000"
    assert r.redact(f"Bearer {token}") == f"Bearer {token}"
    r.add(token)
    assert r.redact(f"Bearer {token} {KEY}") == f"Bearer {REDACTED} {REDACTED}"


def test_blank_values_are_ignored() -> None:
    r = Redactor(["", "   "])
    r.add("\t\n")
    assert r.redact("a b\tc") == "a b\tc"
    assert repr(r) == "Redactor(0 values)"


def test_padded_value_is_also_registered_stripped() -> None:
    r = Redactor(["  fake-padded-0000 "])
    assert r.redact("x=fake-padded-0000;") == f"x={REDACTED};"


def test_non_str_values_are_rejected() -> None:
    with pytest.raises(TypeError, match="must be a str"):
        Redactor().add(1234)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="can only redact a str"):
        Redactor().redact(b"bytes")  # type: ignore[arg-type]


def test_repr_never_shows_values() -> None:
    r = Redactor([KEY, UNSAFE])
    assert KEY not in repr(r)
    assert repr(r) == "Redactor(2 values)"


def test_from_env_covers_every_secret_variable(fake_secrets: FakeEnv, tmp_path: Path) -> None:
    r = Redactor.from_env(load_env(tmp_path))
    values = fake_secrets.secret_values()
    out = r.redact(" | ".join(values))
    assert out == " | ".join([REDACTED] * len(values))


# ---------------------------------------------------------------- JSON values


def test_redact_json_covers_keys_and_values() -> None:
    r = Redactor([KEY])
    value: JsonValue = {"a": [KEY, 1, 2.5, None, True], KEY: {"b": f"x{KEY}"}, "c": "plain"}
    assert r.redact_json(value) == {
        "a": [REDACTED, 1, 2.5, None, True],
        REDACTED: {"b": f"x{REDACTED}"},
        "c": "plain",
    }


def test_redact_json_keeps_keys_that_collide_after_redaction() -> None:
    r = Redactor(["fake-a-0000", "fake-b-0000"])
    out = r.redact_json({"fake-a-0000": 1, "fake-b-0000": 2})
    assert out == {REDACTED: 1, f"{REDACTED}#2": 2}
