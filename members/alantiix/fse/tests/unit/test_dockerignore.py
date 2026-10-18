"""``.dockerignore`` ships every plugin module a shipped runner config names.

The file is deny-by-default (``*``) and re-includes folder by folder, so a plugin whose
folder is missing builds fine and fails only at runtime (``fse run`` exits 2 on the
import). Every ``plugins:`` entry of every ``configs/*.yaml`` (enabled or not) is
checked: its module, the transitive closure of its ``research.*`` imports, the parent
package ``__init__.py`` files and any project YAML its ``config`` names must be sent to
the build. ``docker build`` stays the end-to-end check; this is the fast guard.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
import yaml

from tests.fakes.dataplane import PROJECT_DIR

DOCKERIGNORE: Final = PROJECT_DIR / ".dockerignore"
CONFIGS_DIR: Final = PROJECT_DIR / "configs"


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Docker's path glob: ``**/`` any dirs, ``**`` anything, ``*`` and ``?`` within one segment."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _rules() -> list[tuple[bool, re.Pattern[str]]]:
    rules: list[tuple[bool, re.Pattern[str]]] = []
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:].strip() if negated else line
        pattern = pattern.strip("/")
        rules.append((negated, _pattern_regex(pattern)))
    return rules


RULES: Final = _rules()


def docker_included(rel: str) -> bool:
    """True when ``rel`` (a project-relative file path) is sent to the build context.

    Last matching rule wins; a rule matches the path itself or any of its parent dirs.
    """
    parts = rel.split("/")
    candidates = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    included = True
    for negated, regex in RULES:
        if any(regex.fullmatch(c) for c in candidates):
            included = negated
    return included


@pytest.mark.parametrize(
    "rel",
    [
        "src/fse/cli.py",
        "research/__init__.py",
        "configs/runner.paper.yaml",
        "docker/entrypoint.sh",
    ],
)
def test_matcher_includes(rel: str) -> None:
    assert docker_included(rel), rel


@pytest.mark.parametrize(
    "rel",
    [
        ".env",
        "research/example/plugin.py",  # denied until a `!research/example/**` line allows it
        "research/example/tests/test_x.py",
        "infra/main.tf",
        "research/x/__pycache__/a.pyc",
    ],
)
def test_matcher_excludes(rel: str) -> None:
    assert not docker_included(rel), rel


# ---------------------------------------------------------------- shipped plugins


def _module_file(module: str) -> Path | None:
    """The source file of a dotted module under the project (``research`` or ``src/fse``)."""
    base = PROJECT_DIR / "src" if module.split(".")[0] == "fse" else PROJECT_DIR
    stem = base.joinpath(*module.split("."))
    for path in (stem.with_suffix(".py"), stem / "__init__.py"):
        if path.is_file():
            return path
    return None


def _package_inits(module: str) -> Iterator[str]:
    parts = module.split(".")
    for n in range(1, len(parts)):
        yield ".".join(parts[:n])


def _research_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "research")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] != "research":
                continue
            found.add(node.module)
            # ``from research.x import y`` may name a submodule y
            for alias in node.names:
                if _module_file(f"{node.module}.{alias.name}") is not None:
                    found.add(f"{node.module}.{alias.name}")
    return found


def _closure(module: str) -> dict[str, Path | None]:
    """Every module the plugin imports, with its parent packages; None: no such file."""
    seen: dict[str, Path | None] = {}
    todo = [module]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = _module_file(name)
        seen[name] = path
        todo.extend(p for p in _package_inits(name) if p not in seen)
        if path is not None and name.split(".")[0] == "research":
            todo.extend(m for m in _research_imports(path) if m not in seen)
    return seen


def _yaml_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _yaml_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _yaml_strings(v)


def _plugin_entries() -> list[tuple[str, str, str, object]]:
    entries: list[tuple[str, str, str, object]] = []
    for cfg_path in sorted(CONFIGS_DIR.glob("*.yaml")):
        doc = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("plugins"), list):
            continue
        for entry in doc["plugins"]:
            entries.append((cfg_path.name, entry["id"], entry["module"], entry.get("config")))
    return entries


PLUGIN_ENTRIES: Final = _plugin_entries()


def test_some_runner_configs_are_checked() -> None:
    ids = {(cfg, pid) for cfg, pid, _, _ in PLUGIN_ENTRIES}
    assert ("runner.paper.yaml", "template") in ids
    assert ("runner.yaml", "template") in ids


@pytest.mark.parametrize(
    ("cfg_name", "plugin_id", "module", "config"),
    PLUGIN_ENTRIES,
    ids=[f"{c}:{p}" for c, p, _, _ in PLUGIN_ENTRIES],
)
def test_plugin_module_is_in_the_image(
    cfg_name: str, plugin_id: str, module: str, config: object
) -> None:
    mod = module.split(":", 1)[0]
    problems: list[str] = []
    for name, path in sorted(_closure(mod).items()):
        if path is None:
            problems.append(f"{name}: no source file")
            continue
        rel = path.relative_to(PROJECT_DIR).as_posix()
        if not docker_included(rel):
            problems.append(f"{rel}: excluded by .dockerignore")
    for value in _yaml_strings(config):
        is_project_yaml = value.endswith((".yaml", ".yml")) and (PROJECT_DIR / value).is_file()
        if is_project_yaml and not docker_included(value):
            problems.append(f"{value} (config file): excluded by .dockerignore")
    assert not problems, f"configs/{cfg_name} plugin {plugin_id!r} ({module}): {problems}"
