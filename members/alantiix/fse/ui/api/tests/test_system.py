"""Daily reports: ``.partial`` listed and fetchable, nothing outside the daily dir (C-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fse_ui import system
from tests.conftest import Console


def write(daily: Path, stem: str) -> None:
    daily.mkdir(parents=True, exist_ok=True)
    (daily / f"{stem}.json").write_text(json.dumps({"day": stem, "exit_code": 0}))
    (daily / f"{stem}.md").write_text(f"# {stem}\n")


def test_partial_reports_are_listed_and_fetchable(console: Console) -> None:
    daily = console.app.state.settings.data_dir / "daily"
    write(daily, "2026-10-12")
    write(daily, "2026-10-13.partial")
    reports = console.get("/api/health/daily").json()["reports"]
    assert [(r["day"], r["partial"]) for r in reports] == [
        ("2026-10-13.partial", True),
        ("2026-10-12", False),
    ]
    one = console.get("/api/health/daily/2026-10-13.partial").json()
    assert one["json"] == {"day": "2026-10-13.partial", "exit_code": 0}
    assert one["markdown"] == "# 2026-10-13.partial\n"
    assert console.get("/api/health/daily/2026-10-12").json()["day"] == "2026-10-12"


@pytest.mark.parametrize(
    "day",
    ["..%2Fx", "2026-10-12.partial%2F..%2Fx", "2026-10-12.full", "2026-10-12.partial.partial"],
)
def test_other_day_values_are_404(console: Console, day: str) -> None:
    write(console.app.state.settings.data_dir / "daily", "2026-10-12")
    assert console.get(f"/api/health/daily/{day}").status_code == 404


@pytest.mark.parametrize("day", ["../x", "2026-10-12.partial/../x", "2026-10-12.partial.json"])
def test_daily_report_refuses_paths(tmp_path: Path, day: str) -> None:
    (tmp_path / "x.json").write_text("{}")
    assert system.daily_report(tmp_path / "daily", day) is None
