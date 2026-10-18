"""The command catalog: every CLI the engine ships, read from its argparse parsers.

The catalog is built by introspecting the real parsers (``fse.cli.build_parser``
and the research CLIs), so a new command or flag shows up in the console
without UI code. :func:`build_argv` turns form values into an argv list and
checks it with the same parser before anything runs.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

type Placement = Literal["runner", "local", "batch"]
type Target = Literal["local", "aws"]

LIVE_DISABLED_REASON = (
    "fse live sends Practice and Combine orders to ProjectX. It is disabled in the "
    "console until the TopstepX VPS/VPN terms are verified (FSE_UI_ENABLE_LIVE=false)."
)
RUNNER_REASON = "Runs as the 24 h runner. Start it from Live runner, not as a job."

_RUNNER_COMMANDS = frozenset({"run", "paper", "live"})
_ORDER_FLAGS = ("--plugin-orders", "--live-orders")
"""Argv tokens no console job may carry (equal to or starting with either)."""
_LOCAL_COMMANDS = frozenset(
    {("halt",), ("clear",), ("scan-secrets",), ("skilldocs", "check"), ("drafts", "promote")}
)
# Batch commands that are not heavy: ``fse accounts`` is a read-only lookup (one login,
# one Account/search) and must not wait for the single local heavy slot.
_LIGHT_BATCH: frozenset[tuple[str, ...]] = frozenset({("accounts",)})

# ---- AWS placement overrides (design.md 10.9), applied only with target="aws".
# A (program, path) listed here runs in the UI container (``/data`` mounted) instead of
# on Fargate, because its inputs live on the host EBS volume, not in S3. Empty here: add
# the research CLIs of your own that read host files. ``fse backtest-plugin`` is batch: the
# console stages the runner config and any missing bars or closed recordings, and the task
# reads the rest from S3.
_AWS_HOST_LOCAL: frozenset[tuple[str, tuple[str, ...]]] = frozenset()
DRAFTS_AWS_REASON = "Edits project files in the image. Run it from a checkout."
# They edit project files, which are read-only in the image (and lost on Fargate).
_AWS_DISABLED: dict[tuple[str, tuple[str, ...]], str] = {
    ("fse", ("drafts", "build")): DRAFTS_AWS_REASON,
    ("fse", ("drafts", "promote")): DRAFTS_AWS_REASON,
}
# These point outside the synced ``state/holdout_log.jsonl`` or outside the granted
# ``fse/*`` prefix and ``skylit_fse`` Glue database, so on Fargate they would fail or
# bypass the sync. Hidden from the form; ``build_args`` rejects them (unknown argument).
_AWS_HIDDEN_FLAGS: dict[str, frozenset[str]] = {
    "experiment holdout": frozenset({"--holdout-log"}),
    "export-aws": frozenset({"--bucket", "--prefix", "--glue-database"}),
}


@dataclass(frozen=True, slots=True)
class Program:
    id: str
    label: str
    module: str | None
    """``python -m <module>``; ``None`` for the ``fse`` console script."""
    load: Callable[[], argparse.ArgumentParser]


def _fse() -> argparse.ArgumentParser:
    from fse.cli import build_parser

    return build_parser()


def _research(module: str) -> Callable[[], argparse.ArgumentParser]:
    def load() -> argparse.ArgumentParser:
        # ``_parser`` builds the parser only; ``main`` would install netguard.
        parser: argparse.ArgumentParser = importlib.import_module(module)._parser()
        return parser

    return load


# Add ``Program("research.<name>.run", ..., _research("research.<name>.run"))`` here for
# each research CLI of your own (``python -m research.<name>.run``).
PROGRAMS: tuple[Program, ...] = (Program("fse", "fse", None, _fse),)


@dataclass(slots=True)
class Command:
    id: str
    program: str
    path: tuple[str, ...]
    help: str
    description: str
    placement: Placement
    heavy: bool
    disabled_reason: str | None
    args: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    parser: argparse.ArgumentParser = field(repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "program": self.program,
            "path": list(self.path),
            "help": self.help,
            "description": self.description,
            "placement": self.placement,
            "heavy": self.heavy,
            "disabled_reason": self.disabled_reason,
            "args": self.args,
            "groups": self.groups,
        }


class ArgvError(ValueError):
    """The values do not form a valid command line (the parser's own message)."""


def _jsonable(value: object) -> object:
    if value is None or value is argparse.SUPPRESS:
        return None
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return str(value)


def _type_name(action: argparse.Action) -> str:
    t = action.type
    if t is int:
        return "int"
    if t is float:
        return "float"
    if t is Path or getattr(t, "__name__", "") in ("Path", "_path", "path"):
        return "path"
    name = getattr(t, "__qualname__", "") or getattr(t, "__name__", "") if t is not None else ""
    return "date" if "date" in name.lower() else "string"


def _kind(action: argparse.Action) -> str:
    if isinstance(action, argparse._StoreTrueAction | argparse._StoreFalseAction):
        return "flag"
    if isinstance(action, argparse._StoreConstAction | argparse._CountAction):
        return "flag"
    if isinstance(action, argparse._AppendAction):
        return "append"
    if isinstance(action, argparse.BooleanOptionalAction):
        return "flag"
    return "value"


def _arg_spec(parser: argparse.ArgumentParser, action: argparse.Action) -> dict[str, Any]:
    formatter = parser._get_formatter()
    try:
        text = formatter._expand_help(action) if action.help else ""
    except KeyError, TypeError, ValueError:
        text = action.help or ""
    long_flags = [s for s in action.option_strings if s.startswith("--")]
    flag: str | None = long_flags[0] if long_flags else next(iter(action.option_strings), None)
    nargs = action.nargs
    return {
        "dest": action.dest,
        "flag": flag,
        "positional": not action.option_strings,
        "kind": _kind(action),
        "type": _type_name(action),
        "nargs": nargs if isinstance(nargs, int | str) else None,
        "choices": None if action.choices is None else [str(c) for c in action.choices],
        "default": _jsonable(action.default),
        "required": bool(action.required)
        or (not action.option_strings and nargs not in ("?", "*")),
        "help": text,
        "metavar": action.metavar if isinstance(action.metavar, str) else None,
    }


# Which config-store kind a path argument takes (the console's config picker).
# fse commands read Playbook Strategy_Configs and sweep files; fse backtest-plugin a
# runner config; a research CLI (``research.<name>.run``) its own research YAMLs, stored
# as ``<name>-<file>``. No plugin is named: the rule follows the program id.
_STRATEGY_DESTS: Final = frozenset({"config", "candidate", "chosen", "baseline"})
_RUNNER_CONFIG_COMMANDS: Final = frozenset({("backtest-plugin",)})


def config_kind(program: str, path: tuple[str, ...], dest: str) -> dict[str, str] | None:
    """``{"kind", "prefix"?}`` of the configs a path argument accepts; ``None``: not a config."""
    if program == "fse":
        if path in _RUNNER_CONFIG_COMMANDS:
            return {"kind": "runner"} if dest == "config" else None
        if dest == "sweep":
            return {"kind": "sweep"}
        return {"kind": "strategy"} if dest in _STRATEGY_DESTS else None
    parts = program.split(".")
    if len(parts) == 3 and parts[0] == "research" and parts[2] == "run" and dest == "config":
        return {"kind": "research", "prefix": f"{parts[1]}-"}
    return None


def _placement(program: str, path: tuple[str, ...]) -> Placement:
    if program != "fse":
        return "batch"
    if path[0] in _RUNNER_COMMANDS:
        return "runner"
    return "local" if path in _LOCAL_COMMANDS else "batch"


def _walk(
    program: Program,
    parser: argparse.ArgumentParser,
    path: tuple[str, ...],
    help_text: str,
    target: Target = "local",
) -> list[Command]:
    sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if sub is not None:
        helps = {a.dest: a.help or "" for a in sub._choices_actions}
        out: list[Command] = []
        for name, child in sub.choices.items():
            out.extend(_walk(program, child, (*path, name), helps.get(name, ""), target))
        return out
    hidden = _AWS_HIDDEN_FLAGS.get(" ".join(path), frozenset()) if target == "aws" else frozenset()
    if program.id != "fse":
        hidden = frozenset()
    args = [
        {**spec, "config_kind": config_kind(program.id, path, spec["dest"])
         if spec["type"] == "path" else None}
        for spec in (
            _arg_spec(parser, a)
            for a in parser._actions
            if not isinstance(a, argparse._HelpAction | argparse._VersionAction)
            and a.dest is not argparse.SUPPRESS
        )
        if spec["flag"] not in hidden
    ]
    groups = [
        {"required": g.required, "dests": [a.dest for a in g._group_actions]}
        for g in parser._mutually_exclusive_groups
    ]
    default_placement = _placement(program.id, path)
    placement = default_placement
    disabled: str | None = None
    if program.id == "fse" and path and path[0] in _RUNNER_COMMANDS:
        disabled = LIVE_DISABLED_REASON if path[0] == "live" else RUNNER_REASON
    if target == "aws":
        if (program.id, path) in _AWS_HOST_LOCAL:
            placement = "local"
        disabled = _AWS_DISABLED.get((program.id, path), disabled)
    label = " ".join((program.label, *path)) if path else program.label
    return (
        [
            Command(
                id=" ".join((program.id, *path)),
                program=program.id,
                path=path,
                help=help_text or (parser.description or "").split("\n")[0],
                description=parser.description or "",
                placement=placement,
                heavy=default_placement == "batch"
                and not (program.id == "fse" and path in _LIGHT_BATCH),
                disabled_reason=disabled,
                args=args,
                groups=groups,
                parser=parser,
            )
        ]
        if label
        else []
    )


class Catalog:
    """Every command of every program, built once (thread-safe, lazy).

    ``target="aws"`` applies the AWS placement table (``_AWS_*`` above); with
    ``"local"`` the local executor runs everything on this machine.
    """

    def __init__(
        self, programs: Sequence[Program] | None = None, *, target: Target = "local"
    ) -> None:
        self._programs = {p.id: p for p in (PROGRAMS if programs is None else programs)}
        self.target: Target = target
        self._commands: dict[str, Command] | None = None
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def _build(self) -> dict[str, Command]:
        with self._lock:
            if self._commands is None:
                found: dict[str, Command] = {}
                for program in self._programs.values():
                    try:
                        parser = program.load()
                    except Exception as exc:  # a broken research CLI must not hide the others
                        self._errors[program.id] = f"{type(exc).__name__}: {exc}"
                        continue
                    for cmd in _walk(program, parser, (), "", self.target):
                        found[cmd.id] = cmd
                self._commands = found
            return self._commands

    def commands(self) -> list[Command]:
        return list(self._build().values())

    def errors(self) -> dict[str, str]:
        self._build()
        return dict(self._errors)

    def get(self, command_id: str) -> Command:
        try:
            return self._build()[command_id]
        except KeyError:
            raise KeyError(f"unknown command {command_id!r}") from None

    def program(self, program_id: str) -> Program:
        return self._programs[program_id]


def _scalar(value: object, dest: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ArgvError(f"{dest}: expected a value")
    if isinstance(value, int | float | str):
        text = str(value)
    else:
        raise ArgvError(f"{dest}: expected text or a number")
    if "\x00" in text or "\n" in text:
        raise ArgvError(f"{dest}: the value must be one line of text")
    return text


def build_args(command: Command, values: Mapping[str, object]) -> list[str]:
    """The argument list for ``values`` (dest to value), checked by the real parser."""
    known = {a["dest"]: a for a in command.args}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ArgvError(f"unknown argument(s) for {command.id}: {', '.join(unknown)}")
    options: list[str] = []
    positionals: list[str] = []
    for spec in command.args:
        dest = spec["dest"]
        value = values.get(dest)
        if value is None or value == "" or value == []:
            continue
        if spec["positional"]:
            items = value if isinstance(value, list) else [value]
            for item in items:
                text = _scalar(item, dest)
                if text.startswith("-"):
                    raise ArgvError(f"{dest}: a value may not start with '-'")
                positionals.append(text)
            continue
        flag = spec["flag"]
        if spec["kind"] == "flag":
            if value is True:
                options.append(flag)
            elif value is not False:
                raise ArgvError(f"{dest}: expected true or false")
        elif spec["kind"] == "append":
            for item in value if isinstance(value, list) else [value]:
                options.append(f"{flag}={_scalar(item, dest)}")
        elif spec["nargs"] in ("+", "*") or isinstance(spec["nargs"], int):
            items = value if isinstance(value, list) else [value]
            options.append(flag)
            for item in items:
                text = _scalar(item, dest)
                if text.startswith("-"):
                    raise ArgvError(f"{dest}: a value may not start with '-'")
                options.append(text)
        else:
            options.append(f"{flag}={_scalar(value, dest)}")
    args = [*options, *positionals]
    refuse_order_flags(args)
    check_args(command, args)
    return args


def refuse_order_flags(args: Sequence[str]) -> None:
    """:class:`ArgvError` for any ``--plugin-orders`` / ``--live-orders`` token: broker
    orders start only with the runner (its host gate), never as a console job."""
    for token in args:
        if token.startswith(_ORDER_FLAGS):
            flag = next(f for f in _ORDER_FLAGS if token.startswith(f))
            raise ArgvError(f"{flag} is not available in the console")


def check_args(command: Command, args: Sequence[str]) -> None:
    """Parse ``args`` with the command's own parser; :class:`ArgvError` on a usage error."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            command.parser.parse_args(list(args))
    except SystemExit as exc:
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        message = lines[-1] if lines else f"invalid arguments (exit {exc.code})"
        raise ArgvError(message.split("error: ", 1)[-1]) from None


def full_argv(
    command: Command, args: Sequence[str], *, fse_bin: str, python_bin: str, module: str | None
) -> list[str]:
    head = [fse_bin] if module is None else [python_bin, "-m", module]
    return [*head, *command.path, *args]
