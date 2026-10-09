# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""bigtrades: one alert per aggressive sweep on the ProjectX market tape."""

from .aggregator import SweepAggregator, Thresholds
from .models import BigTrade, Side, TapePrint

__version__ = "0.1.0"
__all__ = ["BigTrade", "Side", "SweepAggregator", "TapePrint", "Thresholds", "__version__"]
