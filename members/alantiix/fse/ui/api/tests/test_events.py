"""Every log kind the fse runner core writes has a plain-English sentence (fse_ui.events)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fse_ui.events import EVENTS, describe

PROJECT = Path(__file__).resolve().parents[3]
CORE = [PROJECT / "src" / "fse" / "live", PROJECT / "src" / "fse" / "projectx"]

_PATTERNS = [
    re.compile(r'"kind":\s*"([a-z_]+)"'),
    re.compile(r'_entry\(\s*"([a-z_]+)"'),
    re.compile(r'\.fail\(\s*"([a-z_]+)"'),
    re.compile(r'_log_once\([^)]*?"([a-z_]+)",\s*"(?:info|warning|error)"'),
    re.compile(r'"event":\s*"([a-z_]+)"'),
]


def core_kinds() -> set[str]:
    found: set[str] = set()
    for root in CORE:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pattern in _PATTERNS:
                found.update(pattern.findall(text))
    return found


def test_every_core_kind_has_a_sentence() -> None:
    kinds = core_kinds()
    assert len(kinds) > 40  # the scan works
    missing = sorted(kinds - set(EVENTS))
    assert missing == [], f"add these kinds to fse_ui/events.py: {missing}"


@pytest.mark.parametrize(
    ("entry", "text"),
    [
        (
            {"kind": "heartbeat", "connected": True, "transport": "stream", "subscription": "sim"},
            "Heartbeat: ProjectX connected (stream, sim)",
        ),
        (
            {
                "kind": "credit_projection",
                "day": "2026-10-12",
                "total_credits": 3488,
                "symbols": ["QQQ", "SPY", "SPXW", "NDXP"],
                "refresh_interval_s": 15,
                "window_start_ny": "09:00",
                "window_end_ny": "16:15",
            },
            "Skylit plan for 2026-10-12: 3488 credits (QQQ, SPY, SPXW, NDXP, every 15 s, "
            "09:00-16:15 ET)",
        ),
        (
            {"kind": "feed_status", "feed": "quotes", "up": True, "detail": "transport stream"},
            "quotes feed up: transport stream",
        ),
        ({"kind": "plugin_loaded", "plugin": "delta"}, "Plugin delta loaded"),
        ({"kind": "rules", "event": "skip", "reason": "size_zero"}, "Rules skip: size_zero"),
        (
            {
                "kind": "rules_entry",
                "side": "long",
                "qty": 4,
                "instrument": "MNQ",
                "tag": "e00010",
                "reason": "pullback",
            },
            "Rules entry: long 4 MNQ e00010 (pullback)",
        ),
        (
            {"kind": "intent_rejected", "tag": "t1", "reason": "max_contracts"},
            "Order refused (max_contracts): t1",
        ),
        (
            {
                "kind": "order_mode",
                "plugin": "m",
                "requested": "practice",
                "mode": "practice",
                "failures": [],
            },
            "Order mode of m: practice (requested practice)",
        ),
        (
            {"kind": "order_mode", "plugin": "m", "detail": "FSE_RUN_PLUGIN_ORDERS is not enabled"},
            "Order mode of m: FSE_RUN_PLUGIN_ORDERS is not enabled",
        ),
        (
            {
                "kind": "broker_rollover",
                "plugin": "m",
                "carried_pending": 1,
                "withdrawn": 0,
                "dropped": 0,
                "open_positions": 1,
                "forced": True,
            },
            "Broker day rollover for m: "
            "(carried 1, withdrawn 0, dropped 0, open positions 1, forced)",
        ),
        (
            {
                "kind": "broker",
                "plugin": "m",
                "event": "order_sent",
                "client_id": "c",
                "instrument": "MNQ",
                "outcome": "accepted",
            },
            "Broker for m: order_sent (MNQ, accepted)",
        ),
        (
            {
                "kind": "broker",
                "plugin": "alpha",
                "event": "reconciliation",
                "differences": [],
                "ignored_positions": [],
            },
            "Broker for alpha: orders match the broker",
        ),
        (
            {
                "kind": "broker",
                "plugin": "alpha",
                "event": "reconciliation",
                "differences": ["MNQ position expected 0 saw 1"],
                "ignored_positions": [],
            },
            "Broker for alpha: reconciliation differences (MNQ position expected 0 saw 1)",
        ),
    ],
)
def test_sentences(entry: dict[str, object], text: str) -> None:
    assert describe(entry)["text"] == text


def test_a_plugin_kind_gets_the_generic_sentence() -> None:
    said = describe(
        {
            "kind": "delta_skip",
            "level": "info",
            "plugin": "delta",
            "t_ns": 1,
            "slot": "T1",
            "reason": "ema_not_ready",
        }
    )
    assert said == {
        "text": "Delta skip: slot T1; reason ema_not_ready",
        "severity": "info",
        "category": "plugin",
    }


def test_severity_follows_the_level() -> None:
    assert describe({"kind": "feed_status", "level": "warning"})["severity"] == "warning"
    assert describe({"kind": "plugin_faulted"})["severity"] == "error"
