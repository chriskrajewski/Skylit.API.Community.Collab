"""Unit tests for the Strategy_Config hash (design §17).

The hash is the SHA-256 of the canonical JSON of what the Config_Printer
writes: equal configs hash equal whatever their file layout, and any value
change changes the hash.
"""

from __future__ import annotations

import hashlib
import re

from fse.config.hashing import CONFIG_HASH_ALGORITHM, config_hash
from fse.config.loader import LoadOk, load_text
from fse.config.printer import dump
from fse.config.schema import StrategyConfig
from fse.logio.canonical_json import dumps_bytes
from tests.fakes.configs import MINIMAL_CONFIG_YAML, minimal_config_data


def loaded(text: str) -> StrategyConfig:
    result = load_text(text)
    assert isinstance(result, LoadOk), result
    return result.config


def test_hash_is_sha256_of_the_canonical_json_of_the_printed_mapping() -> None:
    cfg = StrategyConfig.model_validate(minimal_config_data())
    digest = config_hash(cfg)
    assert CONFIG_HASH_ALGORITHM == "sha256"
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == hashlib.sha256(dumps_bytes(cfg.model_dump(mode="python"))).hexdigest()
    assert config_hash(cfg) == digest


def test_equal_configs_hash_equal_whatever_the_file_layout() -> None:
    minimal = loaded(MINIMAL_CONFIG_YAML)
    printed = loaded(dump(minimal))
    reordered = loaded(
        "# comments and key order do not matter\n"
        "fills:\n  costs:\n"
        "    MNQ: {exchange_fee: '0.35', commission: '0.37'}\n"
        "    MES: {exchange_fee: '0.35', commission: '0.37'}\n"
        "order_mode: paper\n"
        "regime: {min_abs_value: 1500}\n"
    )
    assert config_hash(minimal) == config_hash(printed) == config_hash(reordered)


def test_any_value_change_changes_the_hash() -> None:
    base = loaded(MINIMAL_CONFIG_YAML)
    changed = [
        loaded(MINIMAL_CONFIG_YAML + "order_mode: practice\n"),
        loaded(MINIMAL_CONFIG_YAML + "gates: {midpoint: {enabled: false}}\n"),
        loaded(
            MINIMAL_CONFIG_YAML + "exits: {modes: {fixed_r: {r_multiple: 3.0000000000000004}}}\n"
        ),
        loaded(MINIMAL_CONFIG_YAML.replace('"0.37"', '"0.38"', 1)),
    ]
    hashes = {config_hash(cfg) for cfg in changed}
    assert config_hash(base) not in hashes
    assert len(hashes) == len(changed)
