# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Shared fixtures. Tests never touch the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from bigtrades.models import Side, TapePrint

FIXTURES = Path(__file__).parent / "fixtures"
BASE_US = 1_791_465_600_000_000  # 2026-10-08T13:20:00Z in microseconds


@pytest.fixture
def make_print() -> Callable[..., TapePrint]:
    def _make(
        size: int,
        side: Side = Side.SELL,
        price: float = 7830.0,
        ts_ms: int = 0,
        root: str = "ES",
        contract_id: str = "CON.F.US.EP.Z26",
    ) -> TapePrint:
        return TapePrint(
            root=root, contract_id=contract_id, price=price, size=size, side=side, ts_us=BASE_US + ts_ms * 1000
        )

    return _make


@pytest.fixture
def fixture_lines() -> Callable[[str], list[str]]:
    def _load(name: str) -> list[str]:
        return (FIXTURES / name).read_text(encoding="utf-8").splitlines()

    return _load


@pytest.fixture
def contract_rows() -> list[dict]:
    return json.loads((FIXTURES / "contract_search_es.json").read_text(encoding="utf-8"))["contracts"]
