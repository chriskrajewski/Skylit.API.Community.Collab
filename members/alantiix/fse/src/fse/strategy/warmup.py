"""How many sessions a rules spec needs before its first live bar, and feeding them.

``--history`` copies that many 1-minute sessions from before ``--first`` into a
sandbox cache and writes ``warmup.json`` there. The rules plugin replays those
bars with entries suppressed, so a daily EMA or DEMA is already warm on the
first day of the card's range. The live runner's cache has no ``warmup.json``,
so a restart does not replay years of bars.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, time
from pathlib import Path

from fse.data.cache import DataCache
from fse.data.cache_io import CacheReadError
from fse.engine.types import Bar
from fse.strategy.book import RBar, RulesBook
from fse.strategy.spec import StrategySpec
from fse.timekit import ny_datetime

MAX_HISTORY_SESSIONS = 1200
"""Above a DEMA of the longest allowed length on daily bars, plus one session."""

_BAR_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
_WARMUP_FILE = "warmup.json"


def indicator_warmup_bars(spec: StrategySpec) -> int:
    """Resampled bars before a cross can see a previous value.

    A DEMA of length L is valid on bar ``2L - 1`` (the inner average, then the
    average of that average). One more completed bar is what makes ``_prev``
    exist; the extra session for that is applied in :func:`history_sessions`.
    """
    need = 1
    for item in spec.indicators:
        length = item.length or 1
        if item.kind == "dema":
            need = max(need, 2 * length - 1)
        elif item.kind in {"ema", "sma", "atr", "supertrend"}:
            need = max(need, length)
    need = max(need, spec.stop.swing_lookback * 2)
    for cond in spec.conditions:
        if cond.lookback is not None:
            need = max(need, cond.lookback + 1)
        if cond.n is not None:
            need = max(need, cond.n)
    if spec.entry.lookback is not None:
        need = max(need, spec.entry.lookback + 1)
    return need


def history_sessions(spec: StrategySpec) -> int:
    """1-minute sessions to copy before ``--first`` so the warmup bars can finish.

    A daily bar closes on the next session, so a daily spec asks for one session
    more than its lookback. Intraday bars close inside the session.
    """
    bars = indicator_warmup_bars(spec)
    size = spec.timeframe.bar
    if size == "1d":
        sessions = bars + 1
    else:
        per = max(_session_minutes(spec.session.start, spec.session.end) // _BAR_MINUTES[size], 1)
        sessions = max(1, (bars + per - 1) // per)
    return min(MAX_HISTORY_SESSIONS, sessions)


def feed_warmup(books: Mapping[str, RulesBook], cache_dir: Path) -> int:
    """Replay ``warmup.json`` into ``books`` with entries suppressed. Bars fed.

    Missing file, unreadable cache, or a bad line: feed what is readable and
    return. This never raises into the host.
    """
    marker = cache_dir / _WARMUP_FILE
    if not marker.is_file():
        return 0
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    fed = 0
    try:
        cache_cm = DataCache(cache_dir)
    except ValueError, OSError:
        return 0
    with cache_cm as cache:
        for instrument, book in books.items():
            days = payload.get(instrument)
            if not isinstance(days, list):
                continue
            for raw in days:
                if not isinstance(raw, str):
                    continue
                try:
                    session = date.fromisoformat(raw)
                    bars = cache.bars.read_session(instrument, 60, session)
                except (ValueError, CacheReadError, OSError):
                    continue
                for bar in bars:
                    if bar.o_t is None or bar.h_t is None or bar.l_t is None or bar.c_t is None:
                        continue
                    book.on_bar(_rbar(bar), live=False)
                    fed += 1
            book.take_logs()
    return fed


def _session_minutes(start: time, end: time) -> int:
    a = start.hour * 60 + start.minute
    b = end.hour * 60 + end.minute
    if start <= end:
        return max(b - a, 1)
    return max(24 * 60 - a + b, 1)


def _rbar(bar: Bar) -> RBar:
    ny = ny_datetime(bar.open_ns)
    assert bar.o_t is not None
    assert bar.h_t is not None
    assert bar.l_t is not None
    assert bar.c_t is not None
    return RBar(
        open_ns=bar.open_ns,
        close_ns=bar.close_ns,
        o=bar.o,
        h=bar.h,
        low=bar.l,
        c=bar.c,
        v=bar.v,
        o_t=bar.o_t,
        h_t=bar.h_t,
        l_t=bar.l_t,
        c_t=bar.c_t,
        day=ny.date(),
        wall=time(ny.hour, ny.minute, ny.second),
    )
