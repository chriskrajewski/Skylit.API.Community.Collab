"""The ``experiments`` section of the Strategy_Config (design §20-22, "Strategy_Config shape").

Settings of the Experiment_Runner and the Monte_Carlo_Simulator:

- ``holdout_fraction``: the share of sessions with data held out as the
  Holdout_Period (default 0.20, 0.05 to 0.50, Req 22.1).
- ``ranking_objective``: how experiments with two or more configurations
  are ranked: ``combine_pass_probability`` (default), ``expectancy`` (in R) or
  ``profit_factor`` (Req 20.14).
- ``walkforward``: ``train`` sessions (default 60, 10 to 500), ``test``
  sessions (default 20, 5 to 250), the training-window ``objective`` (same
  choices, default ``expectancy``, Req 22.10) and ``min_trades``, the accepted
  trades below which a configuration is "insufficient sample" (default 30,
  1 to 1,000, Req 22.16) (Req 22.9-22.16).
- ``montecarlo``: ``paths`` (default 10,000, 1,000 to 1,000,000),
  ``max_days`` trading days per path (default 60, 1 to 250) and
  ``min_sessions``, the run sessions below which an estimate is
  "insufficient sample" (default 40, 1 to 1,000) (Req 21.1-21.2, 21.11-21.12).

The holdout bounds repeat ``fse.experiments.holdout`` because the engine may
import this package but not the experiments; a test keeps them equal.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from fse.config.schema._base import SchemaModel

__all__ = [
    "HOLDOUT_FRACTION_DEFAULT",
    "HOLDOUT_FRACTION_MAX",
    "HOLDOUT_FRACTION_MIN",
    "RANKING_OBJECTIVES",
    "ExperimentsConfig",
    "MonteCarloConfig",
    "RankingObjective",
    "WalkforwardConfig",
]

type RankingObjective = Literal["combine_pass_probability", "expectancy", "profit_factor"]

RANKING_OBJECTIVES: Final[tuple[str, ...]] = (
    "combine_pass_probability",
    "expectancy",
    "profit_factor",
)

HOLDOUT_FRACTION_DEFAULT: Final = 0.20
HOLDOUT_FRACTION_MIN: Final = 0.05
HOLDOUT_FRACTION_MAX: Final = 0.50


class WalkforwardConfig(SchemaModel):
    """Training and test window lengths, the selection objective and the sample minimum."""

    train: int = Field(60, ge=10, le=500)
    test: int = Field(20, ge=5, le=250)
    objective: RankingObjective = "expectancy"
    min_trades: int = Field(30, ge=1, le=1000)


class MonteCarloConfig(SchemaModel):
    """Path count, trading days per path and the session minimum of the pass estimate."""

    paths: int = Field(10_000, ge=1_000, le=1_000_000)
    max_days: int = Field(60, ge=1, le=250)
    min_sessions: int = Field(40, ge=1, le=1000)


class ExperimentsConfig(SchemaModel):
    """Holdout fraction, ranking objective, walk-forward and Monte Carlo settings."""

    holdout_fraction: float = Field(
        HOLDOUT_FRACTION_DEFAULT,
        ge=HOLDOUT_FRACTION_MIN,
        le=HOLDOUT_FRACTION_MAX,
        allow_inf_nan=False,
    )
    ranking_objective: RankingObjective = "combine_pass_probability"
    walkforward: WalkforwardConfig = WalkforwardConfig()
    montecarlo: MonteCarloConfig = MonteCarloConfig()
