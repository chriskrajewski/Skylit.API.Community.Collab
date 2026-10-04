"""The Gate_Funnel: final statuses, per-Gate counts and Shadow_Trade edges (design §19).

:func:`gate_funnel` turns the :class:`~fse.backtest.shadow.SetupRecord` of every
Setup_Key in a run, and the run's accepted trades, into a :class:`GateFunnel`
(Req 19.1, 19.3-19.7, 19.10-19.12, 19.17). It is pure: no I/O, no clock.

- **Statuses**: one count per final status, in :data:`FINAL_STATUSES` order;
  they sum to the number of distinct Setup_Keys (Req 19.1).
- **Per enabled Gate**, in the Strategy_Config Gate order, over the rejected
  keys only (untapped keys never count, Req 19.5): the keys failing the Gate,
  the keys where it was the only failing Gate, and the keys where it was the
  first failing Gate. The first-failing counts sum to the rejected count
  (Req 19.6).
- **Co-rejection matrix** over the same Gates: the cell of Gates A and B
  counts the rejected keys failing both, so the matrix is symmetric and its
  diagonal is each Gate's failing count (Req 19.7).
- **Only-rejected Shadow_Trades** per Gate: the rejected keys whose only
  failing Gate is that Gate. Their filled Shadow_Trades give a count, a win
  rate (R_Multiple above 0) and a mean R_Multiple; the shadows whose entry did
  not fill are counted beside them (Req 19.10). The accepted statistics are
  the same figures for the trades of keys with final status ``filled``.
- **Flags** (Req 19.11-19.12): "insufficient sample" when a Gate's filled
  only-rejected count is below the minimum sample or the run has no filled
  accepted trade; otherwise "no measured edge" when their mean R_Multiple is
  at least the accepted mean. No flag changes the Strategy_Config.
- **Per session** (Req 19.17): the distinct Setup_Keys and the three Gates that
  appear most often in the session's rejected keys (one count per failing Gate
  per key), ties broken by Gate order.

Rates are percentages and means are exact ``Fraction`` values; an undefined
value is ``NotApplicable`` (Req 20.16). :func:`funnel_to_jsonable` rounds them
for the file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal

from fse.analytics.metrics import round_fraction
from fse.backtest.shadow import FINAL_STATUSES, FinalStatus, SetupRecord
from fse.engine.types import NotApplicable, SetupKey, Trade
from fse.logio.canonical_json import JsonValue, to_jsonable

__all__ = [
    "FUNNEL_FLAGS",
    "TOP_GATES",
    "FunnelError",
    "FunnelFlag",
    "GateFunnel",
    "GateRow",
    "SessionTop",
    "TradeStats",
    "funnel_to_jsonable",
    "gate_funnel",
    "trade_stats",
]

type FunnelFlag = Literal["no_measured_edge", "insufficient_sample"]

FUNNEL_FLAGS: Final[tuple[FunnelFlag, ...]] = ("no_measured_edge", "insufficient_sample")
TOP_GATES: Final = 3
"""The per-session list holds at most this many Gates (Req 19.17)."""

_NA: Final = NotApplicable()
_HUNDRED: Final = 100


class FunnelError(ValueError):
    """The records, trades or Gates given to :func:`gate_funnel` are inconsistent."""


@dataclass(frozen=True, slots=True)
class TradeStats:
    """Count, wins, win rate (percent) and mean R_Multiple of filled trades (Req 19.10).

    ``not_filled`` counts Shadow_Trades whose entry did not fill; it is 0 for
    accepted trades. Win rate and mean are ``NotApplicable`` with no filled trade.
    """

    filled: int
    wins: int
    win_rate_pct: Fraction | NotApplicable
    mean_r: Fraction | NotApplicable
    not_filled: int = 0


@dataclass(frozen=True, slots=True)
class GateRow:
    """One enabled Gate's funnel counts, its only-rejected Shadow_Trades and its flag."""

    gate_id: str
    failing: int
    only_failing: int
    first_failing: int
    shadow: TradeStats
    flag: FunnelFlag | None


@dataclass(frozen=True, slots=True)
class SessionTop:
    """One session's distinct Setup_Keys and its most frequent failing Gates (Req 19.17)."""

    session: date
    setup_keys: int
    top: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class GateFunnel:
    """The Gate_Funnel of one run (see the module notes).

    ``co_rejection[i][j]`` is the cell of ``gates[i]`` and ``gates[j]``.
    ``cancel_causes`` counts the cancelled keys by the first item of their
    cause, most frequent first, then by name.
    """

    setup_keys: int
    status_counts: tuple[tuple[FinalStatus, int], ...]
    gates: tuple[GateRow, ...]
    co_rejection: tuple[tuple[int, ...], ...]
    accepted: TradeStats
    min_sample: int
    sessions: tuple[SessionTop, ...]
    cancel_causes: tuple[tuple[str, int], ...]

    def count(self, status: FinalStatus) -> int:
        """The number of Setup_Keys with final status ``status``."""
        return dict(self.status_counts)[status]

    def gate(self, gate_id: str) -> GateRow:
        for row in self.gates:
            if row.gate_id == gate_id:
                return row
        raise FunnelError(f"{gate_id!r} is not an enabled Gate of this funnel")


# ---------------------------------------------------------------- computation


def trade_stats(trades: Iterable[Trade], *, not_filled: int = 0) -> TradeStats:
    """:class:`TradeStats` of ``trades``: a win is an R_Multiple above 0 (Req 19.10)."""
    rs = [t.r_multiple for t in trades]
    wins = sum(1 for r in rs if r > 0)
    if not rs:
        return TradeStats(0, 0, _NA, _NA, not_filled)
    mean = sum((Fraction(r) for r in rs), Fraction(0)) / len(rs)
    return TradeStats(len(rs), wins, Fraction(_HUNDRED * wins, len(rs)), mean, not_filled)


def _flag(shadow: TradeStats, accepted: TradeStats, min_sample: int) -> FunnelFlag | None:
    if shadow.filled < min_sample or accepted.filled == 0:
        return "insufficient_sample"
    assert not isinstance(shadow.mean_r, NotApplicable)  # filled >= min_sample >= 1
    assert not isinstance(accepted.mean_r, NotApplicable)
    return "no_measured_edge" if shadow.mean_r >= accepted.mean_r else None


def gate_funnel(
    records: Sequence[SetupRecord],
    accepted_trades: Iterable[Trade],
    gates: Sequence[str],
    *,
    min_sample: int,
    sessions: Collection[date] = (),
) -> GateFunnel:
    """The Gate_Funnel of a run (see the module notes).

    ``gates`` are the enabled Gate ids in the Strategy_Config Gate order;
    ``sessions`` the run's evaluated sessions (every record's session is added).
    Raises :class:`FunnelError` for a repeated Setup_Key, a failing Gate that is
    not in ``gates``, or a minimum sample below 1.
    """
    if isinstance(min_sample, bool) or not isinstance(min_sample, int) or min_sample < 1:
        raise FunnelError(f"the minimum sample must be a whole number of at least 1: {min_sample}")
    order = {g: i for i, g in enumerate(gates)}
    if len(order) != len(gates):
        raise FunnelError("the enabled Gates must be distinct")
    keys: set[SetupKey] = set()
    for r in records:
        if r.key in keys:
            raise FunnelError(f"Setup_Key {r.key} has more than one final status")
        keys.add(r.key)
        unknown = [g for g in r.failing if g not in order]
        if unknown:
            raise FunnelError(f"Setup_Key {r.key} fails Gates that are not enabled: {unknown}")

    statuses = Counter(r.status for r in records)
    rejected = [r for r in records if r.status == "rejected"]
    filled_keys = {r.key for r in records if r.status == "filled"}
    accepted = trade_stats(
        t for t in accepted_trades if not t.shadow and t.setup_key in filled_keys
    )

    n = len(gates)
    matrix = [[0] * n for _ in range(n)]
    failing = [0] * n
    only = [0] * n
    first = [0] * n
    only_shadows: list[list[SetupRecord]] = [[] for _ in range(n)]
    for r in rejected:
        idx = sorted(order[g] for g in set(r.failing))
        for i in idx:
            failing[i] += 1
            for j in idx:
                matrix[i][j] += 1
        if idx:
            first[idx[0]] += 1
        if len(idx) == 1:
            only[idx[0]] += 1
            only_shadows[idx[0]].append(r)

    rows: list[GateRow] = []
    for i, gate_id in enumerate(gates):
        shadows = [r.shadow for r in only_shadows[i] if r.shadow is not None]
        stats = trade_stats(
            (s.trade for s in shadows if s.trade is not None),
            not_filled=sum(1 for s in shadows if s.trade is None),
        )
        rows.append(
            GateRow(
                gate_id, failing[i], only[i], first[i], stats, _flag(stats, accepted, min_sample)
            )
        )

    by_session: dict[date, list[SetupRecord]] = {d: [] for d in sorted(sessions)}
    for r in records:
        by_session.setdefault(r.key.session, []).append(r)
    tops: list[SessionTop] = []
    for session in sorted(by_session):
        recs = by_session[session]
        counts = Counter(g for r in recs if r.status == "rejected" for g in set(r.failing))
        ranked = sorted(counts.items(), key=lambda item: (-item[1], order[item[0]]))
        tops.append(SessionTop(session, len(recs), tuple(ranked[:TOP_GATES])))

    causes = Counter(r.cause[0] for r in records if r.status == "cancelled" and r.cause)
    return GateFunnel(
        setup_keys=len(records),
        status_counts=tuple((s, statuses.get(s, 0)) for s in FINAL_STATUSES),
        gates=tuple(rows),
        co_rejection=tuple(tuple(row) for row in matrix),
        accepted=accepted,
        min_sample=min_sample,
        sessions=tuple(tops),
        cancel_causes=tuple(sorted(causes.items(), key=lambda item: (-item[1], item[0]))),
    )


# ---------------------------------------------------------------- JSON


def _number(value: Fraction | NotApplicable, places: int) -> Decimal | NotApplicable:
    return value if isinstance(value, NotApplicable) else round_fraction(value, places)


def _stats(s: TradeStats, places: int) -> dict[str, object]:
    return {
        "filled": s.filled,
        "wins": s.wins,
        "win_rate_pct": _number(s.win_rate_pct, places),
        "mean_r": _number(s.mean_r, places),
        "not_filled": s.not_filled,
    }


def funnel_to_jsonable(funnel: GateFunnel, *, places: int = 6) -> JsonValue:
    """``funnel`` as canonical JSON values; each ``Fraction`` is rounded to ``places``.

    ``NotApplicable`` encodes as ``{}``, as every engine sentinel does.
    """
    gate_ids = [row.gate_id for row in funnel.gates]
    return to_jsonable(
        {
            "setup_keys": funnel.setup_keys,
            "status_counts": dict(funnel.status_counts),
            "min_sample": funnel.min_sample,
            "accepted": _stats(funnel.accepted, places),
            "gates": [
                {
                    "gate_id": row.gate_id,
                    "failing": row.failing,
                    "only_failing": row.only_failing,
                    "first_failing": row.first_failing,
                    "only_rejected_shadows": _stats(row.shadow, places),
                    "flag": row.flag,
                }
                for row in funnel.gates
            ],
            "co_rejection": {
                "gates": gate_ids,
                "rows": [list(row) for row in funnel.co_rejection],
            },
            "sessions": [
                {
                    "session": s.session,
                    "setup_keys": s.setup_keys,
                    "top_gates": [{"gate_id": g, "count": c} for g, c in s.top],
                }
                for s in funnel.sessions
            ],
            "cancel_causes": [{"cause": c, "count": n} for c, n in funnel.cancel_causes],
        }
    )
