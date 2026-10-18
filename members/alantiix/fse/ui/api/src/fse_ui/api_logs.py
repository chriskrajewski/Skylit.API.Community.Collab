"""GET /api/api-logs: the shared Skylit and ProjectX call ring."""

from __future__ import annotations

import time
from pathlib import Path

from fse.apilog import FILE_NAME, query

__all__ = ["FILE_NAME", "list_calls"]


def list_calls(
    directory: Path,
    *,
    api: str,
    status: str,
    endpoint: str,
    limit: int,
) -> dict[str, object]:
    """Calls retained in ``directory / api_calls.jsonl``.

    A missing file is an empty log. ``ValueError`` is a bad filter.
    ``OSError`` means the file could not be read.
    """
    return query(
        directory / FILE_NAME,
        now_ns=time.time_ns(),
        api=api,
        status=status,
        endpoint=endpoint,
        limit=limit,
    )
