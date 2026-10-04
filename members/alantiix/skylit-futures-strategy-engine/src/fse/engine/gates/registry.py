"""The Gate_Evaluator: evaluation in the configured order and the Grade (design §11).

:func:`evaluate` measures all 27 Gates of :data:`REGISTRY` for one
Candidate_Setup at its Decision_Time, in ``gates.order``, including every Gate
after a failure (Req 11.2, 19.2). Each Gate gives one :class:`GateResult`
with the Setup_Key, the Decision_Time, the Gate id, the result, the measured
value and the threshold (Req 11.3). A disabled Gate is still measured and
recorded ``disabled`` (Req 11.4).

**Rejection_Reasons** (Glossary, Req 8.11, 19.2): one per failing enabled
Gate, in Gate order, each with the Gate's measured value and threshold. An
unpriced Candidate_Setup (``MissingPrice`` for its source, Req 8.11) also
gets :data:`NO_CONVERSION_PRICE` first, naming the missing price.

**Grade** (Req 11.12), exactly one per Candidate_Setup:

- ``A_Plus``: no enabled Gate fails, including when every Gate is disabled;
- ``Alert_2R``: the only failing enabled Gate is ``min_reward_risk``, with a
  measured value of at least ``min_reward_risk.alert_min`` (default 2.0);
- ``Pass``: every other Candidate_Setup.

An unpriced Candidate_Setup is always ``Pass``: it has no entry to place
(Req 8.11), even when every Gate that needs its price is disabled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.gates import GATE_IDS, GateId
from fse.engine.gates.catalog import (
    CATALOG,
    DataUnavailable,
    Gate,
    GateContext,
    GateParams,
    Measured,
    NoConversionPrice,
    Threshold,
    exact_decimal,
)
from fse.engine.types import CandidateSetup, MissingPrice, SetupKey, Snapshot, Unavailable
from fse.timekit import Instant

__all__ = [
    "GATES",
    "GATE_RESULTS",
    "GRADES",
    "NO_CONVERSION_PRICE",
    "REGISTRY",
    "GateEvaluation",
    "GateOutcome",
    "GateResult",
    "Grade",
    "RejectionReason",
    "check_context",
    "evaluate",
    "gate",
    "grade_of",
    "trinity_count",
]

type GateOutcome = Literal["pass", "fail", "disabled"]
type Grade = Literal["A_Plus", "Alert_2R", "Pass"]

GATE_RESULTS: Final[frozenset[str]] = frozenset({"pass", "fail", "disabled"})
GRADES: Final[tuple[Grade, ...]] = ("A_Plus", "Alert_2R", "Pass")
"""The Grades, best first (the Finding_Card order)."""

NO_CONVERSION_PRICE: Final = "no_conversion_price"
"""The Rejection_Reason of an unpriced Candidate_Setup (Req 8.11)."""

_UNPRICED_THRESHOLD: Final = "a conversion price for the source symbol and metric"

REGISTRY: Final[tuple[Gate, ...]] = CATALOG
"""The 27 Gates, in the Req 11.1 order."""

GATES: Final[Mapping[str, Gate]] = MappingProxyType({g.id: g for g in REGISTRY})
"""The Gates by id."""


def gate(gate_id: str) -> Gate:
    """The Gate with ``gate_id``; ``ValueError`` for an unknown id."""
    found = GATES.get(gate_id)
    if found is None:
        raise ValueError(f"{gate_id!r} is not one of the Gate ids {GATE_IDS}")
    return found


# ---------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class GateResult:
    """One Gate's result for one Candidate_Setup at one Decision_Time (Req 11.3)."""

    setup_key: SetupKey
    t: Instant
    gate_id: GateId
    result: GateOutcome
    measured: Measured
    threshold: Threshold

    def __post_init__(self) -> None:
        if self.gate_id not in GATES:
            raise ValueError(f"GateResult.gate_id {self.gate_id!r} is not a Gate id")
        if self.result not in GATE_RESULTS:
            raise ValueError(f"GateResult.result must be one of {sorted(GATE_RESULTS)}")


@dataclass(frozen=True, slots=True)
class RejectionReason:
    """Why a Candidate_Setup is rejected: a failing Gate's id, or ``no_conversion_price``."""

    reason: str
    measured: Measured
    threshold: Threshold


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    """The Gate results, Rejection_Reasons and Grade of one Candidate_Setup at ``t``.

    ``results`` holds one entry per Gate, in the configured order.
    """

    setup_key: SetupKey
    t: Instant
    results: tuple[GateResult, ...]
    rejections: tuple[RejectionReason, ...]
    grade: Grade

    @property
    def failing(self) -> tuple[GateId, ...]:
        """The failing enabled Gates, in the configured order."""
        return tuple(r.gate_id for r in self.results if r.result == "fail")

    def result(self, gate_id: str) -> GateResult:
        """The result of ``gate_id``; ``ValueError`` for an unknown id."""
        for r in self.results:
            if r.gate_id == gate_id:
                return r
        raise ValueError(f"{gate_id!r} is not one of the Gate ids {GATE_IDS}")


def trinity_count(evaluation: GateEvaluation) -> int | Unavailable:
    """The trinity_agreement measured count, as the Position_Sizer reads it (Req 14.6, 14.9)."""
    measured = evaluation.result("trinity_agreement").measured
    if isinstance(measured, bool) or not isinstance(measured, int):
        reason = measured.reason if isinstance(measured, DataUnavailable) else str(measured)
        return Unavailable(f"trinity_agreement has no count: {reason}")
    return measured


# ---------------------------------------------------------------- evaluation


def check_context(c: CandidateSetup, ctx: GateContext) -> None:
    """Raise ``ValueError`` unless ``c`` was detected in ``ctx``.

    ``c`` must be at ``ctx.t`` and in its session, and its source Snapshot and
    converted level must be the context's.
    """
    if c.t != ctx.t:
        raise ValueError(f"Candidate_Setup at {c.t} evaluated in a context at {ctx.t}")
    if c.key.session != ctx.session:
        raise ValueError(f"Candidate_Setup of session {c.key.session}, context of {ctx.session}")
    snap = ctx.detect.map_state.get(c.source.symbol, c.source.metric)
    if not isinstance(snap, Snapshot) or c.source.strike not in snap.strikes:
        raise ValueError(
            f"the context has no {c.source.symbol} {c.source.metric} Snapshot "
            f"with strike {c.source.strike}"
        )
    cm = ctx.detect.converted[(c.source.symbol, c.source.metric)]
    if c.source.level is not None and (
        isinstance(cm, MissingPrice) or cm.level_of(c.source.strike) != c.source.level
    ):
        raise ValueError(f"Candidate_Setup {c.key} has a level the context does not give")


def _alert(measured: Measured, alert_min: float) -> bool:
    return isinstance(measured, float) and exact_decimal(measured) >= exact_decimal(alert_min)


def grade_of(results: Iterable[GateResult], *, priced: bool, alert_min: float) -> Grade:
    """The Grade from the Gate results (Req 11.12); an unpriced setup is ``Pass``."""
    failing = [r for r in results if r.result == "fail"]
    if not priced:
        return "Pass"
    if not failing:
        return "A_Plus"
    only = failing[0]
    if len(failing) == 1 and only.gate_id == "min_reward_risk" and _alert(only.measured, alert_min):
        return "Alert_2R"
    return "Pass"


def evaluate(c: CandidateSetup, ctx: GateContext, p: GateParams) -> GateEvaluation:
    """Every Gate for ``c`` at ``ctx.t`` in ``p.gates.order``, the rejections and the Grade.

    Raises ``ValueError`` when ``c`` does not belong to ``ctx``
    (:func:`check_context`).
    """
    check_context(c, ctx)
    gates_cfg = p.gates
    results: list[GateResult] = []
    rejections: list[RejectionReason] = []
    if not c.priced:
        factor = c.inputs.conversion_factor
        name = factor.symbol_or_contract if isinstance(factor, MissingPrice) else c.source.symbol
        rejections.append(
            RejectionReason(NO_CONVERSION_PRICE, NoConversionPrice(name), _UNPRICED_THRESHOLD)
        )
    for gate_id in gates_cfg.order:
        m = GATES[gate_id].measure(c, ctx, p)
        outcome: GateOutcome
        if not gates_cfg.enabled(gate_id):
            outcome = "disabled"
        elif m.failed:
            outcome = "fail"
            rejections.append(RejectionReason(gate_id, m.measured, m.threshold))
        else:
            outcome = "pass"
        results.append(GateResult(c.key, c.t, gate_id, outcome, m.measured, m.threshold))
    grade = grade_of(results, priced=c.priced, alert_min=gates_cfg.min_reward_risk.alert_min)
    return GateEvaluation(c.key, c.t, tuple(results), tuple(rejections), grade)
