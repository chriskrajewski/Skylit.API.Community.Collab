"""The Markdown run report (design §20, Req 20.15).

:func:`render` turns a :class:`~fse.reports.tables.RunReport` into Markdown, in
this order:

1. the header (Req 20.15), before any metric table, with the "touch fills
   (optimistic)" label in the title when the trade-through distance is 0
   (Req 13.2) and the statement that the figures are measured backtest
   results, not a forecast;
2. the metrics (Req 20.1-20.6), each low-sample value labelled next to it
   (Req 20.13) and each undefined value shown as "not applicable" (Req 20.16);
3. the Gate_Funnel: final statuses, cancel causes, the per-Gate table with
   the only-rejected Shadow_Trades and flags beside the accepted trades, the
   co-rejection matrix and the per-session top 3 (Req 19), with win rate and
   mean R_Multiple shown as "not available" when the filled count is 0
   (Req 19.10);
4. the King and Gatekeeper agreement rates (Req 6.22-6.23);
5. the Tap counts per session (Req 18.11).

Numbers are shown to two decimal places; the JSON output keeps six.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Any, Final

from fse.analytics.metrics import Metrics, Quantiles, round_fraction
from fse.engine.types import NotApplicable
from fse.reports.tables import NOT_MEASURABLE, TOUCH_FILLS_LABEL, RunReport

__all__ = [
    "LOW_SAMPLE_LABEL",
    "MEASURED_STATEMENT",
    "NOT_APPLICABLE",
    "NOT_AVAILABLE",
    "number",
    "render",
    "table",
]

NOT_APPLICABLE: Final = "not applicable"
NOT_AVAILABLE: Final = "not available"
LOW_SAMPLE_LABEL: Final = "(low sample)"
MEASURED_STATEMENT: Final = (
    "These figures are measured backtest results on cached data. "
    "They are not a forecast of future results."
)
_FLAGS: Final[Mapping[str, str]] = {
    "no_measured_edge": "no measured edge",
    "insufficient_sample": "insufficient sample",
}
_PLACES: Final = 2


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """A Markdown table; ``|`` in a cell is escaped."""
    lines = [
        "| " + " | ".join(_cell(h) for h in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def number(value: object, *, suffix: str = "", prefix: str = "") -> str:
    """A value to two decimal places; ``NotApplicable`` (``{}`` in JSON) is "not applicable"."""
    if value is None or isinstance(value, NotApplicable) or value == {}:
        return NOT_APPLICABLE
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return f"{prefix}{value}{suffix}"
    if isinstance(value, Fraction):
        shown = round_fraction(value, _PLACES)
    elif isinstance(value, Decimal):
        shown = value.quantize(Decimal(1).scaleb(-_PLACES))
    elif isinstance(value, str):
        shown = Decimal(value).quantize(Decimal(1).scaleb(-_PLACES))
    else:
        return str(value)
    return f"{prefix}{shown}{suffix}"


def _usd(value: object) -> str:
    return number(value, prefix="$")


def _pct(value: object) -> str:
    return number(value, suffix="%")


def _r(value: object) -> str:
    return number(value, suffix=" R")


# ---------------------------------------------------------------- sections


def _header(report: RunReport) -> list[str]:
    h = report.header
    title = f"# Backtest report {h.run_id}"
    if h.touch_fills:
        title += f": {TOUCH_FILLS_LABEL}"
    if h.first_session is None:
        sessions = f"{h.range_start} to {h.range_end} (no session evaluated)"
    else:
        sessions = f"{h.first_session} to {h.last_session}"
    costs = "; ".join(
        f"{name} commission ${c} and exchange fee ${f} per contract per side"
        for name, c, f in h.costs
    )
    if h.holdout is None:
        holdout = "no Holdout_Period: the Data_Cache holds no session with full data"
    elif h.holdout_included:
        listed = ", ".join(d.isoformat() for d in h.holdout_included)
        holdout = (
            f"yes, {len(h.holdout_included)} session(s): {listed} "
            f"(Holdout_Period {h.holdout[0]} to {h.holdout[1]}, {h.holdout[2]} sessions)"
        )
    else:
        holdout = f"no (Holdout_Period {h.holdout[0]} to {h.holdout[1]}, {h.holdout[2]} sessions)"
    skipped = ", ".join(d.isoformat() for d in h.skipped_sessions) or "none"
    lines = [
        title,
        "",
        "## Run",
        "",
        f"- Sessions: {sessions}; requested range {h.range_start} to {h.range_end}",
        f"- Session count: {h.session_count}; skipped for missing data: {skipped}",
        f"- Trade count: {h.trade_count}",
        f"- Data resolution: {h.bar_interval_s} s bars; Decision_Cadence {h.decision_cadence_s} s",
        f"- Fill_Simulator: trade-through {h.trade_through_ticks} tick(s); "
        f"slippage {h.slippage_ticks} tick(s) on stop and market fills"
        + (f"; {TOUCH_FILLS_LABEL}" if h.touch_fills else ""),
        f"- Costs: {costs or 'none configured'}",
        f"- Holdout_Period sessions included: {holdout}",
        f"- Strategy_Config: {h.config_path or 'not recorded'} (hash {h.config_hash}); "
        f"seed {h.seed}",
        "",
        MEASURED_STATEMENT,
    ]
    return lines


def _quantiles(q: Quantiles | NotApplicable) -> str:
    if isinstance(q, NotApplicable):
        return NOT_APPLICABLE
    return (
        f"min {_r(q.minimum)}, P25 {_r(q.p25)}, median {_r(q.median)}, P75 {_r(q.p75)}, "
        f"P90 {_r(q.p90)}, max {_r(q.maximum)}"
    )


def _metrics(m: Metrics) -> list[str]:
    def low(field: str, text: str) -> str:
        return f"{text} {LOW_SAMPLE_LABEL}" if m.is_low_sample(field) else text

    rows = [
        ("Trade count", str(m.trade_count)),
        ("Trades per day", number(m.trades_per_day)),
        ("Sessions with a trade", _pct(m.sessions_with_trade_pct)),
        ("Longest losing streak", str(m.longest_losing_streak)),
        ("Winning / losing trades", f"{m.win_count} / {m.loss_count}"),
        ("Average win", _r(m.avg_win_r)),
        ("Average loss", _r(m.avg_loss_r)),
        ("Expectancy (R)", low("expectancy_r", _r(m.expectancy_r))),
        ("Expectancy ($)", low("expectancy_usd", _usd(m.expectancy_usd))),
        ("Gross profit / gross loss", f"{_usd(m.gross_profit_usd)} / {_usd(m.gross_loss_usd)}"),
        ("Profit factor", number(m.profit_factor)),
        ("Maximum drawdown ($)", _usd(m.max_drawdown_usd)),
        ("Maximum drawdown (R)", _r(m.max_drawdown_r)),
        ("Win rate (a): net P&L above $0", low("win_rate_a_pct", _pct(m.win_rate_a_pct))),
        ("Win rate (b): scratch counts as a win", low("win_rate_b_pct", _pct(m.win_rate_b_pct))),
        ("Win rate (c): reached TP1", low("win_rate_c_pct", _pct(m.win_rate_c_pct))),
        (
            f"Primary_Win_Rate ({m.primary_win_rate})",
            low("primary_win_rate_pct", _pct(m.primary_win_rate_pct)),
        ),
        (
            "Break-even win rate",
            low("break_even_win_rate_pct", _pct(m.break_even_win_rate_pct)),
        ),
        ("MAE", _quantiles(m.mae_r)),
        ("MFE", _quantiles(m.mfe_r)),
    ]
    out = ["## Metrics", ""]
    if m.low_sample:
        out += [f"Fewer trades than the minimum sample: values marked {LOW_SAMPLE_LABEL}.", ""]
    out.append(table(("Metric", "Value"), rows))
    return out


def _stats(s: Mapping[str, Any]) -> tuple[str, str, str]:
    """Filled count, win rate and mean R; "not available" with 0 filled (Req 19.10)."""
    if s["filled"] == 0:
        return "0", NOT_AVAILABLE, NOT_AVAILABLE
    return str(s["filled"]), _pct(s["win_rate_pct"]), _r(s["mean_r"])


def _funnel(f: Mapping[str, Any]) -> list[str]:
    counts = f["status_counts"]
    out = [
        "## Gate_Funnel",
        "",
        f"Distinct Setup_Keys: {f['setup_keys']}",
        "",
        table(
            ("Final status", "Setup_Keys"),
            [(s, counts[s]) for s in ("filled", "cancelled", "rejected", "untapped")],
        ),
    ]
    causes = f["cancel_causes"]
    if causes:
        out += ["", "Cancelled Setup_Keys by cause:", ""]
        out.append(table(("Cause", "Setup_Keys"), [(c["cause"], c["count"]) for c in causes]))
    accepted = f["accepted"]
    filled, rate, mean = _stats(accepted)
    out += [
        "",
        "### Gates",
        "",
        f"Accepted trades (final status filled): {filled} filled, win rate {rate}, "
        f"mean R_Multiple {mean}. Minimum sample for a flag: {f['min_sample']}.",
        "",
    ]
    rows = []
    for g in f["gates"]:
        sh = g["only_rejected_shadows"]
        s_filled, s_rate, s_mean = _stats(sh)
        flag = _FLAGS.get(g["flag"] or "", "")
        if g["flag"] == "insufficient_sample":
            flag = f"{flag} ({sh['filled']} filled)"
        rows.append(
            (
                g["gate_id"], g["failing"], g["only_failing"], g["first_failing"], s_filled,
                sh["not_filled"], s_rate, s_mean, flag,
            )
        )  # fmt: skip
    if rows:
        out.append(
            table(
                (
                    "Gate", "Failing", "Only failing", "First failing", "Only-rejected shadows "
                    "filled", "Not filled", "Shadow win rate", "Shadow mean R", "Flag",
                ),
                rows,
            )
        )  # fmt: skip
    else:
        out.append("No Gate is enabled.")
    matrix = f["co_rejection"]
    if matrix["gates"]:
        out += ["", "### Co-rejection matrix", ""]
        out.append(
            table(
                ("Gate", *matrix["gates"]),
                [(g, *row) for g, row in zip(matrix["gates"], matrix["rows"], strict=True)],
            )
        )
    out += ["", "### Per session", ""]
    session_rows = [
        (
            s["session"],
            s["setup_keys"],
            ", ".join(f"{t['gate_id']} ({t['count']})" for t in s["top_gates"]) or "none",
        )
        for s in f["sessions"]
    ]
    out.append(table(("Session", "Setup_Keys", "Top failing Gates"), session_rows))
    return out


def _agreement(report: RunReport) -> list[str]:
    a = report.agreement
    king = (
        NOT_AVAILABLE
        if a.compared == 0
        else f"{_pct(a.king_rate_pct)} ({a.king_agree} of {a.compared} Snapshots)"
    )
    gatekeeper = (
        NOT_AVAILABLE
        if a.gatekeeper_either == 0
        else f"{_pct(a.gatekeeper_rate_pct)} ({a.gatekeeper_both} of {a.gatekeeper_either} strikes)"
    )
    return [
        "## Node_Classifier agreement with Skylit nodeType labels",
        "",
        f"- Snapshots compared: {a.compared}; Snapshots without nodeType labels: "
        f"{a.without_labels}",
        f"- King agreement: {king}",
        f"- Gatekeeper agreement: {gatekeeper}",
    ]


def _taps(report: RunReport) -> list[str]:
    rows = [
        (r.session, NOT_MEASURABLE, NOT_MEASURABLE, NOT_MEASURABLE)
        if r.taps is None
        else (r.session, f"{r.bar_interval_s} s", r.taps, r.inter_decision)
        for r in report.taps
    ]
    return [
        "## Taps between 300 s Decision_Times",
        "",
        "Counted on the stored bars, whatever the run's Decision_Cadence.",
        "",
        table(("Session", "Bar interval", "Taps", "Taps between two 300 s Decision_Times"), rows),
    ]


def render(report: RunReport) -> str:
    """The Markdown report (see the module notes), ending with a newline."""
    parts = [
        _header(report),
        _metrics(report.metrics),
        _funnel(report.funnel),
        _agreement(report),
        _taps(report),
        [
            "## Files",
            "",
            "- `report.json`: every value above, to six decimal places",
            "- `trade_excursions.csv`: MAE and MFE in R per trade",
        ],
    ]
    return "\n\n".join("\n".join(p) for p in parts) + "\n"
