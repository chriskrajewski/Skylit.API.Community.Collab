"""The bootstrap resample count of the ``reporting`` section (design §20, Req 20.8, 20.12).

- ``reporting.bootstrap_resamples``: how many resamples of a run's trades
  back each 95% percentile bootstrap interval (``fse.analytics.bootstrap``).
  A whole number from 1,000 to 100,000, default 5,000.

The ``reporting`` section model (task 20.1) declares the key with
:data:`BootstrapResamples`, which carries the range; strictness comes from
:class:`~fse.config.schema._base.SchemaModel`. The bootstrap seed is the
run's seed (``run_backtest(seed=...)``) and is recorded in the Run_Manifest;
it is not a config key.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field

__all__ = [
    "BOOTSTRAP_RESAMPLES_DEFAULT",
    "BOOTSTRAP_RESAMPLES_MAX",
    "BOOTSTRAP_RESAMPLES_MIN",
    "BootstrapResamples",
]

BOOTSTRAP_RESAMPLES_MIN: Final = 1_000
BOOTSTRAP_RESAMPLES_MAX: Final = 100_000
BOOTSTRAP_RESAMPLES_DEFAULT: Final = 5_000

type BootstrapResamples = Annotated[
    int, Field(ge=BOOTSTRAP_RESAMPLES_MIN, le=BOOTSTRAP_RESAMPLES_MAX)
]
"""A bootstrap resample count, 1,000 to 100,000 (Req 20.12)."""
