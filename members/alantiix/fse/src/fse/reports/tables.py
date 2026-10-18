"""Report tables: a backtest run directory read back and turned into report values.

:func:`load_run` reads the files a backtest wrote (``fse.backtest.runner``):
the Run_Manifest, the accepted trades, the Gate_Funnel and the report inputs.
:func:`build_report` turns them into one :class:`RunReport`:

- the Req 20.15 header: date range, session count, trade count, data
  resolution (bar interval and Decision_Cadence), Fill_Simulator and cost
  settings, whether a Holdout_Period session is included, and the "touch
  fills (optimistic)" label when the trade-through distance is 0 (Req 13.2);
- the run metrics (``fse.analytics.metrics.summarize``, Req 20.1-20.6,
  20.13, 20.16) over the evaluated sessions;
- the 95% percentile bootstrap intervals of the Primary_Win_Rate and
  expectancy in R that the Backtester drew with the run's seed (Req 20.12),
  read back from the report inputs (``None`` for a run directory written
  before they were recorded);
- the Gate_Funnel as the run wrote it (Req 19.6-19.7, 19.10-19.12, 19.17);
- the King and Gatekeeper agreement rates (Req 6.22-6.23);
- the Tap count and inter-decision Tap count of each evaluated session; a
  skipped session is not measurable (Req 18.11);
- one MAE and MFE row per trade (Req 20.6).

:func:`report_jsonable` is the JSON output and :func:`excursions_csv` the
per-trade file; ``fse.reports.markdown`` renders the Markdown. Nothing here
writes a file. A missing or unreadable run file raises
:class:`ReportDataError` (exit 4).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, ClassVar, Final

from fse.analytics.bootstrap import BootstrapIntervals, Interval
from fse.analytics.metrics import Metrics, MetricsCfg, metrics_to_jsonable, summarize
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import (
    BACKTEST_KIND,
    FUNNEL_FILE_NAME,
    REPORT_INPUTS_FILE_NAME,
    TRADES_JSON_FILE_NAME,
    intervals_to_jsonable,
)
from fse.engine.types import Fill, NotApplicable, SetupKey, Trade
from fse.logio.canonical_json import JsonValue, to_jsonable

__all__ = [
    "EXCURSION_COLUMNS",
    "NOT_MEASURABLE",
    "TOUCH_FILLS_LABEL",
    "Agreement",
    "Header",
    "ReportDataError",
    "RunData",
    "RunReport",
    "TapRow",
    "build_report",
    "excursions_csv",
    "load_run",
    "report_jsonable",
    "run_header",
    "trade_from_jsonable",
]

TOUCH_FILLS_LABEL: Final = "touch fills (optimistic)"
NOT_MEASURABLE: Final = "not measurable"
EXCURSION_COLUMNS: Final[tuple[str, ...]] = (
    "session",
    "instrument",
    "pattern",
    "source_strike",
    "direction",
    "tap_seq",
    "entry_client_id",
    "net",
    "r_multiple",
    "mae_r",
    "mfe_r",
)
"""The per-trade MAE and MFE file's header (Req 20.6)."""

_PLACES: Final = 6
_NA_TEXT: Final = "not applicable"


class ReportDataError(Exception):
    """A run file is missing, unreadable or not a completed backtest's (exit 4)."""

    exit_code: ClassVar[int] = 4


# ---------------------------------------------------------------- reading a run


@dataclass(frozen=True, slots=True)
class RunData:
    """The files of one completed backtest run, as read from its run directory."""

    run_dir: Path
    manifest: Mapping[str, Any]
    trades: tuple[Trade, ...]
    funnel: Mapping[str, Any]
    inputs: Mapping[str, Any]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReportDataError(f"{path} does not exist") from None
    except (OSError, ValueError) as exc:
        raise ReportDataError(f"{path} cannot be read: {exc}") from None


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"a money or R value must be a decimal string, got {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{value!r} is not a decimal") from None


def _fill(obj: Mapping[str, Any]) -> Fill:
    return Fill(
        obj["client_id"], obj["bar_open_ns"], obj["price"], obj["qty"], _decimal(obj["fees"])
    )


def trade_from_jsonable(obj: Mapping[str, Any]) -> Trade:
    """The :class:`Trade` that ``to_jsonable`` encoded as ``obj`` (``trades.json``)."""
    k = obj["setup_key"]
    key = SetupKey(
        k["instrument"],
        k["pattern"],
        float(k["source_strike"]),
        k["direction"],
        date.fromisoformat(k["session"]),
        k["tap_seq"],
    )
    return Trade(
        setup_key=key,
        entry_fill=_fill(obj["entry_fill"]),
        initial_stop=obj["initial_stop"],
        qty_at_entry=obj["qty_at_entry"],
        exits=tuple(_fill(f) for f in obj["exits"]),
        net=_decimal(obj["net"]),
        r=_decimal(obj["r"]),
        r_multiple=_decimal(obj["r_multiple"]),
        reached_tp1=bool(obj["reached_tp1"]),
        mae_r=_decimal(obj["mae_r"]),
        mfe_r=_decimal(obj["mfe_r"]),
        missing_bars=obj["missing_bars"],
        shadow=bool(obj["shadow"]),
    )


def load_run(run_dir: Path) -> RunData:
    """Read the Run_Manifest, trades, Gate_Funnel and report inputs of a completed backtest."""
    manifest = _read_json(run_dir / MANIFEST_FILE_NAME)
    if not isinstance(manifest, dict) or manifest.get("kind") != BACKTEST_KIND:
        raise ReportDataError(f"{run_dir / MANIFEST_FILE_NAME} is not a backtest Run_Manifest")
    if manifest.get("status") != "completed":
        raise ReportDataError(
            f"backtest {manifest.get('run_id')} ended with status {manifest.get('status')}; "
            "only a completed run has a report"
        )
    raw_trades = _read_json(run_dir / TRADES_JSON_FILE_NAME)
    funnel = _read_json(run_dir / FUNNEL_FILE_NAME)
    inputs = _read_json(run_dir / REPORT_INPUTS_FILE_NAME)
    try:
        trades = tuple(trade_from_jsonable(t) for t in raw_trades)
    except (KeyError, TypeError, ValueError) as exc:
        path = run_dir / TRADES_JSON_FILE_NAME
        raise ReportDataError(f"{path} holds a bad trade: {exc}") from None
    if not isinstance(funnel, dict) or not isinstance(inputs, dict):
        raise ReportDataError(f"the Gate_Funnel or report inputs of {run_dir} are not objects")
    return RunData(run_dir, manifest, trades, funnel, inputs)


# ---------------------------------------------------------------- report values


@dataclass(frozen=True, slots=True)
class Header:
    """The Req 20.15 header of a run report."""

    run_id: str
    config_hash: str
    config_path: str | None
    seed: int | None
    range_start: date
    range_end: date
    first_session: date | None
    last_session: date | None
    session_count: int
    skipped_sessions: tuple[date, ...]
    trade_count: int
    bar_interval_s: int
    decision_cadence_s: int
    trade_through_ticks: int
    slippage_ticks: int
    costs: tuple[tuple[str, Decimal, Decimal], ...]
    holdout: tuple[date, date, int] | None
    holdout_included: tuple[date, ...]

    @property
    def touch_fills(self) -> bool:
        """Whether every report of the run carries the touch-fills label (Req 13.2)."""
        return self.trade_through_ticks == 0


@dataclass(frozen=True, slots=True)
class Agreement:
    """King and Gatekeeper agreement with Skylit's ``nodeType`` labels (Req 6.22-6.23)."""

    compared: int
    without_labels: int
    king_agree: int
    gatekeeper_both: int
    gatekeeper_either: int

    @property
    def king_rate_pct(self) -> Fraction | NotApplicable:
        return Fraction(100 * self.king_agree, self.compared) if self.compared else NotApplicable()

    @property
    def gatekeeper_rate_pct(self) -> Fraction | NotApplicable:
        if not self.gatekeeper_either:
            return NotApplicable()
        return Fraction(100 * self.gatekeeper_both, self.gatekeeper_either)


@dataclass(frozen=True, slots=True)
class TapRow:
    """One session's Tap counts; ``None`` counts mark a session that is not measurable."""

    session: date
    bar_interval_s: int | None
    taps: int | None
    inter_decision: int | None


@dataclass(frozen=True, slots=True)
class RunReport:
    """Everything one run report shows (see the module notes)."""

    header: Header
    metrics: Metrics
    funnel: Mapping[str, Any]
    agreement: Agreement
    taps: tuple[TapRow, ...]
    trades: tuple[Trade, ...]
    intervals: BootstrapIntervals | None = None


def _interval(raw: object) -> Interval | NotApplicable:
    if raw == _NA_TEXT:
        return NotApplicable()
    if not isinstance(raw, Mapping):
        raise ValueError(f"a bootstrap interval must be an object or {_NA_TEXT!r}, got {raw!r}")
    lower, upper = float(raw["lower"]), float(raw["upper"])
    if not lower <= upper:
        raise ValueError(f"a bootstrap interval's lower bound is above its upper bound: {raw!r}")
    return Interval(lower, upper)


def _intervals(raw: object) -> BootstrapIntervals | None:
    """The bootstrap intervals stored in the report inputs; ``None`` when none were stored."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"the bootstrap intervals must be an object, got {raw!r}")
    return BootstrapIntervals(
        seed=raw["seed"],
        resamples=raw["resamples"],
        trade_count=raw["trade_count"],
        primary_win_rate=raw["primary_win_rate"],
        primary_win_rate_pct=_interval(raw["primary_win_rate_pct"]),
        expectancy_r=_interval(raw["expectancy_r"]),
        low_sample=bool(raw["low_sample"]),
        confidence_pct=raw["confidence_pct"],
    )


def _dates(values: Iterable[str]) -> tuple[date, ...]:
    return tuple(date.fromisoformat(v) for v in values)


def run_header(run: RunData, trade_count: int) -> Header:
    """The Req 20.15 header values of ``run`` with ``trade_count`` accepted trades."""
    m, inputs = run.manifest, run.inputs
    evaluated = _dates(m["sessions_evaluated"])
    fills = inputs["fills"]
    costs = tuple(
        (name, Decimal(c["commission"]), Decimal(c["exchange_fee"]))
        for name, c in sorted(fills["costs"].items())
    )
    held = inputs.get("holdout")
    holdout = None
    included: tuple[date, ...] = ()
    if held is not None:
        first, last = date.fromisoformat(held["first"]), date.fromisoformat(held["last"])
        holdout = (first, last, held["sessions"])
        included = _dates(held["included"])
    return Header(
        run_id=m["run_id"],
        config_hash=m["config_hash"],
        config_path=m.get("config_path"),
        seed=m.get("seed"),
        range_start=date.fromisoformat(m["data_range"]["start"]),
        range_end=date.fromisoformat(m["data_range"]["end"]),
        first_session=evaluated[0] if evaluated else None,
        last_session=evaluated[-1] if evaluated else None,
        session_count=len(evaluated),
        skipped_sessions=tuple(date.fromisoformat(s["date"]) for s in m["sessions_skipped"]),
        trade_count=trade_count,
        bar_interval_s=inputs["bar_interval_s"],
        decision_cadence_s=inputs["decision_cadence_s"],
        trade_through_ticks=fills["trade_through_ticks"],
        slippage_ticks=fills["slippage_ticks"],
        costs=costs,
        holdout=holdout,
        holdout_included=included,
    )


def _taps(run: RunData) -> tuple[TapRow, ...]:
    rows = [
        TapRow(
            date.fromisoformat(c["session"]), c["bar_interval_s"], c["taps"], c["inter_decision"]
        )
        for c in run.inputs["tap_counts"]
    ]
    rows.extend(
        TapRow(date.fromisoformat(s["date"]), None, None, None)
        for s in run.manifest["sessions_skipped"]
    )
    return tuple(sorted(rows, key=lambda r: r.session))


def build_report(run: RunData) -> RunReport:
    """The report values of ``run`` (see the module notes)."""
    try:
        cfg = run.inputs["metrics"]
        metrics_cfg = MetricsCfg(
            scratch_tolerance_r=Decimal(cfg["scratch_tolerance_r"]),
            min_sample_trades=cfg["min_sample_trades"],
            primary_win_rate=cfg["primary_win_rate"],
            has_first_target=cfg["has_first_target"],
        )
        sessions = _dates(run.manifest["sessions_evaluated"])
        metrics = summarize(run.trades, sessions, metrics_cfg)
        a = run.inputs["node_agreement"]
        agreement = Agreement(
            a["compared"], a["without_labels"], a["king_agree"], a["gatekeeper_both"],
            a["gatekeeper_either"],
        )  # fmt: skip
        return RunReport(
            header=run_header(run, metrics.trade_count),
            metrics=metrics,
            funnel=run.funnel,
            agreement=agreement,
            taps=_taps(run),
            trades=tuple(t for t in run.trades if not t.shadow),
            intervals=_intervals(run.inputs.get("bootstrap")),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ReportDataError(f"the files of run {run.run_dir} are inconsistent: {exc!r}") from None


# ---------------------------------------------------------------- outputs


def _trade_order(t: Trade) -> tuple[int, int]:
    return max(f.bar_open_ns for f in t.exits), t.entry_fill.bar_open_ns


def excursion_rows(trades: Sequence[Trade]) -> list[list[str]]:
    """One row per accepted trade, by exit time: its MAE and MFE in R (Req 20.6)."""
    rows: list[list[str]] = []
    for t in sorted((t for t in trades if not t.shadow), key=_trade_order):
        k = t.setup_key
        rows.append(
            [
                k.session.isoformat(), k.instrument, k.pattern, repr(k.source_strike),
                k.direction, str(k.tap_seq), t.entry_fill.client_id, str(t.net),
                str(t.r_multiple), str(t.mae_r), str(t.mfe_r),
            ]
        )  # fmt: skip
    return rows


def excursions_csv(trades: Sequence[Trade]) -> str:
    """The per-trade MAE and MFE file: :data:`EXCURSION_COLUMNS`, then one row per trade."""
    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(EXCURSION_COLUMNS)
    out.writerows(excursion_rows(trades))
    return buf.getvalue()


def _rate(value: Fraction | NotApplicable) -> object:
    if isinstance(value, NotApplicable):
        return value
    return Decimal(round(value * 10**_PLACES)).scaleb(-_PLACES)


def report_jsonable(report: RunReport) -> JsonValue:
    """The report as canonical JSON values: header, metrics, funnel, agreement and Taps."""
    h, a = report.header, report.agreement
    return to_jsonable(
        {
            "header": {
                "run_id": h.run_id,
                "config_hash": h.config_hash,
                "config_path": h.config_path,
                "seed": h.seed,
                "data_range": {"start": h.range_start, "end": h.range_end},
                "first_session": h.first_session,
                "last_session": h.last_session,
                "session_count": h.session_count,
                "skipped_sessions": list(h.skipped_sessions),
                "trade_count": h.trade_count,
                "bar_interval_s": h.bar_interval_s,
                "decision_cadence_s": h.decision_cadence_s,
                "trade_through_ticks": h.trade_through_ticks,
                "slippage_ticks": h.slippage_ticks,
                "touch_fills": h.touch_fills,
                "costs": {n: {"commission": c, "exchange_fee": f} for n, c, f in h.costs},
                "holdout": None
                if h.holdout is None
                else {"first": h.holdout[0], "last": h.holdout[1], "sessions": h.holdout[2]},
                "holdout_included": list(h.holdout_included),
                "measured_not_forecast": True,
            },
            "metrics": metrics_to_jsonable(report.metrics, places=_PLACES),
            "bootstrap_intervals": None
            if report.intervals is None
            else intervals_to_jsonable(report.intervals),
            "gate_funnel": report.funnel,
            "node_agreement": {
                "compared": a.compared,
                "without_labels": a.without_labels,
                "king_agree": a.king_agree,
                "king_rate_pct": _rate(a.king_rate_pct),
                "gatekeeper_both": a.gatekeeper_both,
                "gatekeeper_either": a.gatekeeper_either,
                "gatekeeper_rate_pct": _rate(a.gatekeeper_rate_pct),
            },
            "tap_counts": [
                {
                    "session": r.session,
                    "measurable": r.taps is not None,
                    "bar_interval_s": r.bar_interval_s,
                    "taps": r.taps,
                    "inter_decision": r.inter_decision,
                }
                for r in report.taps
            ],
        }
    )
