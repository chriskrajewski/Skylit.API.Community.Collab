"""Property 1: Environment precedence.

*For any* pair of shell and ``.env`` values for a variable (unset, empty,
whitespace-only or text), the resolved value is the shell value when it is
non-blank, else the ``.env`` value when non-blank, else blank. When
``SKYLIT_API_KEY`` resolves blank and a request is due, the Skylit_Client
sends zero requests and exits non-zero with a message that names the variable.

How each example runs:

1. ``SKYLIT_API_KEY``, plus up to three other names (``.env.example`` names
   and test-only ``FSE_P01_*`` names), each get a shell state and a ``.env``
   state: unset, empty, whitespace-only or text. Text may carry leading or
   trailing whitespace, which must survive exactly.
2. The ``.env`` file is written to a fresh temporary Project folder, never the
   real one, in mixed line forms: single-, double- or unquoted values, an
   ``export`` prefix, a bare ``NAME`` line (no value), comments and blank
   lines. A whitespace value written unquoted parses as empty; both are blank.
   When no variable has a line, the file is sometimes left out, since a
   missing file counts as empty.
3. The shell side reaches :func:`load_env` either as an explicit ``environ``
   mapping or through ``os.environ``, patched for that example only.
4. Each resolved value is checked against :meth:`Var.expected`, an oracle
   written here; :func:`resolve_value` and :func:`is_blank` are checked
   against the same oracle.
5. When the key resolves blank, a :class:`SkylitClient` on that view gets 1 to
   3 requests (any documented endpoint) inside a respx router that answers
   every request. Each raises :class:`SkylitKeyMissingError` with exit status
   3 and a message that names the variable and equals the message of an
   environment-free error, so it holds no value. The router sees zero calls,
   the client counts zero attempts and no Fetch_Log file is created.

Whitespace means ``str.isspace``. Every value is fake.

**Validates: Requirements 1.6, 1.7**
"""

from __future__ import annotations

import asyncio
import random
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import respx
from hypothesis import example, given
from hypothesis import strategies as st

from fse.logio import LogWriter, Redactor
from fse.secrets.env import ENV_FILE_NAME, EnvView, is_blank, load_env, resolve_value
from fse.skylit.client import (
    EXIT_CREDENTIALS,
    KEY_VARIABLE,
    ClientConfig,
    SkylitClient,
    SkylitKeyMissingError,
)
from fse.skylit.endpoints import ACCOUNT, ENDPOINTS, HISTORICAL_RANGE, Endpoint
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import NS_PER_SECOND
from tests.fakes.clock import FakeClock

T0 = 1_772_721_000 * NS_PER_SECOND

type Kind = Literal["unset", "empty", "whitespace", "text"]
KINDS: tuple[Kind, ...] = ("unset", "empty", "whitespace", "text")

OTHER_NAMES: tuple[str, ...] = (
    "PROJECTX_USERNAME",
    "PROJECTX_API_KEY",
    "NOTIFIER_WEBHOOK_URL",
    "NARRATOR_API_KEY",
    "PRACTICE_ACCOUNT_ID",
    "COMBINE_ACCOUNT_ID",
    "FSE_P01_EXTRA_A",
    "FSE_P01_EXTRA_B",
)

# Fake values that also make equal shell and .env text likely.
FAKE_TEXTS: tuple[str, ...] = ("fake-skylit-key-0000", "fake-skylit-key-0001", "FAKE-ACCOUNT-1")

# Whitespace per str.isspace. The shell can hold line breaks; .env lines cannot.
SHELL_SPACE = " \t\n\r\x0b\x0c\x1c\x85\u00a0\u2003\u2028\u3000"
DOTENV_SPACE = " \t\u00a0\u2003\u3000"

# No NUL (os.environ refuses it) and no surrogates (not UTF-8).
SHELL_CHARS = st.characters(codec="utf-8", exclude_characters="\x00")
# A single-quoted .env value is literal except for these, so leave them out.
DOTENV_CHARS = st.characters(codec="utf-8", exclude_categories=["Cc"], exclude_characters="'\\")
# Text that python-dotenv reads back unchanged without quotes.
BARE_SAFE = re.compile(r"[A-Za-z0-9_./:@+-]+")


def _blank(value: str | None) -> bool:
    """The oracle: unset, empty or whitespace only."""
    return value is None or all(ch.isspace() for ch in value)


@dataclass(frozen=True)
class Var:
    name: str
    shell: str | None
    """``None``: unset in the shell."""
    dotenv: str | None
    """The value the ``.env`` line writes; ``None``: no line, or a bare ``NAME`` line."""
    line: str | None
    """The ``.env`` line; ``None``: none."""

    @property
    def expected(self) -> str | None:
        if not _blank(self.shell):
            return self.shell
        if not _blank(self.dotenv):
            return self.dotenv
        return None


@dataclass(frozen=True)
class Case:
    variables: tuple[Var, ...]
    env_text: str | None
    """The ``.env`` file content; ``None``: no file."""
    via_os_environ: bool
    requests: tuple[Endpoint, ...]
    """Requests due when the key is blank, in order."""

    def var(self, name: str) -> Var:
        return next(v for v in self.variables if v.name == name)


# ---------------------------------------------------------------- strategies


def _text(chars: st.SearchStrategy[str], space: str) -> st.SearchStrategy[str]:
    pad = st.text(st.sampled_from(space), max_size=2)
    core = st.text(chars, min_size=1, max_size=24).filter(lambda s: not _blank(s))
    padded = st.builds(lambda a, b, c: a + b + c, pad, core, pad)
    return st.one_of(st.sampled_from(FAKE_TEXTS), padded)


def _spaces(space: str) -> st.SearchStrategy[str]:
    return st.text(st.sampled_from(space), min_size=1, max_size=4)


@st.composite
def _shell_value(draw: st.DrawFn, kind: Kind) -> str | None:
    match kind:
        case "unset":
            return None
        case "empty":
            return ""
        case "whitespace":
            return draw(_spaces(SHELL_SPACE))
        case "text":
            return draw(_text(SHELL_CHARS, SHELL_SPACE))


@st.composite
def _dotenv_entry(draw: st.DrawFn, name: str, kind: Kind) -> tuple[str | None, str | None]:
    """The value a ``.env`` line writes and the line itself (``None``: no line)."""
    if kind == "unset":
        return None, draw(st.sampled_from([None, name]))
    value: str
    if kind == "empty":
        value = ""
        forms = [f"{name}=", f"{name}=''", f'{name}=""']
    elif kind == "whitespace":
        value = draw(_spaces(DOTENV_SPACE))
        forms = [f"{name}='{value}'", f'{name}="{value}"', f"{name}={value}"]
    else:
        value = draw(_text(DOTENV_CHARS, DOTENV_SPACE))
        forms = [f"{name}='{value}'"]
        if BARE_SAFE.fullmatch(value):
            forms += [f"{name}={value}", f'{name}="{value}"']
    line = draw(st.sampled_from(forms))
    if draw(st.booleans()):
        line = f"export {line}"
    return value, line


@st.composite
def _vars(draw: st.DrawFn, name: str) -> Var:
    shell = draw(_shell_value(draw(st.sampled_from(KINDS))))
    dotenv, line = draw(_dotenv_entry(name, draw(st.sampled_from(KINDS))))
    return Var(name, shell, dotenv, line)


@st.composite
def _cases(draw: st.DrawFn) -> Case:
    others = draw(st.lists(st.sampled_from(OTHER_NAMES), unique=True, max_size=3))
    variables = tuple(draw(_vars(name)) for name in (KEY_VARIABLE, *others))
    entries = [v.line for v in variables if v.line is not None]
    extras = draw(st.lists(st.sampled_from(["# fake comment", ""]), max_size=2))
    lines = draw(st.permutations([*entries, *extras]))
    no_file = not entries and draw(st.booleans())
    return Case(
        variables=variables,
        env_text=None if no_file else "\n".join(lines) + "\n",
        via_os_environ=draw(st.booleans()),
        requests=tuple(draw(st.lists(st.sampled_from(ENDPOINTS), min_size=1, max_size=3))),
    )


def _key_only(
    shell: str | None, dotenv: str | None, line: str | None, *, via_os_environ: bool
) -> Case:
    text = None if line is None else line + "\n"
    return Case(
        variables=(Var(KEY_VARIABLE, shell, dotenv, line),),
        env_text=text,
        via_os_environ=via_os_environ,
        requests=(ACCOUNT, HISTORICAL_RANGE),
    )


# Blank in both, no .env file, through os.environ: the Req 1.7 path.
_BLANK_BOTH = _key_only(None, None, None, via_os_environ=True)
# A whitespace shell value falls through to the .env value.
_DOTENV_WINS = _key_only(
    " \t", "fake-skylit-key-0001", "SKYLIT_API_KEY='fake-skylit-key-0001'", via_os_environ=False
)
# A non-blank shell value wins, kept exactly, padding included.
_SHELL_WINS = _key_only(
    " fake-skylit-key-0000 ",
    "fake-skylit-key-0001",
    "SKYLIT_API_KEY=fake-skylit-key-0001",
    via_os_environ=True,
)


# ---------------------------------------------------------------- harness


@contextmanager
def _project(case: Case) -> Iterator[Path]:
    """A temporary Project folder holding the case's ``.env`` file, if any."""
    with tempfile.TemporaryDirectory(prefix="fse-p01-") as tmp:
        project = Path(tmp).resolve() / "project"
        project.mkdir()
        if case.env_text is not None:
            (project / ENV_FILE_NAME).write_text(case.env_text, encoding="utf-8")
        yield project


@contextmanager
def _shell(case: Case) -> Iterator[Mapping[str, str] | None]:
    """The ``environ`` argument for ``load_env``: a mapping, or ``None`` with os.environ set."""
    values = {v.name: v.shell for v in case.variables if v.shell is not None}
    if not case.via_os_environ:
        yield values
        return
    with pytest.MonkeyPatch.context() as mp:
        for var in case.variables:
            if var.shell is None:
                mp.delenv(var.name, raising=False)
            else:
                mp.setenv(var.name, var.shell)
        yield None


async def _send_due_requests(
    view: EnvView, requests: tuple[Endpoint, ...], log_path: Path
) -> tuple[list[BaseException | None], int]:
    """Ask a fresh client for each request; return each raised error and the attempts sent."""
    clock = FakeClock(T0)
    raised: list[BaseException | None] = []
    with FetchLog(LogWriter(Redactor()), log_path) as log:
        async with SkylitClient(view, ClientConfig(), log, clock, random.Random(0)) as client:
            for endpoint in requests:
                try:
                    if endpoint is ACCOUNT:
                        await clock.run(client.account())
                    else:
                        await clock.run(client.call(endpoint, {}))
                except Exception as exc:
                    raised.append(exc)
                else:
                    raised.append(None)
            return raised, client.attempts_sent


def _assert_blank_key_sends_nothing(view: EnvView, case: Case, project: Path) -> None:
    log_path = project / "logs" / FETCH_LOG_FILE_NAME
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        every = router.route().respond(200, json={})
        raised, sent = asyncio.run(_send_due_requests(view, case.requests, log_path))

    baseline = str(SkylitKeyMissingError())
    assert len(raised) == len(case.requests)
    for exc in raised:
        assert isinstance(exc, SkylitKeyMissingError)
        assert exc.exit_code == EXIT_CREDENTIALS
        assert exc.exit_code != 0
        assert KEY_VARIABLE in str(exc)
        assert str(exc) == baseline  # depends on no variable value
    assert every.call_count == 0
    assert router.calls.call_count == 0
    assert sent == 0
    assert not log_path.exists()


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 1: Environment precedence
@example(case=_BLANK_BOTH)
@example(case=_DOTENV_WINS)
@example(case=_SHELL_WINS)
@given(case=_cases())
def test_shell_then_dotenv_then_blank_and_blank_key_sends_nothing(case: Case) -> None:
    with _project(case) as project:
        with _shell(case) as environ:
            view = load_env(project, environ=environ)

        for var in case.variables:
            assert is_blank(var.shell) is _blank(var.shell)
            assert is_blank(var.dotenv) is _blank(var.dotenv)
            assert resolve_value(var.shell, var.dotenv) == var.expected, var.name
            assert view.get(var.name) == var.expected, var.name

        if case.var(KEY_VARIABLE).expected is None:
            _assert_blank_key_sends_nothing(view, case, project)
