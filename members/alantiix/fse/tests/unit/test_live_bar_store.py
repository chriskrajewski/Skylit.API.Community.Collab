"""``LiveBarStore``: the parquet layout a plugin's tooling reads as a bar supplement (4.5)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from fse.live.host.barstore import (
    BAR_STORE_COLUMNS,
    CONTRACT_METADATA_KEY,
    LiveBarStore,
    LiveBarStoreView,
    bar_store_path,
)
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.bar_api import synthetic_bar
from tests.fakes.dataplane import ny
from tests.fakes.supplement import RawBar
from tests.fakes.supplement import read_supplement as _read_supplement

S = NS_PER_SECOND
M = NS_PER_MINUTE
MON = date(2026, 10, 12)


def bars(n: int, start: int, instrument: str = "MNQ", contract: str = "MNQZ6") -> list[Any]:
    return [synthetic_bar(instrument, start + i * M, contract) for i in range(n)]


def test_layout_schema_and_metadata(tmp_path: Path) -> None:
    store = LiveBarStore(tmp_path)
    added = store.add(bars(3, ny(MON, 9, 30)), ny(MON, 9, 33, 2))
    assert added == 3
    path = tmp_path / "MNQ" / "60s" / "2026-10-12.parquet"
    assert path == bar_store_path(tmp_path, "MNQ", MON)
    table = pq.read_table(path)
    assert table.schema.names == list(BAR_STORE_COLUMNS)
    assert all(f.type == pa.int64() for f in table.schema)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    assert meta[CONTRACT_METADATA_KEY] == "MNQZ6"
    assert table.column("received_ns").to_pylist() == [ny(MON, 9, 33, 2)] * 3
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp file left


def test_read_back_with_the_supplement_reader(tmp_path: Path) -> None:
    store = LiveBarStore(tmp_path)
    written = bars(5, ny(MON, 10, 0))
    for b in written:  # one rewrite per new bar, as the data plane does
        store.add([b], b.close_ns + S)
    got = _read_supplement(tmp_path, "MNQ", MON)
    assert got == [
        RawBar(b.open_ns, b.close_ns, b.o_t, b.h_t, b.l_t, b.c_t, "MNQZ6")  # type: ignore[arg-type]
        for b in written
    ]
    assert _read_supplement(tmp_path, "MNQ", date(2026, 10, 13)) is None


def test_the_globex_day_holds_the_sunday_evening_bars(tmp_path: Path) -> None:
    store = LiveBarStore(tmp_path)
    sunday_evening = ny(date(2026, 10, 11), 18, 0)
    store.add(bars(2, sunday_evening), sunday_evening + 3 * M)
    assert bar_store_path(tmp_path, "MNQ", MON).is_file()
    weekend = ny(date(2026, 10, 10), 12, 0)  # Saturday: no Globex day
    logs: list[dict[str, Any]] = []
    LiveBarStore(tmp_path, log=logs.append).add(bars(1, weekend), weekend + M)
    assert logs[0]["reason"] == "no_globex_day"


def test_dedupe_order_and_contract_rule(tmp_path: Path) -> None:
    logs: list[dict[str, Any]] = []
    store = LiveBarStore(tmp_path, log=logs.append)
    b = bars(4, ny(MON, 11, 0))
    store.add([b[2], b[0]], 1)
    store.add([b[0], b[1], b[3]], 2)  # b[0] again: kept once, first receipt wins
    store.add(bars(1, ny(MON, 11, 5), contract="MNQH7"), 3)  # another contract: dropped
    view = store.view()
    assert [x.open_ns for x in view.bars("MNQ", MON)] == [x.open_ns for x in b]
    assert [r.received_ns for r in view.rows("MNQ", MON)] == [1, 2, 1, 2]
    assert logs[-1]["reason"] == "contract_mismatch"


def test_a_new_store_continues_an_existing_file(tmp_path: Path) -> None:
    b = bars(3, ny(MON, 12, 0))
    LiveBarStore(tmp_path).add(b[:2], 1)
    LiveBarStore(tmp_path).add(b[1:], 2)  # restart: reads the file back first
    view = LiveBarStoreView(tmp_path)
    assert [x.open_ns for x in view.bars("MNQ", MON)] == [x.open_ns for x in b]
    assert view.root == tmp_path
    read = view.bars("MNQ", MON)[0]
    assert (read.o_t, read.c_t, read.contract, read.source) == (
        b[0].o_t,
        b[0].c_t,
        "MNQZ6",
        "projectx",
    )


def test_today_file_survives_restarts_and_keeps_its_metadata(tmp_path: Path) -> None:
    # range api-request R4: a day's file is extended on every restart, never truncated
    b = bars(6, ny(MON, 13, 0))
    assert LiveBarStore(tmp_path).add(b[:3], 1) == 3
    assert LiveBarStore(tmp_path).add(b[2:5], 2) == 2  # restart; b[2] overlaps
    assert LiveBarStore(tmp_path).add(b[5:], 3) == 1  # a second restart
    path = bar_store_path(tmp_path, "MNQ", MON)
    assert path == tmp_path / "MNQ" / "60s" / "2026-10-12.parquet"
    files = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()]
    assert files == ["MNQ/60s/2026-10-12.parquet"]
    table = pq.read_table(path)
    assert table.schema.names == list(BAR_STORE_COLUMNS)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    assert meta[CONTRACT_METADATA_KEY] == "MNQZ6"
    assert table.column("open_ns").to_pylist() == [x.open_ns for x in b]  # once, oldest first
    assert table.column("received_ns").to_pylist() == [1, 1, 1, 2, 2, 3]
    read = LiveBarStoreView(tmp_path).bars("MNQ", MON)
    assert [x.open_ns for x in read] == [x.open_ns for x in b]
    assert [x.c_t for x in read] == [x.c_t for x in b]
    assert all(x.v == 0.0 for x in read)  # volume is not stored
    assert all(x.v != 0.0 for x in b)


def test_write_day_and_has_day(tmp_path: Path) -> None:
    from fse.live.host.barstore import StoredBar

    store = LiveBarStore(tmp_path)
    d = date(2026, 10, 9)
    assert not store.has_day("MES", d)
    rows = [StoredBar(x, 7) for x in bars(3, ny(d, 9, 30), "MES", "MESZ6")]
    store.write_day("MES", d, "MESZ6", rows)
    assert store.has_day("MES", d)
    assert LiveBarStore(tmp_path).has_day("MES", d)
    assert len(_read_supplement(tmp_path, "MES", d) or []) == 3
