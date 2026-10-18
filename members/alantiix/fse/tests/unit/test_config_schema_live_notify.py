"""Unit tests for the ``live`` and ``notify`` Config_Schema sections (design §23-25).

Defaults from the design sketch, the Req 23-25 ranges, quoted times, strict
types and unknown keys rejected. Inputs are plain dicts, as a YAML safe loader
produces them.

**Validates: Requirements 17.1, 17.2, 17.5**
"""

from __future__ import annotations

from datetime import time
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from fse.config.schema.live import LiveConfig
from fse.config.schema.notify import NotifyConfig

DESIGN_LIVE_YAML = """
refresh_interval_s: 5
mode: polling
live_max_snapshot_age_s: 30
levels_compare_interval_s: 60
run_window: {start: "09:00", end: "16:00"}
stop_confirm_s: 5
lookup_timeout_s: 5
outage_s: 30
ignored_instruments: [MGC, SIL]
expected_contracts: {MES: CON.F.US.MES.Z26, MNQ: CON.F.US.MNQ.Z26}
"""

DESIGN_NOTIFY_YAML = """
sinks: [console, file]
interval_min: 5
premarket: "09:00"
alerts_2r: true
narrator: {enabled: false, base_url: null, model: null, timeout_s: 10, max_chars: 1500}
"""


def error_types(model: type[BaseModel], value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        model.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_live_defaults_match_the_design_sketch() -> None:
    assert LiveConfig.model_validate(yaml.safe_load(DESIGN_LIVE_YAML)) == LiveConfig()
    cfg = LiveConfig()
    assert (cfg.run_window.start, cfg.run_window.end) == (time(9, 0), time(16, 0))
    assert cfg.ignored_instruments == ("MGC", "SIL")
    assert cfg.expected_contracts.for_instrument("ES") is None
    dumped = cfg.model_dump(mode="python")
    assert dumped["run_window"] == {"start": "09:00", "end": "16:00"}
    assert dumped["ignored_instruments"] == ["MGC", "SIL"]


def test_notify_defaults_match_the_design_sketch() -> None:
    assert NotifyConfig.model_validate(yaml.safe_load(DESIGN_NOTIFY_YAML)) == NotifyConfig()
    cfg = NotifyConfig()
    assert cfg.premarket == time(9, 0)
    assert cfg.model_dump(mode="python")["premarket"] == "09:00"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"refresh_interval_s": 4}, [(("refresh_interval_s",), "greater_than_equal")]),
        ({"refresh_interval_s": 5.0}, [(("refresh_interval_s",), "int_type")]),
        ({"mode": "websocket"}, [(("mode",), "literal_error")]),
        ({"stop_confirm_s": 31}, [(("stop_confirm_s",), "less_than_equal")]),
        ({"lookup_timeout_s": 0}, [(("lookup_timeout_s",), "greater_than_equal")]),
        ({"outage_s": 4}, [(("outage_s",), "greater_than_equal")]),
        ({"outage_s": 301}, [(("outage_s",), "less_than_equal")]),
        (
            {"run_window": {"start": "16:00", "end": "16:00"}},
            [(("run_window", "end"), "greater_than")],
        ),
        ({"run_window": {"start": 540}}, [(("run_window", "start"), "time_type")]),
        ({"run_window": {"start": "9:00"}}, [(("run_window", "start"), "time_parsing")]),
        ({"ignored_instruments": ["MGC", "MGC"]}, [(("ignored_instruments",), "duplicate_id")]),
        (
            {"ignored_instruments": ["mgc"]},
            [(("ignored_instruments", 0), "string_pattern_mismatch")],
        ),
        (
            {"expected_contracts": {"MES": "not an id"}},
            [(("expected_contracts", "MES"), "string_pattern_mismatch")],
        ),
        (
            {"expected_contracts": {"MGC": None}},
            [(("expected_contracts", "MGC"), "extra_forbidden")],
        ),
        ({"poll": True}, [(("poll",), "extra_forbidden")]),
    ],
)
def test_live_rejects(value: dict[str, Any], expected: list[tuple[Any, ...]]) -> None:
    assert error_types(LiveConfig, value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"sinks": []}, [(("sinks",), "too_short")]),
        ({"sinks": ["console", "console"]}, [(("sinks",), "duplicate_id")]),
        ({"sinks": ["email"]}, [(("sinks", 0), "literal_error")]),
        ({"interval_min": 0}, [(("interval_min",), "greater_than_equal")]),
        ({"interval_min": 61}, [(("interval_min",), "less_than_equal")]),
        ({"premarket": "09:30"}, [(("premarket",), "less_than")]),
        ({"alerts_2r": "yes"}, [(("alerts_2r",), "bool_type")]),
        ({"narrator": {"timeout_s": 61}}, [(("narrator", "timeout_s"), "less_than_equal")]),
        ({"narrator": {"max_chars": 0}}, [(("narrator", "max_chars"), "greater_than_equal")]),
        (
            {"narrator": {"model": "bad model"}},
            [(("narrator", "model"), "string_pattern_mismatch")],
        ),
    ],
)
def test_notify_rejects(value: dict[str, Any], expected: list[tuple[Any, ...]]) -> None:
    assert error_types(NotifyConfig, value) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@llm.invalid/v1",  # user info
        "https://llm.invalid/v1?key=abc",  # query string
        "https://llm.invalid/v1#frag",
        "ftp://llm.invalid",
        "llm.invalid/v1",
    ],
)
def test_narrator_base_url_cannot_carry_a_credential(url: str) -> None:
    assert error_types(NotifyConfig, {"narrator": {"base_url": url}}) == [
        (("narrator", "base_url"), "string_pattern_mismatch")
    ]


@pytest.mark.parametrize("url", ["https://llm.invalid/v1", "http://localhost:11434"])
def test_narrator_base_url_accepts_plain_endpoints(url: str) -> None:
    cfg = NotifyConfig.model_validate(
        {"narrator": {"enabled": True, "base_url": url, "model": "llama3.1:8b"}}
    )
    assert cfg.narrator.base_url == url
