"""Per-session measurements the Backtester records for the Report_Generator.

- :func:`node_agreement`: King and Gatekeeper agreement between the
  Node_Classifier and Skylit's ``nodeType`` labels (Req 6.22-6.23). Only
  Snapshots that carry a label are compared; the others are counted. A label
  matches case-insensitively: ``King`` and ``Gatekeeper``.
- :func:`tap_counts`: a session's Tap count and the count of its Taps whose
  start (the open of the first bar) and end (the close of the last bar) both
  fall strictly between two consecutive 300 s Decision_Times, 09:30:00 plus
  whole multiples of 300 s (Req 18.11). Taps are counted on the stored
  1-minute bars by the Tap tracker, so the counts do not depend on the run's
  Decision_Cadence.

Both are pure functions of their inputs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Final

from fse.engine.nodes import NodeParams, classify
from fse.engine.taps import BASE_INTERVAL_S, Tap, TapState
from fse.engine.types import Snapshot
from fse.timekit import SessionCalendar

__all__ = [
    "INTER_DECISION_CADENCE_S",
    "NodeAgreement",
    "TapCounts",
    "inter_decision",
    "node_agreement",
    "session_taps",
    "tap_counts",
]

INTER_DECISION_CADENCE_S: Final = 300
"""The Decision_Time spacing of the inter-decision Tap count (Req 18.11)."""

_KING: Final = "king"
_GATEKEEPER: Final = "gatekeeper"


@dataclass(frozen=True, slots=True)
class NodeAgreement:
    """King and Gatekeeper agreement counts over a set of Snapshots (Req 6.22-6.23).

    - ``compared``: Snapshots with at least one ``nodeType`` label;
      ``without_labels``: the others;
    - ``king_agree``: compared Snapshots whose Node_Classifier King is a strike
      Skylit labels King;
    - ``gatekeeper_both`` and ``gatekeeper_either``: strikes both label
      Gatekeeper, and strikes at least one labels Gatekeeper, summed.
    """

    compared: int = 0
    without_labels: int = 0
    king_agree: int = 0
    gatekeeper_both: int = 0
    gatekeeper_either: int = 0

    def __add__(self, other: NodeAgreement) -> NodeAgreement:
        return NodeAgreement(
            self.compared + other.compared,
            self.without_labels + other.without_labels,
            self.king_agree + other.king_agree,
            self.gatekeeper_both + other.gatekeeper_both,
            self.gatekeeper_either + other.gatekeeper_either,
        )


def _labelled(snapshot: Snapshot, label: str) -> set[float]:
    types = snapshot.node_types or ()
    return {
        k for k, t in zip(snapshot.strikes, types, strict=True) if t and t.strip().lower() == label
    }


def node_agreement(snapshots: Iterable[Snapshot], params: NodeParams) -> NodeAgreement:
    """The agreement counts of ``snapshots``; each Snapshot counts once.

    Counted with plain ints; only labelled Snapshots are classified.
    """
    compared = without = king_agree = both = either = 0
    for snapshot in snapshots:
        types = snapshot.node_types
        if types is None or not any(types):
            without += 1
            continue
        labels = classify(snapshot, params)
        kings = _labelled(snapshot, _KING)
        theirs = _labelled(snapshot, _GATEKEEPER)
        ours = set(labels.gatekeepers)
        compared += 1
        king_agree += int(labels.king is not None and labels.king in kings)
        both += len(ours & theirs)
        either += len(ours | theirs)
    return NodeAgreement(compared, without, king_agree, both, either)


@dataclass(frozen=True, slots=True)
class TapCounts:
    """One evaluated session's Tap count and inter-decision Tap count (Req 18.11)."""

    session: date
    bar_interval_s: int
    taps: int
    inter_decision: int


def session_taps(taps: TapState, session: date) -> tuple[Tap, ...]:
    """The Taps of ``session`` in ``taps`` (the tracker after the session's last bar)."""
    if taps.session != session:
        return ()
    return tuple(
        sorted((*taps.ended_taps, *taps.open_taps), key=lambda t: (t.first_open_ns, t.node))
    )


def inter_decision(taps: Iterable[Tap], session: date, calendar: SessionCalendar) -> int:
    """Taps starting and ending strictly between two consecutive 300 s Decision_Times."""
    grid = calendar.decision_times(session, INTER_DECISION_CADENCE_S)
    count = 0
    for tap in taps:
        for lo, hi in pairwise(grid):
            if lo < tap.first_open_ns and tap.end_ns < hi:
                count += 1
                break
    return count


def tap_counts(taps: TapState, session: date, calendar: SessionCalendar) -> TapCounts:
    """The session's :class:`TapCounts`, on its 1-minute bars."""
    found = session_taps(taps, session)
    return TapCounts(session, BASE_INTERVAL_S, len(found), inter_decision(found, session, calendar))
