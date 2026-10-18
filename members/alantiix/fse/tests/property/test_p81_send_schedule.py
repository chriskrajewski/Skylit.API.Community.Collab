"""Property 81: Send schedule.

*For any* timeline of Decision_Times, Grade changes, order events and King
flips, exactly one card is sent at each Decision_Time that has a trigger or
at which the interval has elapsed since the last sent card, no card at any
other Decision_Time, and one 2R alert per Setup_Key at its first Alert_2R
Grade.

The timeline is drawn: Decision_Times 1 s to 30 min apart over one or two
sessions, each with a drawn Grade change, King flip and order event, and the
Grades of a few Setup_Keys. The engine's ``first_alerts`` is replaced by
every key graded Alert_2R at that Decision_Time, so the schedule itself must
keep each key to one alert per session. :class:`CardSchedule` is driven as
the Live_Runner drives it (``due``, then ``sent`` when a card goes out, and
``alerts``), and compared with a reference model: a card at the first
Decision_Time (none was sent yet), at a trigger, or when ``interval_min``
minutes have passed since the last sent card; an alert for a key at the first
Decision_Time of its session where it is graded Alert_2R, when 2R alerts are
on.

**Validates: Requirements 25.5, 25.6, 25.8**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hypothesis import event, given
from hypothesis import strategies as st

from fse.engine.step import CardTriggers, GradeChange, KingFlip
from fse.engine.types import SetupKey
from fse.notify.finding_card import CardSchedule
from fse.timekit import NS_PER_SECOND, Instant

_KEYS = tuple(
    SetupKey("MES", "beach_ball", 5800.0 + 10 * i, "long", date(2026, 3, 2), 1) for i in range(4)
)
_GRADES = ("A_Plus", "Alert_2R", "Pass")


@dataclass(frozen=True, slots=True)
class Step:
    t: Instant
    session: date
    grade_change: bool
    king_flip: bool
    order_event: bool
    grades: tuple[str, ...]


@st.composite
def timelines(draw: st.DrawFn) -> list[Step]:
    n = draw(st.integers(1, 60), label="Decision_Times")
    t = 1_772_461_800 * NS_PER_SECOND
    sessions = (date(2026, 3, 2), date(2026, 3, 3))
    switch = draw(st.integers(0, n), label="second session from")
    out: list[Step] = []
    for i in range(n):
        t += draw(st.integers(1, 1800), label="gap") * NS_PER_SECOND
        out.append(
            Step(
                t=t,
                session=sessions[1 if i >= switch else 0],
                grade_change=draw(st.booleans()),
                king_flip=draw(st.booleans()),
                order_event=draw(st.booleans()),
                grades=tuple(draw(st.sampled_from(_GRADES)) for _ in _KEYS),
            )
        )
    return out


def _triggers(step: Step) -> CardTriggers:
    key = _KEYS[0]
    changes = (GradeChange(key, "Pass", "A_Plus"),) if step.grade_change else ()
    flips = (KingFlip("SPX", "gamma", 5800.0, 5810.0),) if step.king_flip else ()
    graded = tuple(k for k, g in zip(_KEYS, step.grades, strict=True) if g == "Alert_2R")
    return CardTriggers(changes, flips, step.order_event, graded)


# Feature: skylit-futures-strategy-engine, Property 81: Send schedule
@given(
    steps=timelines(),
    interval_min=st.integers(1, 60),
    alerts_on=st.booleans(),
)
def test_one_card_per_due_decision_time_and_one_alert_per_key(
    steps: list[Step], interval_min: int, alerts_on: bool
) -> None:
    schedule = CardSchedule(interval_min, alerts_on)
    interval = interval_min * 60 * NS_PER_SECOND
    last: Instant | None = None
    alerted: dict[date, set[SetupKey]] = {}
    sent_cards = 0
    for step in steps:
        trig = _triggers(step)
        expect = (
            last is None
            or step.grade_change
            or step.king_flip
            or step.order_event
            or step.t - last >= interval
        )
        due = schedule.due(step.t, trig)
        assert due == expect
        if due:
            schedule.sent(step.t)
            last = step.t
            sent_cards += 1
        alerts = schedule.alerts(step.session, trig)
        seen = alerted.setdefault(step.session, set())
        want = [k for k in trig.first_alerts if k not in seen] if alerts_on else []
        seen.update(want)
        assert list(alerts) == want
        assert len(set(alerts)) == len(alerts)
    event(f"cards sent: {'all' if sent_cards == len(steps) else 'some'}")
