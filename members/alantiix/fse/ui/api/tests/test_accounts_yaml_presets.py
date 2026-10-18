"""The SPA's account form shows each preset's values (``ui/web/src/lib/accountsYaml.ts``
``PRESET_RULES``) and writes only the fields that differ from them. Those values must be
the engine's resolved presets, or the form would write the wrong overrides."""

import json
import re
from pathlib import Path

from fse.live.host.accounts_config import PRESETS
from fse.live.host.config import AccountEntry

TS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "accountsYaml.ts"


def _ts_presets() -> dict[str, object]:
    text = TS.read_text(encoding="utf-8")
    m = re.search(r"PRESET_RULES: Record<PresetName, Rules> = (\{.*?\n\});", text, re.S)
    assert m, "PRESET_RULES not found"
    body = m.group(1)
    body = re.sub(r"'([^']*)'", r'"\1"', body)  # single-quoted strings
    body = re.sub(r"([{,]\s*)([a-z_0-9]+):", r'\1"\2":', body)  # bare keys
    body = re.sub(r",(\s*[}\]])", r"\1", body)  # trailing commas
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


def test_the_spa_presets_are_the_engine_presets() -> None:
    ts = _ts_presets()
    assert sorted(ts) == sorted(PRESETS)
    for name in PRESETS:
        rules = AccountEntry.model_validate({"id": "a", "preset": name}).rules
        assert ts[name] == rules.model_dump(mode="json"), name
