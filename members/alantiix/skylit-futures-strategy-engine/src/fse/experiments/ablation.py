"""Gate ablation: the base Strategy_Config and one variant per enabled Gate (Req 19.13-19.16).

:func:`ablation_configs` builds N + 1 configurations for a base with N
enabled Gates: the base (``base``) first, then one variant per enabled Gate
in the Strategy_Config Gate order (``no_<gate id>``), each identical to the
base except that the Gate is disabled (:func:`fse.experiments.variants.disable`).

:func:`run_ablation` runs them through :func:`fse.experiments.runner.run_experiment`
(same sessions and seed, Holdout_Period excluded) and writes ``ablation.json``
beside ``comparison.json``.

:func:`ablation_rows` gives, per variant, the base value, the variant value
and the change (variant minus base) of each :data:`ABLATION_METRICS` entry
(Req 19.14):

- ``trades_per_day``, ``win_rate`` (the Primary_Win_Rate, in percent),
  ``expectancy_r``, ``profit_factor``, ``max_drawdown_usd`` and
  ``pass_probability`` (the Combine_Pass probability, 0 to 1);
- a value is ``NotApplicable`` when the metric is undefined for a completed
  configuration (for example profit factor with no losing trade) and
  ``None`` (not available) when the configuration failed;
- the change is exact and is ``None`` (not available) whenever either value
  is not a number (Req 19.14, 19.16).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Final

from fse.analytics.metrics import round_fraction
from fse.backtest.manifest import DataRange
from fse.config.schema import StrategyConfig
from fse.engine.types import NotApplicable
from fse.experiments.runner import (
    ConfigOutcome,
    ConfigStatus,
    Evaluator,
    ExperimentConfig,
    ExperimentResult,
    backtest_evaluator,
    run_experiment,
)
from fse.experiments.variants import disable
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant, SessionTimes

__all__ = [
    "ABLATION_FILE_NAME",
    "ABLATION_KIND",
    "ABLATION_METRICS",
    "BASE_NAME",
    "AblationCell",
    "AblationRow",
    "AblationValue",
    "ablation_configs",
    "ablation_rows",
    "ablation_to_jsonable",
    "metric_value",
    "run_ablation",
    "variant_name",
]

ABLATION_KIND: Final = "ablation"
ABLATION_FILE_NAME: Final = "ablation.json"
BASE_NAME: Final = "base"

ABLATION_METRICS: Final[tuple[str, ...]] = (
    "trades_per_day",
    "win_rate",
    "expectancy_r",
    "profit_factor",
    "max_drawdown_usd",
    "pass_probability",
)
"""The Req 19.14 metrics, in report order."""

type AblationValue = Fraction | NotApplicable | None
"""A metric value: a number, undefined (``NotApplicable``) or not available (``None``)."""

_PLACES: Final = 6


@dataclass(frozen=True, slots=True)
class AblationCell:
    """One metric of one variant: base value, variant value and variant minus base."""

    base: AblationValue
    variant: AblationValue
    change: Fraction | None


@dataclass(frozen=True, slots=True)
class AblationRow:
    """One variant: the disabled Gate, its status and one cell per metric."""

    gate_id: str
    name: str
    config_hash: str
    status: ConfigStatus
    error: str | None
    insufficient_sample: bool
    cells: Mapping[str, AblationCell]


def variant_name(gate_id: str) -> str:
    return f"no_{gate_id}"


def ablation_configs(base: StrategyConfig) -> tuple[ExperimentConfig, ...]:
    """The base, then one variant per enabled Gate in Gate order: N + 1 configurations."""
    return (
        ExperimentConfig(BASE_NAME, base),
        *(ExperimentConfig(variant_name(g), disable(base, g)) for g in base.gates.enabled_ids()),
    )


def _number(value: Fraction | Decimal | NotApplicable) -> Fraction | NotApplicable:
    return value if isinstance(value, NotApplicable) else Fraction(value)


def metric_value(outcome: ConfigOutcome, metric: str) -> AblationValue:
    """``metric`` of ``outcome``: ``None`` when it failed, ``NotApplicable`` when undefined."""
    r = outcome.result
    if r is None:
        return None
    m = r.metrics
    values: dict[str, Fraction | Decimal | NotApplicable] = {
        "trades_per_day": m.trades_per_day,
        "win_rate": m.primary_win_rate_pct,
        "expectancy_r": m.expectancy_r,
        "profit_factor": m.profit_factor,
        "max_drawdown_usd": m.max_drawdown_usd,
        "pass_probability": r.pass_estimate.pass_probability,
    }
    if metric not in values:
        raise ValueError(f"{metric!r} is not one of {ABLATION_METRICS}")
    return _number(values[metric])


def _change(base: AblationValue, variant: AblationValue) -> Fraction | None:
    if isinstance(base, Fraction) and isinstance(variant, Fraction):
        return variant - base
    return None


def ablation_rows(result: ExperimentResult) -> tuple[AblationRow, ...]:
    """One row per variant, in definition order (see the module notes)."""
    base, *variants = result.outcomes
    rows: list[AblationRow] = []
    for v in variants:
        cells: dict[str, AblationCell] = {}
        for metric in ABLATION_METRICS:
            b, x = metric_value(base, metric), metric_value(v, metric)
            cells[metric] = AblationCell(b, x, _change(b, x))
        gate_id = v.name.removeprefix("no_")
        rows.append(
            AblationRow(
                gate_id, v.name, v.config_hash, v.status, v.error, v.insufficient_sample, cells
            )
        )
    return tuple(rows)


def _value_json(value: AblationValue) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, NotApplicable):
        return "not applicable"
    return str(round_fraction(value, _PLACES))


def ablation_to_jsonable(result: ExperimentResult) -> dict[str, JsonValue]:
    """``ablation.json``: the base, then per variant its status and metric cells."""
    base = result.outcomes[0]
    return {
        "base": {
            "name": base.name,
            "config_hash": base.config_hash,
            "status": base.status,
            "error": base.error,
            "insufficient_sample": base.insufficient_sample,
        },
        "metrics": list(ABLATION_METRICS),
        "distinct_configurations": result.distinct_configurations,
        "variants": [
            {
                "gate_id": row.gate_id,
                "name": row.name,
                "config_hash": row.config_hash,
                "status": row.status,
                "error": row.error,
                "insufficient_sample": row.insufficient_sample,
                "cells": {
                    m: {
                        "base": _value_json(c.base),
                        "variant": _value_json(c.variant),
                        "change": _value_json(c.change),
                    }
                    for m, c in row.cells.items()
                },
            }
            for row in ablation_rows(result)
        ],
    }


def run_ablation(
    base: StrategyConfig,
    requested: DataRange,
    *,
    cache_dir: Path,
    calendar_dir: Path,
    out_dir: Path,
    writer: LogWriter,
    seed: int | None = None,
    workers: int = 1,
    evaluator: Evaluator = backtest_evaluator,
    config_path: str | None = None,
    base_times: SessionTimes | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
) -> ExperimentResult:
    """Run the N + 1 ablation configurations of ``base`` and write ``ablation.json``."""
    return run_experiment(
        ABLATION_KIND,
        base,
        ablation_configs(base),
        requested,
        cache_dir=cache_dir,
        calendar_dir=calendar_dir,
        out_dir=out_dir,
        writer=writer,
        seed=seed,
        workers=workers,
        evaluator=evaluator,
        outputs=lambda r: {ABLATION_FILE_NAME: ablation_to_jsonable(r)},
        config_path=config_path,
        base_times=base_times,
        clock=clock,
        code_version=code_version,
    )
