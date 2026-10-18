"""Helpers the guided workflows need beyond the generic command form.

- Pull estimate: ``fse pull`` estimates before it sends any Replay_Request and,
  without a terminal, stops with exit 2 when the estimate is over
  ``--size-limit-gb``. A probe run with a tiny limit therefore prints the
  estimate and stops (nothing is fetched unless the estimate is zero bytes,
  i.e. every window is already cached). The confirmed run then raises the limit.
- Uploads: CSV files for ``fse import-vix``, stored per upload under
  ``<data>/ui/uploads/<id>/``.
"""

from __future__ import annotations

import math
import re
import secrets
from pathlib import Path
from typing import Any

PROBE_LIMIT_GB = 1e-9
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{16}$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_OVER = re.compile(r"estimated disk size ([\d,.]+) GB exceeds the size limit")
# Console task status/heartbeat lines and entrypoint progress lines are never table rows.
_STATUS = re.compile(r"^(task \S+: |fse-entrypoint: )")


class UploadError(ValueError):
    pass


def parse_pull_estimate(lines: list[str]) -> dict[str, Any]:
    """The estimate a probe pull printed: GB, the table lines, and whether it stopped."""
    gb: float | None = None
    table: list[str] = []
    collecting = False
    for line in lines:
        if "Pull estimate for" in line:
            collecting = True
        m = _OVER.search(line)
        if m:
            gb = float(m.group(1).replace(",", ""))
            collecting = False
        if collecting and not _STATUS.match(line):
            table.append(line)
    return {
        "estimated_gb": gb,
        "stopped_at_limit": gb is not None,
        "lines": table,
        "suggested_limit_gb": None if gb is None else max(1.0, math.ceil(gb * 1.1 * 100) / 100),
    }


def save_upload(root: Path, filename: str, data: bytes) -> dict[str, Any]:
    if len(data) == 0:
        raise UploadError("the file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"the file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not filename.lower().endswith(".csv"):
        raise UploadError("only .csv files are accepted")
    try:
        head = data[:65536].decode("utf-8")
    except UnicodeDecodeError:
        raise UploadError("the file is not UTF-8 text") from None
    if "\x00" in head or "," not in head.split("\n", 1)[0]:
        raise UploadError("the file does not look like a CSV (no comma in the header line)")
    upload_id = secrets.token_hex(8)
    name = _SAFE_NAME.sub("_", Path(filename).name)[:100] or "upload.csv"
    folder = root / upload_id
    folder.mkdir(parents=True, mode=0o700)
    path = folder / name
    path.write_bytes(data)
    return {"id": upload_id, "name": name, "size": len(data)}


def upload_path(root: Path, upload_id: str) -> Path:
    if not UPLOAD_ID_RE.fullmatch(upload_id):
        raise UploadError("invalid upload id")
    folder = root / upload_id
    files = sorted(folder.glob("*.csv")) if folder.is_dir() else []
    if not files:
        raise UploadError("no such upload")
    return files[0]
