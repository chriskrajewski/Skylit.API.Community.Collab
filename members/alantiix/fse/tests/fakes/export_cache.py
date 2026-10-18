"""A small synthetic Data_Cache for the ``fse export-aws`` tests, written with the real writers.

:func:`build_cache` writes, under ``root``:

- heatmap windows for SPX and SPY, gamma and vanna, two views, sessions on
  both sides of a month boundary (2025-10-31 and 2025-11-03), with several
  axes per window, duplicate ``asOf`` values, NaN (with a payload), ``-0.0``,
  null ``previous_close``, mixed ``node_types`` and windows without a view
  (empty ``fse.view``); plus one ``incomplete`` and one ``no_data`` window;
- MES 60 s bars for both sessions;
- VIX daily values;
- dark-pool months for SPY (2025-10 and 2025-11, with a fetched date that has
  no prints) and QQQ;
- one regime-medians file.

:func:`tree_snapshot` records every file's size, mtime and sha256, so a test
can prove the export never wrote to the cache.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from fse.awsexport.options import ExportOptions, Mode, build_options
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.data.medians import MedianParams, RegimeMedians, RegimeMedianStore
from fse.engine.regime import TrailingMedian, TrailingMedians
from fse.engine.types import Bar, DarkPoolPrint, Snapshot, Unavailable
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, ny_instant

__all__ = [
    "BAR_SESSIONS",
    "NAN_PAYLOAD",
    "SESSIONS",
    "VIEW_A",
    "VIEW_B",
    "BuiltCache",
    "assert_column_exact",
    "build_cache",
    "expected_window_columns",
    "opts",
    "read_staged",
    "snapshot",
    "tree_snapshot",
]

SESSIONS = (date(2025, 10, 31), date(2025, 11, 3))
BAR_SESSIONS = SESSIONS
VIEW_A = HeatmapView()
VIEW_B = HeatmapView(max_strikes=4)
NAN_PAYLOAD: float = struct.unpack("<d", struct.pack("<Q", 0x7FF8_0000_0000_0123))[0]


@dataclass(frozen=True)
class BuiltCache:
    root: Path
    complete_windows: tuple[CacheWindowKey, ...]
    incomplete_window: CacheWindowKey
    no_data_window: CacheWindowKey


def snapshot(key: CacheWindowKey, i: int, *, as_of_ns: int | None = None) -> Snapshot:
    """The ``i``-th Snapshot of a window: two alternating axes and awkward values."""
    if i % 2 == 0:
        strikes: tuple[float, ...] = (5800.0, 5805.0, -0.0)
        expirations: tuple[str, ...] = ("2025-10-31",)
        values: tuple[float, ...] = (1.5 + i, -0.0, NAN_PAYLOAD)
        node_types: tuple[str | None, ...] | None = None
    else:
        strikes = (5790.0, 5795.0, 5800.0, 5810.0)
        expirations = ("2025-10-31", "2025-11-03")
        values = (0.1, 2.0**-1074, -1e308, float(i))
        node_types = ("king", None, "", "gatekeeper")
    t = key.start_ns + i * NS_PER_SECOND if as_of_ns is None else as_of_ns
    return Snapshot(
        symbol=key.symbol,
        metric=key.metric,
        view_id=key.view_id,
        as_of_ns=t,
        as_of_raw=f"raw-{t}",
        spot=5801.25 + i,
        previous_close=None if i % 3 == 0 else 5799.0,
        strikes=strikes,
        values=values,
        node_types=node_types,
        expirations=expirations,
        resolution="1s",
        source_endpoint="historical" if node_types is not None else "range",
        extra_json="{}",
    )


def _bars(session: date) -> list[Bar]:
    start = ny_instant(session, time(9, 30))
    return [
        Bar(
            instrument="MES",
            contract="MESZ5",
            interval_s=60,
            open_ns=start + k * NS_PER_MINUTE,
            close_ns=start + (k + 1) * NS_PER_MINUTE,
            o=5800.25 + k,
            h=5801.0 + k,
            l=-0.0 if k == 0 else 5799.5,
            c=5800.75,
            v=100.0 * (k + 1),
            o_t=None if k == 1 else 23201 + k,
            h_t=None if k == 1 else 23204 + k,
            l_t=None if k == 1 else 23198,
            c_t=None if k == 1 else 23203,
            source="atlas",
        )
        for k in range(3)
    ]


def build_cache(root: Path) -> BuiltCache:
    """Write the synthetic cache under ``root`` (outside any git working tree)."""
    complete: list[CacheWindowKey] = []
    with DataCache(root) as cache:
        for symbol in ("SPX", "SPY"):
            for metric in ("gamma", "vanna"):
                for session in SESSIONS:
                    for hhmm in (time(9, 30), time(9, 45)):
                        key = CacheWindowKey.for_view(
                            symbol, metric, VIEW_A, session, ny_instant(session, hhmm)
                        )
                        snaps = [snapshot(key, i) for i in range(3)]
                        # A duplicate asOf: a label sample at the same instant.
                        snaps.append(snapshot(key, 3, as_of_ns=snaps[0].as_of_ns))
                        view = VIEW_A if hhmm == time(9, 30) else None
                        cache.write_window(key, snaps, "range", view=view)
                        complete.append(key)
        key_b = CacheWindowKey.for_view(
            "SPX", "gamma", VIEW_B, SESSIONS[0], ny_instant(SESSIONS[0], time(9, 30))
        )
        cache.write_window(key_b, [snapshot(key_b, i) for i in range(2)], "range", view=VIEW_B)
        complete.append(key_b)
        no_data = CacheWindowKey.for_view(
            "SPY", "vanna", VIEW_A, SESSIONS[1], ny_instant(SESSIONS[1], time(10, 0))
        )
        cache.write_window(no_data, [], "range", view=VIEW_A)
        incomplete = CacheWindowKey.for_view(
            "SPX", "vanna", VIEW_A, SESSIONS[1], ny_instant(SESSIONS[1], time(10, 0))
        )
        cache.mark_incomplete(incomplete)

        for session in BAR_SESSIONS:
            cache.bars.write_session(
                "MES", 60, session, _bars(session), contract="MESZ5", source="atlas"
            )

        open_at = ny_instant(SESSIONS[0], time(9, 31))
        cache.vix.write_daily(
            [
                VixDailyRecord(SESSIONS[1], 17.25, open_at + 1, None, None, "atlas"),
                VixDailyRecord(SESSIONS[0], -0.0, open_at, 18.5, open_at + 5, "import"),
            ]
        )

        def dp(ticker: str, day: date, minute: int, price: float) -> DarkPoolPrint:
            ts = ny_instant(day, time(10, minute))
            return DarkPoolPrint(
                ticker=ticker, ts_ns=ts, price=price, size=100, notional=price * 100, venue="D"
            )

        cache.darkpool.write_trade_dates(
            "SPY",
            [date(2025, 10, 30), date(2025, 10, 31), date(2025, 11, 3)],
            [
                dp("SPY", date(2025, 10, 30), 5, 680.5),
                dp("SPY", date(2025, 10, 30), 1, 680.25),
                dp("SPY", date(2025, 11, 3), 0, 681.0),
            ],
        )
        cache.darkpool.write_trade_dates(
            "QQQ", [date(2025, 10, 30)], [dp("QQQ", date(2025, 10, 30), 2, 610.0)]
        )

        params = MedianParams(symbol="SPX", regime_distance_pct=1.0)
        RegimeMedianStore(cache).write(
            RegimeMedians(
                view_id=VIEW_A.view_id(),
                params=params,
                inputs_sha256="0" * 64,
                by_session={
                    SESSIONS[0]: TrailingMedians(
                        gamma=Unavailable("no history"), vanna=Unavailable("no history")
                    ),
                    SESSIONS[1]: TrailingMedians(
                        gamma=TrailingMedian(12.5, 1), vanna=TrailingMedian(0.25, 1)
                    ),
                },
                incomplete_windows=1,
            )
        )
    return BuiltCache(root, tuple(complete), incomplete, no_data)


def tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """``relative path -> (size, mtime_ns, sha256)`` of every file under ``root``."""
    out: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[path.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns, digest)
    return out


# ---------------------------------------------------------------- export helpers


def opts(cache: Path, staging: Path, mode: Mode = "stage_only", **kw: object) -> ExportOptions:
    """Validated options on ``cache`` and ``staging`` (both under ``tmp_path``)."""
    return build_options(mode=mode, cache_dir=cache, staging_dir=staging, **kw)  # type: ignore[arg-type]


def read_staged(staging: Path, rel_path: str) -> pa.Table:
    """Every staged part of a partition, concatenated in part order."""
    directory = staging.joinpath("data", *rel_path.strip("/").split("/"))
    parts = sorted(directory.glob("part-*.parquet"))
    return pa.concat_tables([pq.read_table(p) for p in parts])


def _plain(col: pa.Array | pa.ChunkedArray) -> pa.Array:
    return col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col


def assert_column_exact(
    name: str, got: pa.Array | pa.ChunkedArray, want: pa.Array | pa.ChunkedArray
) -> None:
    """Equal length, validity and values; float64 by bit pattern, lists by lengths and children.

    Raw list offsets are never compared: ``take`` and slicing rebuild them from 0.
    """
    g, w = _plain(got), _plain(want)
    assert len(g) == len(w), f"{name}: {len(g)} rows, expected {len(w)}"
    assert g.is_null().equals(w.is_null()), f"{name}: null masks differ"
    if pa.types.is_list(w.type):
        assert pa.types.is_list(g.type), f"{name}: {g.type} is not a list"
        assert pc.list_value_length(g).equals(pc.list_value_length(w)), f"{name}: lengths differ"
        assert_column_exact(f"{name}[]", g.flatten(), w.flatten())
    elif pa.types.is_float64(w.type):
        assert pa.types.is_float64(g.type), f"{name}: {g.type} is not float64"
        assert g.view(pa.int64()).equals(w.view(pa.int64())), f"{name}: float64 bits differ"
    else:
        assert g.type.equals(w.type), f"{name}: {g.type} is not {w.type}"
        assert g.equals(w), f"{name}: values differ"


def expected_window_columns(path: Path) -> dict[str, pa.Array]:
    """A source window file's rows as export columns: values renamed, axes joined per row."""
    table = pq.read_table(path)
    axes = pa.ipc.open_stream(pa.BufferReader(table.schema.metadata[b"fse.axes"])).read_all()
    idx = pc.index_in(table["axis_id"], value_set=axes["axis_id"].combine_chunks())
    out = {name: _plain(table[name]) for name in table.column_names}
    out["strike_values"] = out.pop("values")
    out["strikes"] = _plain(axes["strikes"].take(idx))
    out["expirations"] = _plain(axes["expirations"].take(idx))
    return out
