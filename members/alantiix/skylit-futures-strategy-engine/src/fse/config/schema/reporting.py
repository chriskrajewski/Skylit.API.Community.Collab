"""The ``reporting`` section of the Strategy_Config (design §20, "Strategy_Config shape").

Settings of the Report_Generator and ``fse.analytics`` (Req 19-20):

- ``primary_win_rate``: which Req 20.4 win rate is the Primary_Win_Rate:
  ``a`` (net P&L above $0, the default), ``b`` (scratch counts as a win) or
  ``c`` (reached TP1) (Glossary "Primary_Win_Rate").
- ``scratch_tolerance_r``: win rate (b) counts a trade whose net P&L is at or
  above minus this multiple of its R (default 0.1, 0 to 1, Req 20.4). The
  caller builds ``fse.analytics.metrics.MetricsCfg`` with
  ``Decimal(repr(value))``, so the decimal is the one written in the file.
- ``min_sample_trades``: fewer trades than this label a run low-sample
  (default 30, 1 to 1,000, Req 20.13).
- ``bootstrap_resamples``: the bootstrap resample count
  (:data:`~fse.config.schema.bootstrap.BootstrapResamples`, default 5,000,
  1,000 to 100,000, Req 20.12).
- ``reference_win_rate``: the frontier's reference win rate in percent
  (default 80.0, above 0 and at most 100, Req 20.11).
- ``shadow_min_sample``: the Gate_Funnel's minimum filled only-rejected
  Shadow_Trades for a "no measured edge" flag (default 30, 1 to 1,000,
  Req 19.11-19.12).

Every float rejects NaN and infinity.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from fse.config.schema._base import SchemaModel
from fse.config.schema.bootstrap import BOOTSTRAP_RESAMPLES_DEFAULT, BootstrapResamples

__all__ = [
    "MIN_SAMPLE_MAX",
    "WIN_RATE_DEFINITIONS",
    "ReportingConfig",
    "WinRateDefinition",
]

type WinRateDefinition = Literal["a", "b", "c"]
"""The same values as ``fse.analytics.metrics.PrimaryWinRate``; a test keeps them equal."""

WIN_RATE_DEFINITIONS: Final[tuple[str, ...]] = ("a", "b", "c")
MIN_SAMPLE_MAX: Final = 1000


class ReportingConfig(SchemaModel):
    """Primary_Win_Rate, scratch tolerance, sample minimums, bootstrap and reference rate."""

    primary_win_rate: WinRateDefinition = "a"
    scratch_tolerance_r: float = Field(0.1, ge=0, le=1, allow_inf_nan=False)
    min_sample_trades: int = Field(30, ge=1, le=MIN_SAMPLE_MAX)
    bootstrap_resamples: BootstrapResamples = BOOTSTRAP_RESAMPLES_DEFAULT
    reference_win_rate: float = Field(80.0, gt=0, le=100, allow_inf_nan=False)
    shadow_min_sample: int = Field(30, ge=1, le=MIN_SAMPLE_MAX)
