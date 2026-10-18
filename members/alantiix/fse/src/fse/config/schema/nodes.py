"""The ``nodes`` section of the Strategy_Config (design §6, "Strategy_Config shape").

Node_Classifier labels (Req 6.1-6.11), read by ``fse.engine.nodes``:

- ``node_fraction``: a strike is a Node when its absolute value is at least
  this fraction of the King's (default 0.20, range 0.01 to 1, Req 6.1).
- ``gatekeeper_fraction``: the share of the larger same-side Node's absolute
  value a Gatekeeper needs (default 0.30, range 0.01 to 0.99, Req 6.8).
- ``air_pocket_min_width_pct``: the minimum Air_Pocket width, in percent of
  the Snapshot's spot (default 0.5, Req 6.9).
- ``lookout_pct``: the Clear_Skies and Empty_Basement lookout distance, in
  percent of the Snapshot's spot (default 1.0, Req 6.10-6.11).

Lifecycle, Sloppy_Seconds, Dormant and clustering (Req 6.12-6.18), read by
``fse.engine.lifecycle`` and ``fse.engine.clusters``:

- ``decay_fraction``: the drop from the session's highest absolute value that
  makes a Node Decaying (default 0.20, range 0.01 to 1, Req 6.15).
- ``dormant_distance_pct``: the Dormant distance, in percent of the
  Snapshot's spot (default 3.5, Req 6.17).
- ``sloppy_seconds``: window in minutes (default 15, range 1 to 390) and drop
  fraction (default 0.10, range 0.01 to 1, Req 6.16).
- ``clustering``: off by default; ``es_width_pts`` is the ES cluster width in
  points (default 5.0). The NQ width scales it by NQ / ES at ``t`` (Req 6.12).

Percent distances must be above 0 and at most 100. Every float rejects NaN and
infinity.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from fse.config.schema._base import SchemaModel

__all__ = [
    "FRACTION_MIN",
    "GATEKEEPER_FRACTION_MAX",
    "PCT_MAX",
    "SLOPPY_WINDOW_MAX_MIN",
    "ClusteringConfig",
    "NodesConfig",
    "SloppySecondsConfig",
]

FRACTION_MIN: Final = 0.01
"""The smallest fraction any ``*_fraction`` key takes (1%)."""
GATEKEEPER_FRACTION_MAX: Final = 0.99
PCT_MAX: Final = 100.0
"""The largest percent-of-spot distance."""
SLOPPY_WINDOW_MAX_MIN: Final = 390
"""One RTH session, in minutes."""


class SloppySecondsConfig(SchemaModel):
    """The Sloppy_Seconds window after a Tap and the drop that sets the label."""

    window_min: int = Field(15, ge=1, le=SLOPPY_WINDOW_MAX_MIN)
    fraction: float = Field(0.10, ge=FRACTION_MIN, le=1.0)


class ClusteringConfig(SchemaModel):
    """Optional Node clusters over converted levels; labels never change."""

    enabled: bool = False
    es_width_pts: float = Field(5.0, gt=0, allow_inf_nan=False)


class NodesConfig(SchemaModel):
    """Node_Classifier, lifecycle, Sloppy_Seconds, Dormant and clustering parameters."""

    node_fraction: float = Field(0.20, ge=FRACTION_MIN, le=1.0)
    gatekeeper_fraction: float = Field(0.30, ge=FRACTION_MIN, le=GATEKEEPER_FRACTION_MAX)
    air_pocket_min_width_pct: float = Field(0.5, gt=0, le=PCT_MAX)
    lookout_pct: float = Field(1.0, gt=0, le=PCT_MAX)
    decay_fraction: float = Field(0.20, ge=FRACTION_MIN, le=1.0)
    dormant_distance_pct: float = Field(3.5, gt=0, le=PCT_MAX)
    sloppy_seconds: SloppySecondsConfig = SloppySecondsConfig()
    clustering: ClusteringConfig = ClusteringConfig()
