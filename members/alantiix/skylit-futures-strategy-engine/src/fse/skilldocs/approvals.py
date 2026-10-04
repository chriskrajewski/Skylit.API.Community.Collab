"""Draft approvals and promotion behind ``fse drafts promote`` (design §26, Req 26.18).

**Approvals.** ``<FINE_TUNE_DIR>/approvals.yaml`` holds the Operator's
approvals, one per reviewed Revised_Draft, as the sha256 of the approved
file's bytes::

    approvals:
      - draft: revised_SKILL_2026-10-05.md
        sha256: <64 lowercase hex digits, e.g. from `sha256sum`>

The Operator writes this file by hand after reviewing the draft; no code
path records an approval. Other keys in an entry (a date, a note) are
ignored. A missing file means no approval.

**Promotion.** :func:`promote` copies a ``revised_SKILL_*.md`` draft from the
fine-tune folder into ``SKILL.md`` only when the sha256 of the draft's
current bytes equals a recorded approval. Otherwise it raises
:class:`PromotionRefused` and ``SKILL.md`` stays byte-identical. Any edit
after the approval changes the hash, so an edited draft needs a new
approval. This is the only code path that writes ``SKILL.md``; it writes
the draft's bytes unchanged, through a temporary file and a rename.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final

import yaml

from fse.skilldocs import APPROVALS_FILE, FINE_TUNE_DIR, REVISED_SKILL_GLOB, SKILL_FILE

__all__ = [
    "Approval",
    "ApprovalsError",
    "Promotion",
    "PromotionRefused",
    "draft_sha256",
    "is_approved",
    "load_approvals",
    "promote",
]

_SHA256: Final = re.compile(r"[0-9a-f]{64}")


class ApprovalsError(Exception):
    """The approvals file or the draft cannot be used; ``SKILL.md`` is unchanged."""

    exit_code: int

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class PromotionRefused(Exception):
    """The draft has no recorded approval of its current bytes; ``SKILL.md`` is unchanged."""

    exit_code: ClassVar[int] = 2


@dataclass(frozen=True, slots=True)
class Approval:
    """One recorded approval: the draft's file name and the sha256 of its approved bytes."""

    draft: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Promotion:
    """A completed promotion: the draft, its sha256, and whether ``SKILL.md`` bytes changed."""

    draft: Path
    sha256: str
    skill: Path
    changed: bool


def draft_sha256(data: bytes) -> str:
    """The lowercase hex sha256 of a draft's bytes."""
    return hashlib.sha256(data).hexdigest()


def load_approvals(path: Path) -> tuple[Approval, ...]:
    """The approvals in ``path``, in file order; none when the file does not exist.

    Raises :class:`ApprovalsError`: exit 4 when the file cannot be read, exit 2
    when it is not the module-notes shape.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeDecodeError) as exc:
        raise ApprovalsError(
            f"the approvals file {path} cannot be read: {type(exc).__name__}", exit_code=4
        ) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ApprovalsError(f"the approvals file {path} is not valid YAML") from None
    if data is None:
        return ()
    entries = data.get("approvals") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ApprovalsError(f"the approvals file {path} needs an `approvals` list")
    out: list[Approval] = []
    for i, entry in enumerate(entries, 1):
        draft = entry.get("draft") if isinstance(entry, dict) else None
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(draft, str) or not draft.strip():
            raise ApprovalsError(f"approval {i} in {path} needs a `draft` file name")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ApprovalsError(
                f"approval {i} in {path} needs a `sha256` of 64 lowercase hex digits"
            )
        out.append(Approval(draft.strip(), digest))
    return tuple(out)


def is_approved(digest: str, approvals: tuple[Approval, ...]) -> bool:
    """Whether ``digest`` equals a recorded approval's sha256."""
    return any(a.sha256 == digest for a in approvals)


def promote(skill_root: Path, draft_name: str) -> Promotion:
    """Copy the approved draft ``draft_name`` into ``SKILL.md`` (module notes)."""
    if (
        Path(draft_name).name != draft_name
        or draft_name in {".", ".."}
        or not fnmatch.fnmatchcase(draft_name, REVISED_SKILL_GLOB)
    ):
        raise ApprovalsError(
            f"{draft_name!r} is not a {REVISED_SKILL_GLOB} file name in {FINE_TUNE_DIR}/"
        )
    folder = skill_root / FINE_TUNE_DIR
    draft = folder / draft_name
    try:
        data = draft.read_bytes()
    except FileNotFoundError:
        raise ApprovalsError(f"the draft {draft} does not exist") from None
    except OSError as exc:
        raise ApprovalsError(
            f"the draft {draft} cannot be read: {type(exc).__name__}", exit_code=4
        ) from None
    approvals_path = folder / APPROVALS_FILE
    digest = draft_sha256(data)
    if not is_approved(digest, load_approvals(approvals_path)):
        raise PromotionRefused(
            f"the draft {draft_name} (sha256 {digest}) has no recorded approval in "
            f"{approvals_path}; {SKILL_FILE} is unchanged"
        )
    skill = skill_root / SKILL_FILE
    try:
        changed = skill.read_bytes() != data
    except FileNotFoundError:
        changed = True
    tmp = skill.with_name(f".{SKILL_FILE}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, skill)
    finally:
        tmp.unlink(missing_ok=True)
    return Promotion(draft, digest, skill, changed)
