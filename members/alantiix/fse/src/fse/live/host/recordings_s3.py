"""Data-plane recordings offloaded to S3, for the replay (``fse backtest-plugin --recordings-s3``).

The runner host uploads closed days to ``s3://<bucket>/<prefix>recordings/dataplane/``
with the local file names (``<day>.jsonl.gz``, ``<day>.part*.jsonl.gz``,
``<day>.quotes.jsonl.gz``). :class:`S3Recordings` lists a day's names (coverage
needs nothing more) and downloads a day into a local recordings root, where the
replay reads it with the same code as a local recording. Read only: nothing here
writes to S3. boto3 is imported inside :meth:`S3Recordings.__init__` only when no
client is given, so ``fse`` starts without it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Final

from fse.live.recorder import RECORDINGS_DIR_NAME

__all__ = ["S3Recordings", "is_quotes_name"]

_DATAPLANE: Final = "dataplane"
_QUOTES: Final = ".quotes.jsonl.gz"
_FILE: Final = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})(?:\.part[^/]*)?\.jsonl\.gz$"
    r"|^(?P<qday>\d{4}-\d{2}-\d{2})\.quotes\.jsonl\.gz$"
)


def _day_of(name: str) -> str | None:
    found = _FILE.fullmatch(name)
    if found is None:
        return None
    day = found.group("day") or found.group("qday")
    return day


def is_quotes_name(name: str) -> bool:
    """Whether a recording file name is a day's quote file."""
    return name.endswith(_QUOTES)


class S3Recordings:
    """``s3://bucket[/prefix]`` holding ``recordings/dataplane/`` (see the module notes)."""

    __slots__ = ("_client", "_index", "bucket", "errors", "prefix", "uri")

    def __init__(self, uri: str, client: Any | None = None) -> None:
        if not uri.startswith("s3://") or not uri[5:].split("/", 1)[0]:
            raise ValueError(f"{uri!r} is not an s3://bucket[/prefix] URI")
        bucket, _, prefix = uri[5:].partition("/")
        prefix = prefix.strip("/")
        self.uri = uri
        self.bucket = bucket
        self.prefix = f"{prefix}/" if prefix else ""
        self.errors: list[str] = []
        """Listing failures (one line), reported as coverage warnings."""
        self._index: dict[str, list[str]] | None = None
        if client is None:
            import boto3

            session = boto3.Session()
            session.get_credentials()  # resolved now: the replay later moves HOME (~/.aws)
            client = session.client("s3")
        self._client = client

    def _dir(self) -> str:
        return f"{self.prefix}{RECORDINGS_DIR_NAME}/{_DATAPLANE}/"

    def _index_names(self) -> dict[str, list[str]]:
        """Every dataplane file, keyed by day. One listing; a failure is remembered."""
        if self._index is not None:
            return self._index
        base = self._dir()
        by_day: dict[str, list[str]] = {}
        token: str | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": base}
                if token is not None:
                    kwargs["ContinuationToken"] = token
                page = self._client.list_objects_v2(**kwargs)
                for obj in page.get("Contents") or ():
                    name = str(obj.get("Key", ""))[len(base) :]
                    day = _day_of(name)
                    if day is None:
                        continue
                    by_day.setdefault(day, []).append(name)
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
        except Exception as exc:  # botocore errors: no credentials, AccessDenied, network
            self.errors.append(f"cannot list {self.uri} recordings: {type(exc).__name__}: {exc}")
            self._index = {}
            return self._index
        for names in by_day.values():
            names.sort()
        self._index = by_day
        return by_day

    def names(self, day: date) -> list[str]:
        """The day's recording file names in S3 (sorted); ``[]`` when listing fails."""
        return list(self._index_names().get(day.isoformat(), []))

    def fetch(self, day: date, dest_root: Path) -> list[Path]:
        """Download the day's files to ``<dest_root>/recordings/dataplane/``; the local paths.

        A download failure raises (the replay reports it as a fault). The listing
        is the cached index from :meth:`names`.
        """
        folder = Path(dest_root) / RECORDINGS_DIR_NAME / _DATAPLANE
        folder.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        for name in self.names(day):
            dest = folder / name
            out.append(dest)
            self._client.download_file(self.bucket, f"{self._dir()}{name}", str(dest))
        return out
