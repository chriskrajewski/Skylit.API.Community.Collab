"""Write-only secrets: the console reports only whether each one is set.

``LocalSecretStore`` keeps values in ``<data>/ui/secrets.json`` (mode 0600) and
passes them to the commands it launches. ``SsmSecretStore`` writes
``/fse/secrets/<NAME>`` SecureStrings (the runner host and batch tasks read
them there). No method returns a value to a caller outside this module.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol

SECRET_NAMES: tuple[str, ...] = (
    "SKYLIT_API_KEY",
    "PROJECTX_USERNAME",
    "PROJECTX_API_KEY",
    "PRACTICE_ACCOUNT_ID",
    "COMBINE_ACCOUNT_ID",
    "NOTIFIER_WEBHOOK_URL",
    "NARRATOR_API_KEY",
    "FSE_SECRET_VARS",
)
PLACEHOLDER_PREFIX = "PLACEHOLDER"
MAX_SECRET_CHARS = 4096
SSM_PREFIX = "/fse/secrets/"

type SecretStatus = Literal["set", "placeholder", "missing"]
type IdShape = Literal["unset", "not_numeric", "numeric"]


class SecretError(ValueError):
    """An unknown name or an unusable value (the message never holds the value)."""


def _status(value: str | None) -> SecretStatus:
    if value is None or not value.strip():
        return "missing"
    return "placeholder" if value.startswith(PLACEHOLDER_PREFIX) else "set"


def check_secret(name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise SecretError(f"{name!r} is not a known secret name")
    if not value.strip():
        raise SecretError(f"{name}: the value is blank")
    if len(value) > MAX_SECRET_CHARS:
        raise SecretError(f"{name}: the value is longer than {MAX_SECRET_CHARS} characters")
    if re.search(r"[\r\n\x00]", value):
        raise SecretError(f"{name}: the value must be one line")
    if value.startswith(PLACEHOLDER_PREFIX):
        raise SecretError(f"{name}: the value starts with {PLACEHOLDER_PREFIX}")


class SecretStore(Protocol):
    kind: str

    def statuses(self) -> dict[str, SecretStatus]: ...

    def put(self, name: str, value: str) -> None: ...

    def child_env(self) -> dict[str, str]:
        """Values to give the commands this console launches locally."""
        ...

    def known_values(self) -> list[str]:
        """Every non-blank value, for redacting command output."""
        ...

    def same(self, a: str, b: str) -> bool | None:
        """Whether secrets ``a`` and ``b`` hold the same value; ``None`` when either is
        unset or a placeholder. Only the bool leaves the store; no value is logged."""
        ...

    def equals(self, name: str, value: str) -> bool | None:
        """Whether secret ``name`` holds ``value``; ``None`` when it is unset or a
        placeholder. Only the bool leaves the store; no value is logged."""
        ...

    def id_shape(self, name: str) -> IdShape:
        """``unset`` (missing or placeholder), ``numeric`` (an account id ProjectX can
        match) or ``not_numeric`` (for example the account name). Never the value."""
        ...


def _same(values: dict[str, str], a: str, b: str) -> bool | None:
    left, right = values.get(a), values.get(b)
    if _status(left) != "set" or _status(right) != "set":
        return None
    return left == right


def _equals(values: dict[str, str], name: str, value: str) -> bool | None:
    stored = values.get(name)
    if stored is None or _status(stored) != "set":
        return None
    return stored == value


def _id_shape(values: dict[str, str], name: str) -> IdShape:
    stored = values.get(name)
    if stored is None or _status(stored) != "set":
        return "unset"
    numeric = stored.isascii() and stored.isdigit() and str(int(stored)) == stored
    return "numeric" if numeric else "not_numeric"


class LocalSecretStore:
    kind = "local"

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except OSError, ValueError:
            return {}
        return {k: v for k, v in data.items() if k in SECRET_NAMES and isinstance(v, str)}

    def statuses(self) -> dict[str, SecretStatus]:
        data = self._read()
        return {name: _status(data.get(name)) for name in SECRET_NAMES}

    def put(self, name: str, value: str) -> None:
        check_secret(name, value)
        data = self._read()
        data[name] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".secrets-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def child_env(self) -> dict[str, str]:
        return {k: v for k, v in self._read().items() if _status(v) == "set"}

    def known_values(self) -> list[str]:
        return [v for v in self._read().values() if v.strip()]

    def same(self, a: str, b: str) -> bool | None:
        return _same(self._read(), a, b)

    def equals(self, name: str, value: str) -> bool | None:
        return _equals(self._read(), name, value)

    def id_shape(self, name: str) -> IdShape:
        return _id_shape(self._read(), name)


class SsmSecretStore:
    kind = "ssm"

    def __init__(self, client: Any) -> None:
        self._ssm = client

    def _values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        paginator = self._ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=SSM_PREFIX, WithDecryption=True):
            for p in page.get("Parameters", []):
                name = str(p["Name"]).removeprefix(SSM_PREFIX)
                if name in SECRET_NAMES:
                    out[name] = str(p.get("Value", ""))
        return out

    def statuses(self) -> dict[str, SecretStatus]:
        values = self._values()
        return {name: _status(values.get(name)) for name in SECRET_NAMES}

    def put(self, name: str, value: str) -> None:
        check_secret(name, value)
        self._ssm.put_parameter(
            Name=f"{SSM_PREFIX}{name}", Value=value, Type="SecureString", Overwrite=True
        )

    def child_env(self) -> dict[str, str]:
        # On AWS the runner host and batch tasks read SSM themselves.
        return {}

    def known_values(self) -> list[str]:
        return [v for v in self._values().values() if v.strip()]

    def same(self, a: str, b: str) -> bool | None:
        return _same(self._values(), a, b)

    def equals(self, name: str, value: str) -> bool | None:
        return _equals(self._values(), name, value)

    def id_shape(self, name: str) -> IdShape:
        return _id_shape(self._values(), name)


class Redactor:
    """Replaces every known secret value in a line of command output."""

    def __init__(self, values: list[str]) -> None:
        self._values = sorted({v for v in values if len(v) >= 4}, key=len, reverse=True)

    def __call__(self, line: str) -> str:
        for value in self._values:
            if value in line:
                line = line.replace(value, "[redacted]")
        return line
