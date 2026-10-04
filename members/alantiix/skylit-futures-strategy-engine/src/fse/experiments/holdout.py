"""The Holdout_Period: the newest sessions, reserved for out-of-sample evaluation.

Design §22 and Req 22.1. The Holdout_Period is the newest
``ceil(fraction * n)`` of the ``n`` sessions with data in the Data_Cache, with
the holdout fraction from 0.05 to 0.50 (default 0.20). The caller decides which
sessions have data and passes them in, so this module reads no file and no
clock. Sweeps, ablations, cadence comparisons and walk-forward tests drop every
session in the period (Req 22.2), and every relevant Run_Manifest records its
first and last dates as ``holdout: {first, last}`` (Req 22.3, :meth:`to_json`).

The rounding is exact. A float fraction is read through its shortest decimal
form, so ``0.07`` of 100 sessions is 7 sessions, not the 8 that
``math.ceil(0.07 * 100)`` gives after binary rounding.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from typing import Final

from fse.logio.canonical_json import JsonValue
from fse.timekit import SessionCalendar

__all__ = [
    "HOLDOUT_FRACTION_DEFAULT",
    "HOLDOUT_FRACTION_MAX",
    "HOLDOUT_FRACTION_MIN",
    "HoldoutError",
    "HoldoutPeriod",
    "compute_holdout_period",
    "holdout_session_count",
]

HOLDOUT_FRACTION_DEFAULT: Final = 0.20
HOLDOUT_FRACTION_MIN: Final = 0.05
HOLDOUT_FRACTION_MAX: Final = 0.50

_MIN_EXACT: Final = Fraction(1, 20)
_MAX_EXACT: Final = Fraction(1, 2)


class HoldoutError(ValueError):
    """The holdout fraction or the list of sessions with data is invalid."""


def _exact_fraction(fraction: float | Decimal | Fraction) -> Fraction:
    """``fraction`` as an exact rational, checked against 0.05 to 0.50 inclusive."""
    if isinstance(fraction, bool) or not isinstance(fraction, int | float | Decimal | Fraction):
        raise HoldoutError(f"holdout fraction must be a number, got {fraction!r}")
    if (isinstance(fraction, float) and not math.isfinite(fraction)) or (
        isinstance(fraction, Decimal) and not fraction.is_finite()
    ):
        raise HoldoutError(f"holdout fraction must be finite, got {fraction!r}")
    # repr() of a float is its shortest round-tripping decimal, e.g. "0.07".
    exact = Fraction(repr(fraction)) if isinstance(fraction, float) else Fraction(fraction)
    if not _MIN_EXACT <= exact <= _MAX_EXACT:
        raise HoldoutError(
            f"holdout fraction {fraction} is outside the allowed range "
            f"{HOLDOUT_FRACTION_MIN} to {HOLDOUT_FRACTION_MAX}"
        )
    return exact


def holdout_session_count(n_sessions_with_data: int, fraction: float | Decimal | Fraction) -> int:
    """``ceil(fraction * n)``: how many of ``n`` sessions the Holdout_Period holds."""
    if isinstance(n_sessions_with_data, bool) or not isinstance(n_sessions_with_data, int):
        raise HoldoutError(f"session count must be an integer, got {n_sessions_with_data!r}")
    if n_sessions_with_data < 0:
        raise HoldoutError(f"session count must not be negative, got {n_sessions_with_data}")
    return math.ceil(_exact_fraction(fraction) * n_sessions_with_data)


@dataclass(frozen=True, slots=True)
class HoldoutPeriod:
    """The Holdout_Period computed from ``n_sessions_with_data`` sessions.

    ``sessions`` holds the newest sessions with data, oldest first, and is never
    empty. ``fraction`` is the configured holdout fraction as given.
    """

    sessions: tuple[date, ...]
    fraction: float | Decimal | Fraction
    n_sessions_with_data: int

    def __post_init__(self) -> None:
        if not self.sessions:
            raise HoldoutError("a Holdout_Period holds at least one session")
        if any(a >= b for a, b in pairwise(self.sessions)):
            raise HoldoutError("Holdout_Period sessions must be distinct and oldest first")
        expected = holdout_session_count(self.n_sessions_with_data, self.fraction)
        if len(self.sessions) != expected:
            raise HoldoutError(
                f"a Holdout_Period of {self.n_sessions_with_data} sessions at fraction "
                f"{self.fraction} holds {expected} sessions, not {len(self.sessions)}"
            )

    @property
    def first(self) -> date:
        """The oldest session in the Holdout_Period."""
        return self.sessions[0]

    @property
    def last(self) -> date:
        """The newest session in the Holdout_Period (the newest session with data)."""
        return self.sessions[-1]

    def __len__(self) -> int:
        return len(self.sessions)

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and not isinstance(d, datetime) and d in self.sessions

    def to_json(self) -> dict[str, JsonValue]:
        """The Run_Manifest ``holdout`` field: first and last session as ``YYYY-MM-DD``."""
        return {"first": self.first.isoformat(), "last": self.last.isoformat()}


def compute_holdout_period(
    sessions_with_data: Iterable[date],
    fraction: float | Decimal | Fraction = HOLDOUT_FRACTION_DEFAULT,
    *,
    calendar: SessionCalendar | None = None,
) -> HoldoutPeriod:
    """The newest ``ceil(fraction * n)`` of the ``n`` sessions with data (Req 22.1).

    ``sessions_with_data`` may come in any order; repeated dates count once.
    With ``calendar`` every date must be a session of that calendar; a date
    outside its coverage raises ``OutsideCoverageError``.

    Raises ``HoldoutError`` for a fraction outside 0.05 to 0.50, a value that is
    not a date, a non-session date, or no sessions with data at all.
    """
    unique: set[date] = set()
    for d in sessions_with_data:
        if not isinstance(d, date) or isinstance(d, datetime):
            raise HoldoutError(f"sessions with data must be dates, got {d!r}")
        unique.add(d)
    ordered = sorted(unique)
    if calendar is not None:
        for d in ordered:
            if not calendar.is_session(d):
                raise HoldoutError(f"{d} is not a session (weekend or exchange holiday)")
    count = holdout_session_count(len(ordered), fraction)
    if count == 0:
        raise HoldoutError("no sessions with data, so there is no Holdout_Period")
    return HoldoutPeriod(
        sessions=tuple(ordered[-count:]),
        fraction=fraction,
        n_sessions_with_data=len(ordered),
    )
