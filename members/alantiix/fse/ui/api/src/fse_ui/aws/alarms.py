"""CloudWatch alarm states for the Health page (explicit names, so IAM scopes to their ARNs)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from fse_ui.aws.errors import AWS_ERRORS, error_code

log = logging.getLogger(__name__)

CACHE_S = 60.0


class AlarmsReader:
    def __init__(
        self,
        cloudwatch: Any,
        names: Sequence[str],
        *,
        ttl_s: float = CACHE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cw = cloudwatch
        self._names = list(names)
        self._ttl = ttl_s
        self._clock = clock
        self._cache: tuple[float, list[dict[str, Any]]] | None = None

    def _describe(self) -> list[dict[str, Any]]:
        resp = self._cw.describe_alarms(AlarmNames=self._names)
        found: dict[str, dict[str, Any]] = {}
        for alarm in [*resp.get("MetricAlarms", []), *resp.get("CompositeAlarms", [])]:
            updated = alarm.get("StateUpdatedTimestamp")
            found[str(alarm["AlarmName"])] = {
                "name": str(alarm["AlarmName"]),
                "state": str(alarm.get("StateValue", "INSUFFICIENT_DATA")),
                "reason": alarm.get("StateReason"),
                "updated_at": updated.timestamp() if updated is not None else None,
            }
        return [found[n] for n in self._names if n in found]

    async def read(self) -> list[dict[str, Any]] | dict[str, str]:
        """``[{"name", "state", "reason", "updated_at"}]`` or ``{"error": ...}`` (cached 60 s)."""
        now = self._clock()
        if self._cache is not None and now - self._cache[0] < self._ttl:
            return self._cache[1]
        try:
            rows = await asyncio.to_thread(self._describe)
        except AWS_ERRORS as exc:
            log.warning("cannot read CloudWatch alarms: %s", error_code(exc))
            return {"error": f"cannot read CloudWatch alarms: {error_code(exc)}"}
        self._cache = (now, rows)
        return rows
