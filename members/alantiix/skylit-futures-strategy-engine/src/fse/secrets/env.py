"""Environment loading: shell values, then the Project ``.env`` file (design §1, Req 1.6).

For each variable, a non-blank shell value wins; else the ``.env`` value when it
is non-blank; else the variable is blank. A value is blank when it is unset,
empty or whitespace only. Non-blank values are kept exactly as written.

Only the ``.env`` file in the given Project folder is read. Nothing searches
parent folders, nothing expands ``${...}`` references, and nothing writes to
``os.environ``. No value ever appears in a repr or an error message.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from dotenv import dotenv_values

__all__ = [
    "ENV_FILE_NAME",
    "SECRET_LIST_VARIABLE",
    "SECRET_VARIABLES",
    "EnvFileError",
    "EnvView",
    "is_blank",
    "load_env",
    "parse_secret_list",
    "resolve_value",
]

ENV_FILE_NAME = ".env"

# Built-in Secret_Variables. PRACTICE_ACCOUNT_ID and COMBINE_ACCOUNT_ID are not
# here: account ids count as Secret_Variables only when listed in FSE_SECRET_VARS.
SECRET_VARIABLES: tuple[str, ...] = (
    "SKYLIT_API_KEY",
    "PROJECTX_USERNAME",
    "PROJECTX_API_KEY",
    "NOTIFIER_WEBHOOK_URL",
    "NARRATOR_API_KEY",
)

# Comma-separated names the Operator adds to the Secret_Variable list.
SECRET_LIST_VARIABLE = "FSE_SECRET_VARS"


class EnvFileError(Exception):
    """The Project ``.env`` file exists but cannot be read. Names the path only."""


def is_blank(value: str | None) -> bool:
    """True when ``value`` is unset, empty or whitespace only."""
    return value is None or not value.strip()


def resolve_value(shell: str | None, dotenv: str | None) -> str | None:
    """Apply the precedence rule to one variable. ``None`` means blank."""
    if not is_blank(shell):
        return shell
    if not is_blank(dotenv):
        return dotenv
    return None


def parse_secret_list(raw: str | None) -> tuple[str, ...]:
    """Split an ``FSE_SECRET_VARS`` value into names, in order, without blanks or repeats."""
    if raw is None:
        return ()
    return _unique(name.strip() for name in raw.split(",") if name.strip())


class EnvView:
    """Resolved variables from one ``load_env`` call.

    The view is a snapshot: later changes to the shell environment or to the
    ``.env`` file do not change it.
    """

    __slots__ = ("_values",)

    def __init__(self, shell: Mapping[str, str], dotenv: Mapping[str, str | None]) -> None:
        values: dict[str, str] = {}
        for name in (*shell, *dotenv):
            value = resolve_value(shell.get(name), dotenv.get(name))
            if value is not None:
                values[name] = value
        self._values = values

    def get(self, name: str) -> str | None:
        """The resolved value of ``name``, or ``None`` when it is blank."""
        return self._values.get(name)

    def secret_names(self) -> tuple[str, ...]:
        """Every Secret_Variable name: the built-in list, then each ``FSE_SECRET_VARS`` name."""
        extra = parse_secret_list(self.get(SECRET_LIST_VARIABLE))
        return _unique((*SECRET_VARIABLES, *extra))

    def secret_values(self) -> list[str]:
        """Non-blank Secret_Variable values, in ``secret_names()`` order, without repeats."""
        found = (self.get(name) for name in self.secret_names())
        return list(_unique(value for value in found if value is not None))

    def __repr__(self) -> str:
        # Never show values: a repr can reach a log line or a traceback.
        return f"EnvView({len(self._values)} non-blank variables)"


def load_env(project_dir: Path, *, environ: Mapping[str, str] | None = None) -> EnvView:
    """Resolve variables from the shell environment and ``project_dir/.env``.

    ``environ`` defaults to ``os.environ``. A missing ``.env`` file counts as
    empty. A ``.env`` path that exists but cannot be read as UTF-8 text raises
    ``EnvFileError``.
    """
    shell = dict(os.environ if environ is None else environ)
    return EnvView(shell, _read_env_file(project_dir / ENV_FILE_NAME))


def _read_env_file(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        # `from None` drops the cause: a decode error carries the file bytes.
        raise EnvFileError(f"cannot read {path}") from None
    # Always pass a stream: with neither a path nor a stream, python-dotenv
    # searches parent folders for a .env file.
    return dotenv_values(stream=io.StringIO(text), interpolate=False)


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
