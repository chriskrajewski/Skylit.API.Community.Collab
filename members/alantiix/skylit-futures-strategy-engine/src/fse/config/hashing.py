"""The Strategy_Config hash (design §17, Req 18.4, 19.15, 22.5, 23.12).

:func:`config_hash` is the SHA-256 of the canonical JSON
(``fse.logio.canonical_json``) of the same mapping the Config_Printer
writes (``cfg.model_dump(mode="python")``): every key, defaults included,
money as strings and floats by ``repr``. So:

- two configs that print the same have the same hash, whatever their files'
  key order, comments, quoting or omitted defaults (a money string keeps its
  digits, so ``"0.370"`` and ``"0.37"`` hash differently);
- any change to an id, enabled flag or value changes the hash;
- the hash does not depend on PyYAML's output formatting.

It is the hash recorded in Run_Manifests, comparison reports, the Holdout_Log
and the Live_Runner start line.
"""

from __future__ import annotations

import hashlib
from typing import Final

from fse.config.schema import StrategyConfig
from fse.logio.canonical_json import dumps_bytes

__all__ = ["CONFIG_HASH_ALGORITHM", "config_hash"]

CONFIG_HASH_ALGORITHM: Final = "sha256"


def config_hash(cfg: StrategyConfig) -> str:
    """The lowercase hex SHA-256 of ``cfg``'s canonical JSON."""
    return hashlib.sha256(dumps_bytes(cfg.model_dump(mode="python"))).hexdigest()
