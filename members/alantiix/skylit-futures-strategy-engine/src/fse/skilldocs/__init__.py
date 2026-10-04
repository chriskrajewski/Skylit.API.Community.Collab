"""Skill_Documents and Revised_Drafts (design §26, Req 26).

The Skill_Documents live in the sibling member project
``members/alantiix/skylit-academy-playbook-skill/``:

- ``SKILL.md``
- ``<FINE_TUNE_DIR>/current_working_SKILL_100226.md``
- ``<FINE_TUNE_DIR>/TASK.md``

Revised_Drafts are written to ``<FINE_TUNE_DIR>/`` as
``revised_SKILL_{date}.md`` and ``revised_TASK_{date}.md``.

Every module in this package takes the fine-tune folder from
:data:`FINE_TUNE_DIR` and from nowhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

__all__ = [
    "FINE_TUNE_DIR",
    "REVISED_SKILL_GLOB",
    "REVISED_TASK_GLOB",
    "SKILL_FILE",
    "SKILL_ROOT_NAME",
    "TASK_FILE",
    "WORKING_SKILL_FILE",
    "default_skill_root",
]

# The sibling member project that holds the Skill_Documents.
SKILL_ROOT_NAME: Final = "skylit-academy-playbook-skill"

# The fine-tune folder, relative to the skill root. The spec names it
# `skill-fine-tune/`; the Operator confirmed `skill-fine-tune-wip/` as the
# final name (task 6.2). The folder is not renamed by any code.
FINE_TUNE_DIR: Final = "skill-fine-tune-wip"

SKILL_FILE: Final = "SKILL.md"
WORKING_SKILL_FILE: Final = "current_working_SKILL_100226.md"
TASK_FILE: Final = "TASK.md"
REVISED_SKILL_GLOB: Final = "revised_SKILL_*.md"
REVISED_TASK_GLOB: Final = "revised_TASK_*.md"


def default_skill_root(project_dir: Path) -> Path:
    """The skill root, a sibling of the Project folder: ``<project_dir>/../SKILL_ROOT_NAME``."""
    return project_dir.parent / SKILL_ROOT_NAME
