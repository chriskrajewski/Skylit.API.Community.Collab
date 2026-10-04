"""Unit tests for the traceability table, ``docs/traceability.md`` (task 20.3).

The table maps each Skill_Document rule to the Strategy_Config key paths and
Playbook_Baseline values that codify it, or marks it "not codified" with a
reason (Req 17.10). It also lists the rule pairs that cannot both be followed
(Req 17.11). These tests parse its Markdown tables and check that:

- every "Hard passes" item of ``SKILL.md`` and of the working draft has exactly
  one row;
- every row cites a Skill_Document and one of its sections, and is codified,
  "not codified" with a reason, or the Operator's ``regime.min_abs_value`` row;
- every ``<key path> = <value>`` in the file names a Strategy_Config key, and
  the value is the Playbook_Baseline's: the one written in
  ``configs/playbook_baseline.yaml``, or the Config_Schema default where the
  file leaves the key out;
- both Requirement 17.11 conflict pairs are listed;
- the ``regime.min_abs_value`` row and its derivation table exist;
- no account identifier from ``TASK.md`` appears in the file. The forbidden
  strings are read from ``TASK.md`` at test time and are never written in this
  repository. Failure messages give counts, never the strings.

The Skill_Documents are read-only here. ``TASK.md`` text is only searched, never
shown: no assertion has it as an operand.

**Validates: Requirements 17.10, 17.11**
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from fse.config.loader import LoadOk, UniqueKeySafeLoader, validate_data
from fse.config.printer import dump
from fse.skilldocs import (
    FINE_TUNE_DIR,
    SKILL_FILE,
    TASK_FILE,
    WORKING_SKILL_FILE,
    default_skill_root,
)

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
TRACEABILITY: Final = PROJECT_DIR / "docs" / "traceability.md"
BASELINE: Final = PROJECT_DIR / "configs" / "playbook_baseline.yaml"
SKILL_ROOT: Final = default_skill_root(PROJECT_DIR)

DOCUMENTS: Final[Mapping[str, Path]] = {
    "SKILL": SKILL_ROOT / SKILL_FILE,
    "CW": SKILL_ROOT / FINE_TUNE_DIR / WORKING_SKILL_FILE,
    "TASK": SKILL_ROOT / FINE_TUNE_DIR / TASK_FILE,
}
"""The source abbreviations the table uses, as its legend defines them."""

SOURCE_SEPARATOR: Final = " \u203a "  # single right angle quote, between document and section
EN_DASH: Final = "\u2013"
OPENING: Final = {"SKILL": "opening", "CW": "opening", "TASK": "opening paragraph"}

RULE_HEADER: Final = ("ID", "Rule", "Source", "Codified as")
PAIR_HEADER: Final = (
    "ID",
    "Rule A",
    "Rule B",
    "Where both cannot hold",
    "Playbook_Baseline outcome",
)
ONE_SIDED_HEADER: Final = ("ID", "Codified side", "Not codified side", "Where they disagree")
FIELD_HEADER: Final = ("Field", "Value")

OPERATOR_ROW: Final = "OV01"
DERIVATION_HEADING: Final = "`regime.min_abs_value` derivation"
DERIVATION_FIELDS: Final = frozenset(
    {
        "Command and arguments",
        "Percentile and printed value",
        "Chosen `regime.min_abs_value`",
        "Reason for the choice",
        "Date set (YYYY-MM-DD)",
    }
)

REQ_17_11_PAIRS: Final = (
    ("exits.per_regime.Positive_Gamma.mode = next_node", "gates.min_reward_risk.min = 3.0"),
    (
        "exits.per_regime.Positive_Gamma.mode = next_node",
        "gates.opposition_inside_target.window_r = 3.0",
    ),
)

# Test-only values for the keys the Operator enters (task 21). They fill a key
# only where the shipped file leaves it out, so the baseline can be loaded.
OPERATOR_FAKES: Final[Mapping[str, Any]] = {
    "regime": {"min_abs_value": 1234.5},
    "fills": {
        "costs": {
            "MES": {"commission": "1.11", "exchange_fee": "2.22"},
            "MNQ": {"commission": "3.33", "exchange_fee": "4.44"},
        }
    },
}

ROW_ID: Final = re.compile(r"[A-Z]{2}\d{2}")
SEPARATOR_LINE: Final = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
CODE_SPAN: Final = re.compile(r"`([^`]+)`")
KEY_VALUE: Final = re.compile(r"([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)*) = (.+)")
NOT_CODIFIED: Final = re.compile(r"not codified[^:]*: (\S.*)")

# Account-like strings: digit runs of six or more, hyphenated tokens holding
# one (and their alphanumeric segments), and e-mail addresses.
DIGIT_RUN: Final = re.compile(r"\d{6,}")
HYPHENATED: Final = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+")
SEGMENT_WITH_DIGIT: Final = re.compile(r"(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,}")
EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# ------------------------------------------------------------------ parsing


@dataclass(frozen=True, slots=True)
class Table:
    """One Markdown table: its header cells and its body rows."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class RuleRow:
    """One row of a rules table."""

    id: str
    rule: str
    sources: tuple[tuple[str, str], ...]
    codified: str


def cells(line: str) -> tuple[str, ...]:
    """The cells of a table line, stripped."""
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def tables(text: str) -> tuple[Table, ...]:
    """Every Markdown table in ``text``, in order."""
    lines = text.splitlines()
    found: list[Table] = []
    i = 0
    while i < len(lines):
        if not (
            lines[i].startswith("|") and i + 1 < len(lines) and SEPARATOR_LINE.match(lines[i + 1])
        ):
            i += 1
            continue
        header = cells(lines[i])
        rows: list[tuple[str, ...]] = []
        i += 2
        while i < len(lines) and lines[i].startswith("|"):
            row = cells(lines[i])
            assert len(row) == len(header), f"line {i + 1}: {len(row)} cells, want {len(header)}"
            rows.append(row)
            i += 1
        found.append(Table(header, tuple(rows)))
    return tuple(found)


def section(text: str, heading: str, level: int = 2) -> str:
    """The body under the heading ``heading`` up to the next heading of the same level."""
    marker = "#" * level + " "
    lines = text.splitlines()
    start = lines.index(marker + heading) + 1
    end = next(
        (n for n in range(start, len(lines)) if lines[n].startswith(marker)),
        len(lines),
    )
    return "\n".join(lines[start:end])


def headings(text: str) -> frozenset[str]:
    """The ``##`` headings of a Skill_Document."""
    return frozenset(line[3:].strip() for line in text.splitlines() if line.startswith("## "))


def normalize(rule: str) -> str:
    """A rule's text with bold marks, en dashes, spacing and the final period evened out."""
    flat = " ".join(rule.replace("**", "").replace(EN_DASH, "-").split())
    return flat.removesuffix(".")


def hard_pass_items(path: Path) -> list[str]:
    """The items of a Skill_Document's "Hard passes" section, one per sentence."""
    body = " ".join(section(path.read_text(encoding="utf-8"), "Hard passes").split())
    return [normalize(item) for item in re.split(r"(?<=\.)\s+", body.replace("**", "")) if item]


def key_values(text: str) -> list[tuple[str, str]]:
    """Each ``<key path> = <value>`` code span in ``text`` as (key path, YAML value text)."""
    pairs: list[tuple[str, str]] = []
    for span in CODE_SPAN.findall(text):
        match = KEY_VALUE.fullmatch(span)
        if match is not None:
            pairs.append((match.group(1), match.group(2)))
    return pairs


@cache
def doc_text() -> str:
    return TRACEABILITY.read_text(encoding="utf-8")


@cache
def doc_tables() -> tuple[Table, ...]:
    return tables(doc_text())


def rows_with_header(header: tuple[str, ...]) -> list[tuple[str, ...]]:
    return [row for table in doc_tables() if table.header == header for row in table.rows]


@cache
def rule_rows() -> tuple[RuleRow, ...]:
    parsed: list[RuleRow] = []
    for row_id, rule, source, codified in rows_with_header(RULE_HEADER):
        refs: list[tuple[str, str]] = []
        for ref in source.split(";"):
            doc, sep, name = ref.strip().partition(SOURCE_SEPARATOR.strip())
            assert sep, f"{row_id}: source {ref.strip()!r} has no document separator"
            refs.append((doc.strip(), name.strip()))
        parsed.append(RuleRow(row_id, rule, tuple(refs), codified))
    return tuple(parsed)


# ------------------------------------------------------------------ the baseline


def fill_missing(base: Mapping[str, Any], fakes: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``base`` with each ``fakes`` key added only where ``base`` lacks it."""
    merged = dict(base)
    for key, fake in fakes.items():
        if key not in merged:
            merged[key] = fake
        elif isinstance(merged[key], Mapping) and isinstance(fake, Mapping):
            merged[key] = fill_missing(merged[key], fake)
    return merged


def missing_paths(base: Mapping[str, Any], fakes: Mapping[str, Any], prefix: str = "") -> set[str]:
    """The dotted key paths that :func:`fill_missing` fills with a fake value."""
    paths: set[str] = set()
    for key, fake in fakes.items():
        path = f"{prefix}{key}"
        below = base.get(key)
        if key not in base:
            paths.add(path)
        elif isinstance(below, Mapping) and isinstance(fake, Mapping):
            paths |= missing_paths(below, fake, f"{path}.")
    return paths


@cache
def parsed_baseline() -> Mapping[str, Any]:
    """The shipped file as the Config_Loader parses it."""
    loader = UniqueKeySafeLoader(BASELINE.read_text(encoding="utf-8"))
    try:
        data = loader.get_single_data()
    finally:
        loader.dispose()
    assert isinstance(data, dict)
    return data


@cache
def baseline_values() -> Mapping[str, Any]:
    """Every Playbook_Baseline key and value, as the Config_Printer writes them (Req 17.7)."""
    result = validate_data(fill_missing(parsed_baseline(), OPERATOR_FAKES), source=str(BASELINE))
    assert isinstance(result, LoadOk), result
    printed = yaml.safe_load(dump(result.config))
    assert isinstance(printed, dict)
    return printed


def dig(data: object, path: str) -> object:
    """The value at dotted ``path``; fails when the Strategy_Config has no such key."""
    for key in path.split("."):
        assert isinstance(data, dict), f"{path}: {key!r} is under a non-mapping"
        assert key in data, f"{path} is not a Strategy_Config key"
        data = data[key]
    return data


def same(a: object, b: object) -> bool:
    """Equal values of equal types, recursively, so ``true`` never matches ``1``."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


# ------------------------------------------------------------------ account guard


def account_identifiers(text: str) -> frozenset[str]:
    """Account-like strings in ``text``: long digit runs, ids holding one, e-mail addresses."""
    found = set(DIGIT_RUN.findall(text)) | set(EMAIL.findall(text))
    for token in HYPHENATED.findall(text):
        if DIGIT_RUN.search(token):
            found.add(token)
            found.update(SEGMENT_WITH_DIGIT.findall(token))
    return frozenset(found)


def task_identifier_count(text: str) -> int:
    """How many account identifiers from ``TASK.md`` occur in ``text``; never the strings."""
    task = DOCUMENTS["TASK"].read_text(encoding="utf-8")
    return sum(1 for identifier in account_identifiers(task) if identifier in text)


def task_has_label(label: str) -> bool:
    """Whether a ``TASK.md`` paragraph starts with ``label``."""
    task = DOCUMENTS["TASK"].read_text(encoding="utf-8")
    return re.search(rf"^{re.escape(label)}[ :]", task, re.MULTILINE) is not None


# ------------------------------------------------------------------ tests


def test_rule_rows_have_unique_well_formed_ids() -> None:
    ids = [row.id for row in rule_rows()]
    assert len(ids) > 100
    assert [i for i in ids if not ROW_ID.fullmatch(i)] == []
    assert sorted(i for i in set(ids) if ids.count(i) > 1) == []


@pytest.mark.parametrize("doc", ["SKILL", "CW"])
def test_every_hard_passes_item_has_exactly_one_row(doc: str) -> None:
    items = hard_pass_items(DOCUMENTS[doc])
    assert len(items) >= 20, "the Hard passes section did not split into items"
    rows = [normalize(r.rule) for r in rule_rows() if (doc, "Hard passes") in r.sources]
    assert sorted(rows) == sorted(items)


def test_every_source_names_a_skill_document_and_one_of_its_sections() -> None:
    known = {doc: headings(path.read_text(encoding="utf-8")) for doc, path in DOCUMENTS.items()}
    bad: list[str] = []
    for row in rule_rows():
        for doc, name in row.sources:
            if doc not in DOCUMENTS:
                bad.append(f"{row.id}: unknown document {doc!r}")
            elif name == OPENING[doc]:
                continue
            elif doc == "TASK":
                if not task_has_label(name):
                    bad.append(f"{row.id}: TASK has no paragraph label {name!r}")
            elif name not in known[doc]:
                bad.append(f"{row.id}: {doc} has no section {name!r}")
    assert bad == []


def test_every_row_is_codified_or_not_codified_with_a_reason() -> None:
    bad: list[str] = []
    for row in rule_rows():
        if row.id == OPERATOR_ROW:
            continue  # test_min_abs_value_row_and_its_derivation_exist
        if row.codified.startswith("not codified"):
            if NOT_CODIFIED.match(row.codified) is None:
                bad.append(f"{row.id}: 'not codified' with no reason")
        elif not key_values(row.codified):
            bad.append(f"{row.id}: neither a key path and value nor 'not codified'")
    assert bad == []


def test_the_known_uncodified_rules_are_marked_with_their_reasons() -> None:
    by_rule = {normalize(row.rule): row for row in rule_rows()}
    average_down = by_rule["Unplanned average-down"].codified
    assert average_down.startswith("not codified: no key")
    wider_stop = by_rule["+GEX: wider stops"].codified
    assert wider_stop.startswith("not codified")
    assert "`exits.per_regime.Positive_Gamma.stop_rule = one_node_beyond`" in wider_stop


STATED: Final = sorted(set(key_values(TRACEABILITY.read_text(encoding="utf-8"))))


@pytest.mark.parametrize(("path", "stated"), STATED, ids=[f"{p} = {v}" for p, v in STATED])
def test_stated_value_is_the_playbook_baseline_value(path: str, stated: str) -> None:
    unset = missing_paths(parsed_baseline(), OPERATOR_FAKES)
    assert not any(p == path or p.startswith(f"{path}.") for p in unset), (
        f"{path} states a value the Operator has not entered in {BASELINE.name}"
    )
    found = dig(baseline_values(), path)
    expected = yaml.safe_load(stated)
    assert same(found, expected), f"{path}: the table says {expected!r}, the baseline {found!r}"


def test_the_table_states_values_for_the_codified_baseline_choices() -> None:
    stated = {path for path, _ in STATED}
    # Design "Playbook_Baseline choices that codify the Skill_Documents".
    for path in (
        "exits.per_regime.Positive_Gamma.mode",
        "exits.global.mode",
        "exits.modes.opposition_or_fixed_r.r_multiple",
        "gates.min_reward_risk.min",
        "gates.min_reward_risk.alert_min",
        "gates.opposition_inside_target.fraction",
        "gates.opposition_inside_target.window_r",
        "exits.breakeven.trigger_r",
        "orders.max_open",
        "orders.flatten_time",
        "gates.dark_pool_confluence.enabled",
        "gates.stdev_fib_zone.enabled",
    ):
        assert path in stated, path


def test_conflict_rows_give_key_paths_and_values_on_both_sides() -> None:
    pairs = rows_with_header(PAIR_HEADER)
    one_sided = rows_with_header(ONE_SIDED_HEADER)
    assert pairs
    assert one_sided
    bad = [row[0] for row in pairs if not (key_values(row[1]) and key_values(row[2]) and row[4])]
    bad += [row[0] for row in one_sided if not (key_values(row[1]) and row[2] and row[3])]
    assert bad == []


@pytest.mark.parametrize(("rule_a", "rule_b"), REQ_17_11_PAIRS)
def test_the_requirement_17_11_conflict_pairs_are_listed(rule_a: str, rule_b: str) -> None:
    listed = [
        row[0]
        for row in rows_with_header(PAIR_HEADER)
        if f"`{rule_a}`" in row[1] and f"`{rule_b}`" in row[2]
    ]
    assert len(listed) == 1


def test_min_abs_value_row_and_its_derivation_exist() -> None:
    rows = [row for row in rule_rows() if row.id == OPERATOR_ROW]
    assert len(rows) == 1
    assert "`regime.min_abs_value" in rows[0].codified
    derivation = section(doc_text(), DERIVATION_HEADING)
    assert "fse calibrate regime-min-abs --start" in derivation
    fields = {
        row[0] for table in tables(derivation) if table.header == FIELD_HEADER for row in table.rows
    }
    assert fields >= DERIVATION_FIELDS


def test_account_identifier_guard_finds_account_like_strings() -> None:
    # Made-up strings in the shape of a Combine account name, an id and an e-mail.
    sample = "Default: 50K combine 90XYZ-Q1-ABC-123456-7654321 (account id 99887766); a@b.example"
    found = account_identifiers(sample)
    expected = {"90XYZ-Q1-ABC-123456-7654321", "90XYZ", "123456", "7654321", "99887766"}
    assert found >= expected
    assert "a@b.example" in found
    assert account_identifiers("CON.F.US.MNQ.Z26 at 09:00, 1000000.0 notional") == {"1000000"}


@pytest.mark.parametrize("path", [TRACEABILITY, Path(__file__)], ids=lambda p: p.name)
def test_no_account_identifier_from_task_md_appears(path: Path) -> None:
    leaks = task_identifier_count(path.read_text(encoding="utf-8"))
    assert leaks == 0, f"{path.name} holds {leaks} account identifier(s) from {TASK_FILE}"


def test_traceability_holds_no_email_address() -> None:
    assert EMAIL.findall(doc_text()) == []
