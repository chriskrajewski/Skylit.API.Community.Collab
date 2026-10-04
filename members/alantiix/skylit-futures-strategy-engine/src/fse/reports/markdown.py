"""The Markdown run report (design §20, Req 20.15).

:func:`render` turns a :class:`~fse.reports.tables.RunReport` into Markdown, in
this order:

1. the header (Req 20.15), before any metric table, with the "touch fills
   (optimistic)" label in the title when the trade-through distance is 0
   (Req 13.2) and the statement that the figures are measured backtest
   results, not a forecast;
2. the metrics (Req 20.1-20.6), each low-sample value labelled next to it
   (Req 20.13) and each undefined value shown as "not applicable" (Req 20.16);
   then the 95% bootstrap intervals of the Primary_Win_Rate and expectancy
   in R with their seed and resample count (Req 20.12), labelled low-sample
   the same way;
3. the Gate_Funnel: final statuses, cancel causes, the per-Gate table with
   the only-rejected Shadow_Trades and flags beside the accepted trades, the
   co-rejection matrix and the per-session top 3 (Req 19), with win rate and
   mean R_Multiple shown as "not available" when the filled count is 0
   (Req 19.10);
4. the King and Gatekeeper agreement rates (Req 6.22-6.23);
5. the Tap counts per session (Req 18.11).

Numbers are shown to two decimal places; the JSON output keeps six.

**Experiment reports** (``fse experiment ...``). Each ``render_<kind>``
renders the JSON files an experiment wrote, after an :class:`ExperimentHeader`
(Req 20.15: sessions, trade count or a pointer to the per-row counts, data
resolution, fills and costs, whether a Holdout_Period session is included,
and the measured-not-forecast statement):

- :func:`render_ablation`: base, variant and change per metric (Req 19.14);
- :func:`render_frontier`: the frontier table, Pareto set, reference
  win-rate list and ranking (Req 20.9-20.11, 20.14);
- :func:`render_pass_estimate`: counts, shares and days to pass (Req 21.6-21.7);
- :func:`render_walkforward`: out-of-sample beside in-sample results, the
  "no selection" count and per-pair configuration counts (Req 22.14-22.15);
- :func:`render_cadence`: per-cadence values, shorter-minus-longer
  differences and the included and excluded sessions (Req 18.10);
- :func:`render_holdout`: the Holdout_Log entry and the overlap warning
  (Req 22.5, 22.7).

Every multi-configuration report gives the distinct configuration count
(Req 22.15). "not available" marks a failed configuration's values;
Combine_Pass probabilities are shown to four places and labeled
"(insufficient sample)" when the estimate drew from too few sessions
(Req 21.11); win rates, expectancy and intervals of a low-sample
configuration are labeled "(low sample)" (Req 20.13).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any, Final

from fse.analytics.bootstrap import BootstrapIntervals, Interval
from fse.analytics.metrics import Metrics, Quantiles, round_fraction
from fse.config.schema import StrategyConfig
from fse.engine.step import EngineParams
from fse.engine.taps import BASE_INTERVAL_S
from fse.engine.types import NotApplicable
from fse.experiments.holdout import HoldoutPeriod
from fse.reports.tables import NOT_MEASURABLE, TOUCH_FILLS_LABEL, Header, RunReport

__all__ = [
    "LOW_SAMPLE_LABEL",
    "MEASURED_STATEMENT",
    "NOT_APPLICABLE",
    "NOT_AVAILABLE",
    "PASS_INSUFFICIENT_LABEL",
    "ExperimentHeader",
    "config_costs",
    "experiment_header",
    "holdout_excluded",
    "holdout_included",
    "number",
    "render",
    "render_ablation",
    "render_cadence",
    "render_frontier",
    "render_holdout",
    "render_pass_estimate",
    "render_walkforward",
    "shown",
    "table",
]

NOT_APPLICABLE: Final = "not applicable"
NOT_AVAILABLE: Final = "not available"
LOW_SAMPLE_LABEL: Final = "(low sample)"
PASS_INSUFFICIENT_LABEL: Final = "(insufficient sample)"
"""Next to every value of a pass estimate drawn from too few sessions (Req 21.11)."""
_PROB_PLACES: Final = 4
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


def number(value: object, *, suffix: str = "", prefix: str = "", places: int = _PLACES) -> str:
    """A value to ``places`` decimals (default 2); ``NotApplicable`` is "not applicable"."""
    if value is None or isinstance(value, NotApplicable) or value == {}:
        return NOT_APPLICABLE
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return f"{prefix}{value}{suffix}"
    if isinstance(value, Fraction):
        shown = round_fraction(value, places)
    elif isinstance(value, Decimal):
        shown = value.quantize(Decimal(1).scaleb(-places))
    elif isinstance(value, str):
        shown = Decimal(value).quantize(Decimal(1).scaleb(-places))
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


def holdout_included(h: Header) -> str:
    """A backtest's Holdout_Period line: whether, and which, of its sessions are included."""
    if h.holdout is None:
        return "no Holdout_Period: the Data_Cache holds no session with full data"
    if h.holdout_included:
        listed = ", ".join(d.isoformat() for d in h.holdout_included)
        return (
            f"yes, {len(h.holdout_included)} session(s): {listed} "
            f"(Holdout_Period {h.holdout[0]} to {h.holdout[1]}, {h.holdout[2]} sessions)"
        )
    return f"no (Holdout_Period {h.holdout[0]} to {h.holdout[1]}, {h.holdout[2]} sessions)"


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
    holdout = holdout_included(h)
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


def _bound(value: float, suffix: str) -> str:
    return number(Decimal(repr(value)), suffix=suffix)


def _span(interval: Interval | NotApplicable, suffix: str) -> str:
    if isinstance(interval, NotApplicable):
        return NOT_APPLICABLE
    return f"{_bound(interval.lower, suffix)} to {_bound(interval.upper, suffix)}"


def _intervals(b: BootstrapIntervals | None) -> list[str]:
    """The 95% bootstrap intervals (Req 20.12), labelled low-sample (Req 20.13)."""
    out = ["## Confidence intervals", ""]
    if b is None:
        out.append(f"Bootstrap intervals: {NOT_AVAILABLE} (the run recorded none).")
        return out

    def low(text: str) -> str:
        return f"{text} {LOW_SAMPLE_LABEL}" if b.low_sample else text

    out += [
        f"{b.confidence_pct}% percentile bootstrap intervals: {b.resamples:,} resamples of the "
        f"{b.trade_count} accepted trade(s), drawn with replacement; seed {b.seed}.",
        "",
        table(
            ("Metric", f"{b.confidence_pct}% interval"),
            [
                (
                    f"Primary_Win_Rate ({b.primary_win_rate})",
                    low(_span(b.primary_win_rate_pct, "%")),
                ),
                ("Expectancy (R)", low(_span(b.expectancy_r, " R"))),
            ],
        ),
    ]
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
        _intervals(report.intervals),
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
    return _join(parts)


def _join(parts: Sequence[Sequence[str]]) -> str:
    return "\n\n".join("\n".join(p) for p in parts) + "\n"


# ---------------------------------------------------------------- experiment reports


@dataclass(frozen=True, slots=True)
class ExperimentHeader:
    """The Req 20.15 header of an experiment report (``fse experiment ...``).

    ``trades`` is the trade count, or for a multi-configuration report a
    pointer to the per-row trade counts. ``holdout`` states whether any
    Holdout_Period session is included.
    """

    title: str
    sessions: tuple[date, ...]
    trades: str
    bar_interval_s: int
    decision_cadence: str
    trade_through_ticks: int
    slippage_ticks: int
    costs: tuple[tuple[str, Decimal, Decimal], ...]
    holdout: str
    config: str
    seed: int | None
    notes: tuple[str, ...] = ()


def config_costs(cfg: StrategyConfig) -> tuple[tuple[str, Decimal, Decimal], ...]:
    """Commission and exchange fee of each traded instrument of ``cfg``, by name."""
    out: list[tuple[str, Decimal, Decimal]] = []
    for instrument in sorted(EngineParams.from_sections(cfg).instruments):
        found = cfg.fills.costs_for(instrument)
        if found is not None:
            out.append((instrument, Decimal(found.commission), Decimal(found.exchange_fee)))
    return tuple(out)


def experiment_header(
    cfg: StrategyConfig,
    *,
    title: str,
    sessions: Sequence[date],
    trades: str,
    holdout: str,
    config: str,
    seed: int | None,
    decision_cadence: str | None = None,
    notes: Sequence[str] = (),
) -> ExperimentHeader:
    """The header of an experiment over ``cfg``'s data resolution, fills and costs."""
    return ExperimentHeader(
        title=title,
        sessions=tuple(sessions),
        trades=trades,
        bar_interval_s=BASE_INTERVAL_S,
        decision_cadence=(
            f"{cfg.time.decision_cadence_s} s" if decision_cadence is None else decision_cadence
        ),
        trade_through_ticks=cfg.fills.trade_through_ticks,
        slippage_ticks=cfg.fills.slippage_ticks,
        costs=config_costs(cfg),
        holdout=holdout,
        config=config,
        seed=seed,
        notes=tuple(notes),
    )


def holdout_excluded(period: HoldoutPeriod | None) -> str:
    """The header's Holdout_Period line for a sweep, ablation, cadence or walk-forward test."""
    if period is None:
        return "no; the Data_Cache holds no session with full data, so there is no Holdout_Period"
    return (
        f"no; every Holdout_Period session ({period.first} to {period.last}, "
        f"{len(period)} sessions) is excluded"
    )


def _experiment_header(h: ExperimentHeader) -> list[str]:
    touch = h.trade_through_ticks == 0
    title = f"# {h.title}" + (f": {TOUCH_FILLS_LABEL}" if touch else "")
    span = f"{h.sessions[0]} to {h.sessions[-1]}" if h.sessions else "no session"
    costs = "; ".join(
        f"{name} commission ${c} and exchange fee ${f} per contract per side"
        for name, c, f in h.costs
    )
    return [
        title,
        "",
        "## Run",
        "",
        f"- Sessions: {span}",
        f"- Session count: {len(h.sessions)}",
        f"- Trade count: {h.trades}",
        f"- Data resolution: {h.bar_interval_s} s bars; Decision_Cadence {h.decision_cadence}",
        f"- Fill_Simulator: trade-through {h.trade_through_ticks} tick(s); "
        f"slippage {h.slippage_ticks} tick(s) on stop and market fills"
        + (f"; {TOUCH_FILLS_LABEL}" if touch else ""),
        f"- Costs: {costs or 'none configured'}",
        f"- Holdout_Period sessions included: {h.holdout}",
        f"- Strategy_Config: {h.config}; seed {h.seed}",
        *(f"- {n}" for n in h.notes),
        "",
        MEASURED_STATEMENT,
    ]


def _raw(value: object) -> object:
    """A JSON value as :func:`number` takes it: ``"a/b"`` strings become fractions."""
    if isinstance(value, str) and "/" in value:
        return Fraction(value)
    return value


def shown(value: object, *, suffix: str = "", prefix: str = "", places: int = _PLACES) -> str:
    """A JSON report value: ``null`` and "not available" are "not available"."""
    if value is None or value == NOT_AVAILABLE:
        return NOT_AVAILABLE
    if value == NOT_APPLICABLE:
        return NOT_APPLICABLE
    return number(_raw(value), suffix=suffix, prefix=prefix, places=places)


def _prob(value: object, insufficient: bool = False) -> str:
    """A Combine_Pass probability (0 to 1, four places), labeled per Req 21.11."""
    text = shown(value, places=_PROB_PLACES)
    return f"{text} {PASS_INSUFFICIENT_LABEL}" if insufficient and text != NOT_AVAILABLE else text


def _low(text: str, low: bool) -> str:
    """``text`` with the Req 20.13 low-sample label when ``low`` and it is a number."""
    if not low or text in (NOT_AVAILABLE, NOT_APPLICABLE):
        return text
    return f"{text} {LOW_SAMPLE_LABEL}"


def _ranking(ranking: Mapping[str, Any] | None) -> list[str]:
    if not ranking:
        return []
    order = ", ".join(f"{i}. {name}" for i, name in enumerate(ranking["order"], start=1))
    return ["", f"Ranking by {ranking['objective']} (Req 20.14 tie rules): {order}"]


def _configurations(comparison: Mapping[str, Any]) -> list[str]:
    """Each configuration's status, trade count and labels, and the distinct count."""
    rows = []
    for c in comparison["configurations"]:
        labels = []
        if c["insufficient_sample"]:
            labels.append("insufficient sample")
        m = c["metrics"]
        if m is not None and m["low_sample"]:
            labels.append("low sample")
        status = c["status"] if c["error"] is None else f"{c['status']}: {c['error']}"
        trades = NOT_AVAILABLE if c["trade_count"] is None else c["trade_count"]
        rows.append((c["name"], c["config_hash"][:12], status, trades, ", ".join(labels)))
    return [
        "## Configurations",
        "",
        f"Distinct configurations evaluated: {comparison['distinct_configurations']} "
        "(configurations labeled insufficient sample and failed ones included). A configuration "
        f"with fewer than {comparison['min_trades']} accepted trades is labeled insufficient "
        "sample and is not ranked.",
        "",
        table(("Configuration", "Config hash", "Status", "Trades", "Labels"), rows),
        *_ranking(comparison.get("ranking")),
    ]


_ABLATION_METRICS: Final[Mapping[str, tuple[str, str, str]]] = {
    "trades_per_day": ("Trades per day", "", ""),
    "win_rate": ("Primary_Win_Rate", "", "%"),
    "expectancy_r": ("Expectancy", "", " R"),
    "profit_factor": ("Profit factor", "", ""),
    "max_drawdown_usd": ("Maximum drawdown", "$", ""),
    "pass_probability": ("Combine_Pass probability", "", ""),
}
_LOW_SAMPLE_METRICS: Final = frozenset({"win_rate", "expectancy_r"})


def render_ablation(
    header: ExperimentHeader, ablation: Mapping[str, Any], comparison: Mapping[str, Any]
) -> str:
    """The ablation report (Req 19.14-19.16): base, variant and change per metric."""
    by_name = {c["name"]: c for c in comparison["configurations"]}

    def low(name: str) -> bool:
        m = by_name[name]["metrics"]
        return bool(m is not None and m["low_sample"])

    base = ablation["base"]
    rows = []
    for v in ablation["variants"]:
        for metric in ablation["metrics"]:  # Req 19.14 order; the cells' keys are sorted
            cell = v["cells"][metric]
            label, prefix, suffix = _ABLATION_METRICS[metric]
            if metric == "pass_probability":
                b = _prob(cell["base"], base["pass_estimate_insufficient_sample"])
                x = _prob(cell["variant"], v["pass_estimate_insufficient_sample"])
                change = shown(cell["change"], places=_PROB_PLACES)
            else:
                checked = metric in _LOW_SAMPLE_METRICS
                b = _low(shown(cell["base"], prefix=prefix, suffix=suffix),
                         checked and low(base["name"]))  # fmt: skip
                x = _low(shown(cell["variant"], prefix=prefix, suffix=suffix),
                         checked and low(v["name"]))  # fmt: skip
                change = shown(cell["change"], prefix=prefix, suffix=suffix)
            rows.append((v["gate_id"], label, b, x, change))
    body = [
        "## Ablation",
        "",
        f"Base configuration: {base['name']} ({base['status']}). Each variant disables one "
        "enabled Gate and keeps every other value. Change = variant minus base; a change is "
        f"{NOT_AVAILABLE} when either value is not a number.",
        "",
        table(("Disabled Gate", "Metric", "Base", "Variant", "Change"), rows)
        if rows
        else "No Gate is enabled, so there is no variant.",
    ]
    return _join([_experiment_header(header), _configurations(comparison), body, _files(
        ("ablation.json", "the rows above, to six decimal places"),
        ("comparison.json", "the session list, seed and every configuration's metrics"),
    )])  # fmt: skip


def _interval_text(intervals: Mapping[str, Any] | None, low: bool) -> str:
    if intervals is None:
        return NOT_AVAILABLE

    def span(raw: object, suffix: str) -> str:
        if raw == NOT_APPLICABLE or not isinstance(raw, Mapping):
            return NOT_APPLICABLE
        return f"{shown(raw['lower'], suffix=suffix)} to {shown(raw['upper'], suffix=suffix)}"

    text = (
        f"win rate {span(intervals['primary_win_rate_pct'], '%')}; "
        f"expectancy {span(intervals['expectancy_r'], ' R')}"
    )
    return f"{text} {LOW_SAMPLE_LABEL}" if low else text


def render_frontier(header: ExperimentHeader, frontier: Mapping[str, Any]) -> str:
    """The frontier table (Req 20.9), Pareto set (20.10), reference list (20.11), ranking."""
    rows = []
    for r in frontier["rows"]:
        low = bool(r["low_sample"])
        labels = [r["status"] if r["error"] is None else f"failed: {r['error']}"]
        if r["insufficient_sample"]:
            labels.append("insufficient sample")
        rows.append(
            (
                r["exit_mode"], NOT_APPLICABLE if r["r_multiple"] is None else r["r_multiple"],
                r["min_reward_risk"],
                NOT_AVAILABLE if r["trade_count"] is None else r["trade_count"],
                _low(shown(r["primary_win_rate_pct"], suffix="%"), low),
                _low(shown(r["expectancy_r"], suffix=" R"), low), shown(r["profit_factor"]),
                shown(r["max_drawdown_usd"], prefix="$"), shown(r["trades_per_day"]),
                _prob(r["pass_probability"], r["pass_estimate_insufficient_sample"]),
                r["pareto"], _interval_text(r["intervals"], low), ", ".join(labels),
            )
        )  # fmt: skip
    ref = frontier["reference"]
    reference: list[str] = [
        "## Reference win rate",
        "",
        f"Configurations with a Primary_Win_Rate at or above {shown(ref['win_rate_pct'])}%:",
        "",
    ]
    if ref["met"]:
        reference.append(
            table(
                ("Configuration", "Primary_Win_Rate", "Expectancy", "Profit factor",
                 "Combine_Pass probability"),
                [
                    (
                        x["name"], _low(shown(x["primary_win_rate_pct"], suffix="%"),
                                        x["low_sample"]),
                        _low(shown(x["expectancy_r"], suffix=" R"), x["low_sample"]),
                        shown(x["profit_factor"]),
                        _prob(x["pass_probability"], x["pass_estimate_insufficient_sample"]),
                    )
                    for x in ref["rows"]
                ],
            )
        )  # fmt: skip
    else:
        reference.append(f"{ref['statement'].capitalize()}.")
    members = ", ".join(frontier["pareto_set"]) or "none"
    excluded = ", ".join(frontier["pareto_excluded"]) or "none"
    body = [
        "## Frontier",
        "",
        f"Distinct configurations evaluated: {frontier['distinct_configurations']} "
        "(configurations labeled insufficient sample included). Intervals are 95% percentile "
        f"bootstrap intervals ({frontier['definition']['bootstrap_resamples']:,} resamples).",
        "",
        table(
            (
                "Exit_Mode",
                "R multiple",
                "min_reward_risk",
                "Trades",
                "Primary_Win_Rate",
                "Expectancy",
                "Profit factor",
                "Maximum drawdown",
                "Trades per day",
                "Combine_Pass probability",
                "Pareto",
                "95% intervals",
                "Status",
            ),
            rows,
        ),
        "",
        f"Pareto set (expectancy in R, Primary_Win_Rate, Combine_Pass probability): {members}",
        f"Excluded from the Pareto set (a value not applicable or not available): {excluded}",
        *_ranking(frontier.get("ranking")),
    ]
    return _join([_experiment_header(header), body, reference, _files(
        ("frontier.json", "the rows above, to six decimal places"),
        ("comparison.json", "the session list, seed and every configuration's metrics"),
    )])  # fmt: skip


def _estimate(est: Mapping[str, Any]) -> list[str]:
    """A pass estimate's counts, shares and days to pass (Req 21.6-21.8, 21.11)."""
    low = bool(est["insufficient_sample"])
    rows = [
        ("Paths", f"{est['paths']:,}"),
        ("Passing paths", est["passed"]),
        ("Failing paths", est["failed"]),
        ("Unresolved paths", est["unresolved"]),
        ("Pass probability", _prob(est["pass_probability"], low)),
        ("Failure probability", _prob(est["failure_probability"], low)),
        ("Unresolved share", _prob(est["unresolved_share"], low)),
        ("Median trading days to pass", shown(est["median_days_to_pass"], places=1)),
        ("90th-percentile trading days to pass (nearest rank)", shown(est["p90_days_to_pass"])),
    ]
    lines = [
        f"Paths of up to {est['max_days']} trading days, drawn with replacement from "
        f"{est['sessions']} session(s); seed {est['seed']}.",
    ]
    if low:
        lines.append(
            f"Insufficient sample: the run has fewer than {est['min_sessions']} sessions, so "
            f"every estimate value is labeled {PASS_INSUFFICIENT_LABEL}."
        )
    lines.append(
        f"Sessions in which the run's own Combine_Attempt ended mid-session "
        f"(truncated_by_run_account): {est['truncated_by_run_account']}."
    )
    return [*lines, "", table(("Value", "Estimate"), rows)]


def render_pass_estimate(header: ExperimentHeader, estimate: Mapping[str, Any]) -> str:
    """The Combine pass estimate report of one completed backtest (Req 21.6-21.12)."""
    body = [
        "## Combine pass estimate",
        "",
        f"Source run: {estimate['source_run_id']}.",
        *_estimate(estimate),
    ]
    return _join([_experiment_header(header), body, _files(
        ("pass_estimate.json", "the counts and exact shares above"),
    )])  # fmt: skip


def _pooled(name: str, p: Mapping[str, Any], min_sample_trades: int) -> tuple[str, ...]:
    trades = p["trade_count"]
    low = trades is not None and trades < min_sample_trades
    est = p["pass_estimate"]
    return (
        name,
        str(p["runs"]),
        str(p["sessions"]),
        NOT_AVAILABLE if trades is None else str(trades),
        _low(shown(p["expectancy_r"], suffix=" R"), low),
        _low(shown(p["win_rate_pct"], suffix="%"), low),
        _prob(p["pass_probability"], est is not None and est["insufficient_sample"]),
    )


def render_walkforward(
    header: ExperimentHeader, walkforward: Mapping[str, Any], *, min_sample_trades: int
) -> str:
    """The walk-forward report (Req 22.14-22.15): pooled results beside the window pairs."""
    pairs = [
        (
            p["index"], f"{p['train']['first']} to {p['train']['last']} ({p['train']['sessions']})",
            f"{p['test']['first']} to {p['test']['last']} ({p['test']['sessions']})",
            p["configurations_evaluated"], ", ".join(p["insufficient_sample"]) or "none",
            ", ".join(p["failed"]) or "none",
            p["selected"] if p["selected"] is not None else p["selection"],
        )
        for p in walkforward["pairs"]
    ]  # fmt: skip
    body = [
        "## Walk-forward results",
        "",
        f'Selection objective: {walkforward["objective"]}. "No selection" window pairs: '
        f"{walkforward['no_selection_pairs']} (their test sessions count as zero-trade "
        "sessions). Distinct configurations evaluated: "
        f"{walkforward['distinct_configurations']} (configurations labeled insufficient "
        "sample included).",
        "",
        table(
            (
                "Results",
                "Runs",
                "Sessions",
                "Trades",
                "Expectancy",
                "Primary_Win_Rate",
                "Combine_Pass probability",
            ),
            [
                _pooled(
                    "Out-of-sample (concatenated test windows)",
                    walkforward["out_of_sample"],
                    min_sample_trades,
                ),
                _pooled(
                    "In-sample (selected configurations on their training windows)",
                    walkforward["in_sample"],
                    min_sample_trades,
                ),
            ],
        ),
        "",
        "## Window pairs",
        "",
        table(
            (
                "Pair",
                "Training window",
                "Test window",
                "Configurations evaluated",
                "Insufficient sample",
                "Failed",
                "Selected",
            ),
            pairs,
        )
        if pairs
        else "No window pair.",
    ]
    return _join([_experiment_header(header), body, _files(
        ("walkforward.json", "the pairs and pooled results above, to six decimal places"),
    )])  # fmt: skip


_CADENCE_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("setup_keys", "Distinct Setup_Keys"),
    ("entry_fills", "Entry fills"),
    ("trades_per_session", "Trades per session"),
    ("expectancy_r", "Expectancy"),
)


def render_cadence(
    header: ExperimentHeader,
    cadence: Mapping[str, Any],
    comparison: Mapping[str, Any],
    *,
    min_sample_trades: int,
) -> str:
    """The cadence comparison report (Req 18.10)."""

    def cells(values: Mapping[str, Any], low: bool) -> list[str]:
        out = []
        for key, _ in _CADENCE_COLUMNS:
            text = shown(values[key], suffix=" R" if key == "expectancy_r" else "")
            out.append(_low(text, low and key == "expectancy_r"))
        return out

    rows = []
    for c in cadence["cadences"]:
        fills = c["entry_fills"]
        low = isinstance(fills, int) and fills < min_sample_trades
        rows.append((f"{c['cadence_s']} s", c["status"], *cells(c, low)))
    diffs = [
        (f"{d['shorter_s']} s minus {d['longer_s']} s", *cells(d, False))
        for d in cadence["differences"]
    ]
    excluded = [
        (
            e["session"],
            NOT_AVAILABLE if e["snapshot_interval_s"] is None else f"{e['snapshot_interval_s']} s",
            NOT_AVAILABLE if e["bar_interval_s"] is None else f"{e['bar_interval_s']} s",
        )
        for e in cadence["excluded_sessions"]
    ]
    names = [label for _, label in _CADENCE_COLUMNS]
    body = [
        "## Cadence comparison",
        "",
        f"One Strategy_Config at each cadence ({', '.join(str(c) for c in cadence['cadences_s'])}"
        " s) on the same sessions: those whose stored Snapshot and bar intervals are both no "
        "longer than the shortest cadence.",
        "",
        table(("Cadence", "Status", *names), rows),
        "",
        "Differences (the shorter cadence's value minus the longer cadence's):",
        "",
        table(("Pair", *names), diffs),
        "",
        "Included sessions: " + (", ".join(cadence["included_sessions"]) or "none"),
        "",
        "Excluded sessions (stored interval longer than the shortest cadence):",
        "",
        table(("Session", "Stored Snapshot interval", "Stored bar interval"), excluded)
        if excluded
        else "none",
    ]
    return _join([_experiment_header(header), _configurations(comparison), body, _files(
        ("cadence.json", "the values above, to six decimal places"),
        ("comparison.json", "the session list, seed and every configuration's metrics"),
    )])  # fmt: skip


def render_holdout(
    header: ExperimentHeader, holdout: Mapping[str, Any], *, min_sample_trades: int
) -> str:
    """The holdout evaluation report (Req 22.4-22.8)."""
    e = holdout["entry"]
    low = e["trade_count"] < min_sample_trades
    lines = [
        "## Holdout evaluation",
        "",
        f"The named Strategy_Config ran unchanged on the Holdout_Period sessions only "
        f"({e['holdout']['first']} to {e['holdout']['last']}, {e['holdout_sessions']} sessions).",
    ]
    if holdout["warning"] is not None:
        lines += ["", f"Warning: {holdout['warning']}."]
    else:
        lines += ["", "No earlier Holdout_Log entry shares a session with this Holdout_Period."]
    lines += [
        "",
        table(
            ("Value", "Result"),
            [
                ("Trade count", e["trade_count"]),
                ("Expectancy", _low(shown(e["expectancy_r"], suffix=" R"), low)),
                (
                    f"Primary_Win_Rate ({e['primary_win_rate']})",
                    _low(shown(e["win_rate_pct"], suffix="%"), low),
                ),
                (
                    "Combine_Pass probability",
                    _prob(e["pass_probability"], e["pass_estimate_insufficient_sample"]),
                ),
                ("Overlapping Holdout_Log entries", holdout["overlapping_entries"]),
            ],
        ),
        "",
        f"Holdout_Log entry appended to {holdout['log_path']}.",
    ]
    return _join([_experiment_header(header), lines, _files(
        ("holdout.json", "the Holdout_Log entry and the overlap count"),
    )])  # fmt: skip


def _files(*files: tuple[str, str]) -> list[str]:
    return ["## Files", "", *(f"- `{name}`: {what}" for name, what in files)]
