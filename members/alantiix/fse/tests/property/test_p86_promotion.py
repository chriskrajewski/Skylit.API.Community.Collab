"""Property 86: Promotion requires approval.

*For any* draft content and approvals file, ``fse drafts promote`` changes
``SKILL.md`` if and only if the sha256 of the current draft equals a
recorded approval, and otherwise leaves ``SKILL.md`` byte-identical.

**Inputs.** In a fresh skill root: a ``SKILL.md`` of drawn bytes (or none),
a ``revised_SKILL_<date>.md`` draft of drawn bytes, and an ``approvals.yaml``
that is absent, empty, or lists 0 to 4 approvals. Each approval holds the
sha256 of the draft's bytes, of the draft with one drawn edit (an approval
of an earlier version), or of other drawn bytes.

**Checks.** :func:`promote` (the function behind ``fse drafts promote``)
either returns, and ``SKILL.md`` then holds exactly the draft's bytes, or
raises :class:`PromotionRefused` and ``SKILL.md`` (or its absence) is
unchanged. It returns exactly when a recorded sha256 equals the sha256 of
the draft's current bytes. The draft and the approvals file are never
changed, and no other file appears.

**Validates: Requirements 26.18**
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fse.skilldocs import APPROVALS_FILE, FINE_TUNE_DIR, SKILL_FILE
from fse.skilldocs.approvals import PromotionRefused, promote

DRAFT = "revised_SKILL_2026-10-05.md"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _edit(data: bytes, at: int, byte: int) -> bytes:
    if not data:
        return bytes([byte])
    i = at % len(data)
    new = (data[i] + 1 + byte % 255) % 256
    return data[:i] + bytes([new]) + data[i + 1 :]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


approval_kinds = st.lists(
    st.one_of(
        st.just(("current", b"")),
        st.tuples(st.just("edited"), st.binary(min_size=2, max_size=2)),
        st.tuples(st.just("other"), st.binary(max_size=40)),
    ),
    max_size=4,
)


@given(
    skill=st.one_of(st.none(), st.binary(max_size=200)),
    draft=st.binary(max_size=200),
    approvals=st.one_of(st.none(), st.just([]), approval_kinds),
)
def test_promotion_requires_approval(
    skill: bytes | None, draft: bytes, approvals: list[tuple[str, bytes]] | None
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / FINE_TUNE_DIR
        folder.mkdir()
        if skill is not None:
            (root / SKILL_FILE).write_bytes(skill)
        (folder / DRAFT).write_bytes(draft)
        recorded: list[str] = []
        if approvals is not None:
            for kind, extra in approvals:
                if kind == "current":
                    recorded.append(_sha(draft))
                elif kind == "edited":
                    recorded.append(_sha(_edit(draft, extra[0], extra[1])))
                else:
                    recorded.append(_sha(extra))
            lines = ["approvals:"] if recorded else ["approvals: []"]
            lines += [f"  - draft: {DRAFT}\n    sha256: {h}" for h in recorded]
            (folder / APPROVALS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
        before = _snapshot(root)

        approved = _sha(draft) in recorded
        if approved:
            done = promote(root, DRAFT)
            assert (root / SKILL_FILE).read_bytes() == draft
            assert done.sha256 == _sha(draft)
            assert done.changed == (skill != draft)
            expected = {**before, SKILL_FILE: draft}
        else:
            with pytest.raises(PromotionRefused):
                promote(root, DRAFT)
            expected = before
        assert _snapshot(root) == expected
