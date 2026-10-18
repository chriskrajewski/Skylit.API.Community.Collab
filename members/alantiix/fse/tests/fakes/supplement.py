"""A reader for the bar store's parquet layout, as an external consumer would read it.

``<root>/<SYMBOL>/60s/<day>.parquet``: int64 columns ``open_ns, close_ns, o_t, h_t, l_t,
c_t`` and the contract name in the schema metadata ``fse.contract``. Used by the bar store
and backfill tests to check the layout a plugin's own tooling can rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

__all__ = ["RawBar", "read_supplement"]

INTERVAL_S = 60


@dataclass(frozen=True, slots=True)
class RawBar:
    open_ns: int
    close_ns: int
    o: int
    h: int
    lo: int
    c: int
    contract: str


def read_supplement(root: Path, symbol: str, day: date) -> list[RawBar] | None:
    """The bars of one stored day, or ``None`` when the file does not exist."""
    path = root / symbol / f"{INTERVAL_S}s" / f"{day.isoformat()}.parquet"
    if not path.is_file():
        return None
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    contract = meta["fse.contract"]
    cols = {
        n: table.column(n).to_pylist() for n in ("open_ns", "close_ns", "o_t", "h_t", "l_t", "c_t")
    }
    return [
        RawBar(
            int(cols["open_ns"][i]),
            int(cols["close_ns"][i]),
            int(cols["o_t"][i]),
            int(cols["h_t"][i]),
            int(cols["l_t"][i]),
            int(cols["c_t"][i]),
            contract,
        )
        for i in range(len(cols["open_ns"]))
    ]
