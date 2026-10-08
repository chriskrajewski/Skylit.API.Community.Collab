# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""ProjectX contract ids and front-month selection.

A ProjectX contract id looks like ``CON.F.US.MNQ.Z26``:
``CON`` . ``F`` (future) . ``US`` . symbol code . month code + year.

Some symbol codes differ from the CME root (``EP`` is ES, ``ENQ`` is NQ).
Extend ``ROOT_ALIASES`` if you trade other products.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MONTH_CODES = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}
MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

# ProjectX symbol code -> common root shown to humans.
ROOT_ALIASES: dict[str, str] = {"EP": "ES", "ENQ": "NQ"}

_EXPIRY_RE = re.compile(r"^([FGHJKMNQUVXZ])(\d{1,2})$")


@dataclass(frozen=True, slots=True)
class ContractRef:
    contract_id: str
    code: str  # ProjectX symbol code, e.g. "ENQ"
    root: str  # human root, e.g. "NQ"
    month: int | None
    year: int | None

    @property
    def label(self) -> str:
        """``MNQ DEC26``, or just the root when the month is unknown."""
        if self.month is None or self.year is None:
            return self.root
        return f"{self.root} {MONTH_NAMES[self.month - 1]}{self.year % 100:02d}"


def parse_contract_id(contract_id: str) -> ContractRef | None:
    parts = (contract_id or "").strip().split(".")
    if len(parts) < 4:
        return None
    code = parts[3].upper()
    month = year = None
    if len(parts) >= 5:
        match = _EXPIRY_RE.match(parts[4].upper())
        if match:
            month = MONTH_CODES[match.group(1)]
            digits = match.group(2)
            year = 2000 + int(digits) if len(digits) == 2 else 2020 + int(digits)
    return ContractRef(contract_id, code, ROOT_ALIASES.get(code, code), month, year)


def display_root(contract_or_symbol_id: str) -> str:
    """Root for a contract id (``CON.F.US.EP.Z26``) or symbol id (``F.US.EP``)."""
    text = (contract_or_symbol_id or "").strip()
    ref = parse_contract_id(text)
    if ref is not None:
        return ref.root
    parts = text.split(".")
    if len(parts) == 3 and parts[0] == "F":
        code = parts[2].upper()
        return ROOT_ALIASES.get(code, code)
    return text.upper()


def contract_label(contract_id: str) -> str:
    ref = parse_contract_id(contract_id)
    return ref.label if ref else (contract_id or "").upper()


def pick_front_month(rows: list[dict[str, Any]], root: str) -> dict[str, Any] | None:
    """Nearest-expiry contract whose root matches exactly (NQ never matches MNQ).

    Prefers rows flagged ``activeContract``. Returns None when nothing matches.
    """
    wanted = root.upper()
    matches: list[tuple[int, int, int, dict[str, Any]]] = []
    for row in rows:
        ref = parse_contract_id(str(row.get("id") or ""))
        if ref is None or ref.root != wanted:
            continue
        inactive = 0 if row.get("activeContract", True) else 1
        matches.append((inactive, ref.year or 9999, ref.month or 99, row))
    if not matches:
        return None
    matches.sort(key=lambda item: item[:3])
    return matches[0][3]
