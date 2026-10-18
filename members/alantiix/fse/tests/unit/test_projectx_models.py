"""Unit tests for ``fse.projectx.models``: bar units, wire parsing and normalization.

Fixtures are synthetic. The retrieveBars body mirrors the documented example
shape (newest bar first, ``t`` with an offset).

**Validates: Requirements 1.9, 4.3**
"""

from __future__ import annotations

import pytest

from fse.projectx.models import (
    BarFetchFailure,
    BarUnit,
    LoginResponse,
    MalformedResponseError,
    RetrieveBarsRequest,
    RetrieveBarsResponse,
    bar_unit_for,
    format_instant,
    price_to_ticks,
    to_bar,
)
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, parse_rfc3339

T0 = parse_rfc3339("2026-03-02T23:00:00Z")
FAKE_TOKEN = "fake-session-token-0000"


# ---------------------------------------------------------------- bar units (OQ9)


@pytest.mark.parametrize(
    ("interval_s", "expected"),
    [
        (1, (BarUnit.SECOND, 1)),
        (5, (BarUnit.SECOND, 5)),
        (90, (BarUnit.SECOND, 90)),
        (60, (BarUnit.MINUTE, 1)),
        (300, (BarUnit.MINUTE, 5)),
    ],
)
def test_bar_unit_for_uses_seconds_unless_whole_minutes(
    interval_s: int, expected: tuple[BarUnit, int]
) -> None:
    assert bar_unit_for(interval_s) == expected


def test_bar_unit_values_match_the_docs() -> None:
    assert [int(u) for u in BarUnit] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("interval_s", [0, -5])
def test_bar_unit_for_rejects_non_positive_intervals(interval_s: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        bar_unit_for(interval_s)


def test_bar_unit_for_rejects_a_bool() -> None:
    with pytest.raises(TypeError):
        bar_unit_for(True)


# ---------------------------------------------------------------- formatting and ticks


def test_format_instant_is_utc_with_z() -> None:
    assert format_instant(T0) == "2026-03-02T23:00:00Z"
    assert format_instant(T0 + 250_000_000) == "2026-03-02T23:00:00.250000Z"


@pytest.mark.parametrize(
    ("price", "ticks"),
    [(6065.25, 24261), (6065.0, 24260), (6065.125, 24261), (6065.1, 24260), (6065.2, 24261)],
)
def test_price_to_ticks_rounds_half_up(price: float, ticks: int) -> None:
    assert price_to_ticks(price) == ticks


def test_price_to_ticks_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="finite"):
        price_to_ticks(float("nan"))


def test_request_body_has_the_documented_fields() -> None:
    request = RetrieveBarsRequest(
        contract_id="CON.F.US.MES.H26",
        live=False,
        start_ns=T0,
        end_ns=T0 + 5 * NS_PER_MINUTE,
        unit=BarUnit.SECOND,
        unit_number=5,
        limit=60,
    )
    assert request.to_json() == {
        "contractId": "CON.F.US.MES.H26",
        "live": False,
        "startTime": "2026-03-02T23:00:00Z",
        "endTime": "2026-03-02T23:05:00Z",
        "unit": 1,
        "unitNumber": 5,
        "limit": 60,
        "includePartialBar": False,
    }


# ---------------------------------------------------------------- login body


def test_login_response_parses_and_hides_the_token_in_repr() -> None:
    parsed = LoginResponse.parse(
        {"token": FAKE_TOKEN, "success": True, "errorCode": 0, "errorMessage": None}
    )
    assert (parsed.token, parsed.success, parsed.error_code) == (FAKE_TOKEN, True, 0)
    assert FAKE_TOKEN not in repr(parsed)


def test_login_response_blank_token_is_none() -> None:
    parsed = LoginResponse.parse({"token": "  ", "success": False, "errorCode": 3})
    assert parsed.token is None


@pytest.mark.parametrize(
    "body",
    [
        None,
        [FAKE_TOKEN],
        {"token": 7, "success": True, "errorCode": 0},
        {"token": FAKE_TOKEN, "success": "yes", "errorCode": 0},
        {"token": FAKE_TOKEN, "success": True, "errorCode": "0"},
        {"token": FAKE_TOKEN, "success": True},
    ],
)
def test_malformed_login_body_error_names_no_value(body: object) -> None:
    with pytest.raises(MalformedResponseError) as info:
        LoginResponse.parse(body)
    assert FAKE_TOKEN not in str(info.value)


# ---------------------------------------------------------------- bars body


def _body(*bars: object, success: bool = True, error_code: int = 0) -> dict[str, object]:
    return {"bars": list(bars), "success": success, "errorCode": error_code, "errorMessage": None}


def test_retrieve_bars_response_keeps_received_order() -> None:
    parsed = RetrieveBarsResponse.parse(
        _body(
            {
                "t": "2026-03-02T23:01:00+00:00",
                "o": 6066,
                "h": 6067.5,
                "l": 6065.75,
                "c": 6067.0,
                "v": 12,
            },
            {
                "t": "2026-03-02T18:00:00-05:00",
                "o": 6065.25,
                "h": 6066,
                "l": 6065,
                "c": 6066,
                "v": 30,
            },
        )
    )
    assert [b.t_ns for b in parsed.bars] == [T0 + NS_PER_MINUTE, T0]
    assert parsed.bars[1].o == 6065.25
    assert isinstance(parsed.bars[0].o, float)


def test_failed_bars_body_may_have_null_bars() -> None:
    parsed = RetrieveBarsResponse.parse(
        {"bars": None, "success": False, "errorCode": 1, "errorMessage": "x"}
    )
    assert (parsed.bars, parsed.success, parsed.error_code) == ((), False, 1)


@pytest.mark.parametrize(
    "body",
    [
        "not an object",
        {"bars": None, "success": True, "errorCode": 0},
        _body({"t": "2026-03-02T23:00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}),  # no offset
        _body({"t": "2026-03-02T23:00:00Z", "o": "1", "h": 1, "l": 1, "c": 1, "v": 1}),
        _body({"t": "2026-03-02T23:00:00Z", "o": 1, "h": 1, "l": 1, "c": True, "v": 1}),
        _body({"t": "2026-03-02T23:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1}),
        _body({"t": "2026-03-02T23:00:00Z", "o": float("inf"), "h": 1, "l": 1, "c": 1, "v": 1}),
        _body({"t": "2026-03-02T23:00:00Z", "o": 10**400, "h": 1, "l": 1, "c": 1, "v": 1}),
        _body(["2026-03-02T23:00:00Z", 1, 1, 1, 1, 1]),
    ],
)
def test_malformed_bars_body_is_rejected(body: object) -> None:
    with pytest.raises(MalformedResponseError):
        RetrieveBarsResponse.parse(body)


def test_malformed_timestamp_error_does_not_echo_the_value() -> None:
    secret_like = "fake-not-a-time-0000"
    with pytest.raises(MalformedResponseError) as info:
        RetrieveBarsResponse.parse(
            _body({"t": secret_like, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1})
        )
    assert secret_like not in str(info.value)
    assert info.value.__cause__ is None


def test_to_bar_stamps_open_and_close_and_ticks() -> None:
    wire = RetrieveBarsResponse.parse(
        _body(
            {"t": "2026-03-02T23:00:00Z", "o": 6065.25, "h": 6066.5, "l": 6065, "c": 6066, "v": 9}
        )
    ).bars[0]
    bar = to_bar(wire, instrument="MES", contract="MESH6", interval_s=5)
    assert (bar.open_ns, bar.close_ns) == (T0, T0 + 5 * NS_PER_SECOND)
    assert (bar.o_t, bar.h_t, bar.l_t, bar.c_t) == (24261, 24266, 24260, 24264)
    assert (bar.instrument, bar.contract, bar.interval_s, bar.source) == (
        "MES",
        "MESH6",
        5,
        "projectx",
    )


def test_failure_cause_names_the_one_set_field() -> None:
    assert BarFetchFailure(T0, T0 + 1, status=503).cause == "HTTP 503"
    assert BarFetchFailure(T0, T0 + 1, error_code=1).cause == "errorCode 1"
    assert BarFetchFailure(T0, T0 + 1, error_type="ReadTimeout").cause == "ReadTimeout"
