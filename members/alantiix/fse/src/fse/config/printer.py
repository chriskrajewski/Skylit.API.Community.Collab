"""The Config_Printer: a Strategy_Config as YAML the Config_Loader reads back (design §17).

:func:`dump` writes every key, including each key set from a schema default
(Req 17.7), in schema declaration order (``sort_keys=False``):

- floats by ``repr``, PyYAML's float representer, which round-trips exactly
  (it writes ``1e-05`` as ``1.0e-05`` so YAML 1.1 still reads a float);
- money (``Decimal``) and times as quoted strings, through the schema's
  serializers;
- tuples as lists, ``None`` as ``null``.

Loading the output gives a config equal to the input, so every id, enabled
flag and number is exactly equal (Req 17.8).
"""

from __future__ import annotations

import yaml

from fse.config.schema import StrategyConfig

__all__ = ["dump"]


def dump(cfg: StrategyConfig) -> str:
    """``cfg`` as Strategy_Config YAML text, every key included."""
    text: str = yaml.safe_dump(
        cfg.model_dump(mode="python"),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    )
    return text
