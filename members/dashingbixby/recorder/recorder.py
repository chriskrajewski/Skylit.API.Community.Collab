"""Recorder: raw Skylit Heatseeker stream events to disk, as received, with receive timestamps.

Built for the one-day recorder trial (docs/build-order.md, "Recorder trial"): run one trading day,
then read `trial_report` and decide keep / reduce / stop.

Output: runtime/recordings/<trading_day>/heatseeker_stream.jsonl (gzipped at stop) plus run.json.
One JSON object per line:
  {"recv_utc", "recv_ns", "conn", "kind", ...}
  kind "event":   event, id, data (raw data string, not re-serialised)
  kind "comment": data (": ping" keepalives)
  kind "meta":    what (start | conn_open | conn_end | conn_error | backoff | stop), detail

Run:
  PYTHONPATH=src python3 -m agentic_trading.logging.recorder --start-at 09:25 --stop-at 16:00
Times are configured in ET (glossary: store UTC, configure ET, display CT).
"""
from __future__ import annotations

import argparse
import gzip
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from agentic_trading.market.skylit.sse import SSEEvent, SSEParser
from agentic_trading.market.skylit.stream import SkylitStream, StreamHTTPError

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
STREAM_FILE = "heatseeker_stream.jsonl"
RUN_FILE = "run.json"

# Skylit `closed` reasons. All of them end the stream for the day.
TERMINAL_CLOSE_REASONS = {
    "insufficient_credits", "account_suspended", "key_revoked", "monthly_cap_reached", "credit_check_failed",
}
RETRYABLE_NET_ERRORS = (
    socket.timeout, TimeoutError, ConnectionError, urllib.error.URLError, http.client.HTTPException, OSError,
)


class StopRequested(Exception):
    """Raised from the SIGTERM/SIGINT handler to end the run cleanly."""


@dataclass
class RunSummary:
    trading_day: str
    symbols: list[str]
    started_utc: str
    stopped_utc: str | None = None
    stop_reason: str | None = None
    stop_at_utc: str | None = None
    credit_cap: int | None = None
    connections: int = 0
    events: int = 0
    comments: int = 0
    events_by_type: dict = field(default_factory=dict)
    credits_remaining_first: float | None = None
    credits_remaining_last: float | None = None
    credits_charged_sum: float = 0.0
    credits_used: float | None = None
    last_event_id: str | None = None
    resumed_from_event_id: str | None = None
    file: str | None = None


class Recorder:
    def __init__(
        self,
        stream: SkylitStream,
        out_dir: Path,
        *,
        stop_at: datetime,
        credit_cap: int | None = 2000,
        trading_day: str | None = None,
        compress: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
        max_backoff: float = 60.0,
        log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True),
    ):
        self.stream = stream
        self.clock = clock
        self.sleep = sleep
        self.stop_at = stop_at.astimezone(timezone.utc)
        self.credit_cap = credit_cap
        self.compress = compress
        self.max_backoff = max_backoff
        self.log = log
        day = trading_day or clock().astimezone(ET).date().isoformat()
        self.day_dir = Path(out_dir) / day
        self.path = self.day_dir / STREAM_FILE
        self.summary = RunSummary(
            trading_day=day,
            symbols=list(stream.symbols),
            started_utc=clock().isoformat(),
            stop_at_utc=self.stop_at.isoformat(),
            credit_cap=credit_cap,
        )
        self._fh = None
        self._conn = 0

    # ---------- output ----------

    def _write(self, kind: str, **fields) -> None:
        rec = {"recv_utc": self.clock().isoformat(), "recv_ns": time.time_ns(), "conn": self._conn, "kind": kind}
        rec.update(fields)
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._fh.flush()  # one line per event survives a crash

    def _meta(self, what: str, **detail) -> None:
        self._write("meta", what=what, detail=detail)

    @staticmethod
    def last_event_id_in(path: Path) -> str | None:
        """Resume cursor from an existing in-progress file (crash or restart mid-day)."""
        if not path.exists():
            return None
        last = None
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn last line after a crash
                if rec.get("kind") == "event" and rec.get("id"):
                    last = rec["id"]
        return last

    # ---------- credits ----------

    def _track_credits(self, ev: SSEEvent) -> None:
        if ev.event not in ("connected", "credits"):
            return
        try:
            d = json.loads(ev.data)
        except json.JSONDecodeError:
            return
        remaining = d.get("creditsRemaining") if ev.event == "connected" else d.get("remaining")
        if ev.event == "credits" and isinstance(d.get("charged"), (int, float)):
            self.summary.credits_charged_sum += d["charged"]
        if isinstance(remaining, (int, float)):
            if self.summary.credits_remaining_first is None:
                self.summary.credits_remaining_first = remaining
            self.summary.credits_remaining_last = remaining
        s = self.summary
        if s.credits_remaining_first is not None and s.credits_remaining_last is not None:
            # Account-wide balance delta: also counts other Skylit use during the run.
            s.credits_used = s.credits_remaining_first - s.credits_remaining_last
        else:
            s.credits_used = s.credits_charged_sum

    def _over_cap(self) -> bool:
        return self.credit_cap is not None and (self.summary.credits_used or 0) >= self.credit_cap

    # ---------- main loop ----------

    def _past_stop(self) -> bool:
        return self.clock() >= self.stop_at

    def _stream_once(self, last_id: str | None) -> tuple[str | None, str | None, bool]:
        """One connection. Returns (last_id, stop_reason or None, got_events)."""
        parser = SSEParser()
        got_events = False
        reconnect_requested = False
        self._meta("conn_open", last_event_id=last_id)
        for line in self.stream.open(last_id):
            ev = parser.feed(line)
            if ev is not None:
                if ev.comment:
                    self.summary.comments += 1
                    self._write("comment", data=ev.data)
                else:
                    got_events = True
                    self.summary.events += 1
                    self.summary.events_by_type[ev.event] = self.summary.events_by_type.get(ev.event, 0) + 1
                    self._write("event", event=ev.event, id=ev.id, data=ev.data)
                    if ev.id:
                        last_id = ev.id
                        self.summary.last_event_id = ev.id
                    self._track_credits(ev)
                    if ev.event == "closed":
                        reason = _json_field(ev.data, "reason") or "unknown"
                        return last_id, f"closed:{reason}", got_events
                    if ev.event == "reconnect":
                        reconnect_requested = True
                    if self._over_cap():
                        return last_id, "credit_cap", got_events
            if self._past_stop():
                return last_id, "stop_at", got_events
        # Server closed the connection (normally right after a `reconnect` event at the 1-hour limit).
        self._meta("conn_end", reconnect_requested=reconnect_requested)
        return last_id, None, got_events

    def run(self) -> RunSummary:
        self.day_dir.mkdir(parents=True, exist_ok=True)
        last_id = self.last_event_id_in(self.path)
        self.summary.resumed_from_event_id = last_id
        self._fh = self.path.open("a", encoding="utf-8")
        reason = None
        backoff = 1.0
        try:
            self._meta("start", symbols=self.stream.symbols, stop_at_utc=self.stop_at.isoformat(),
                       credit_cap=self.credit_cap, resume_from=last_id)
            self.log(f"recorder: {self.summary.trading_day} {','.join(self.stream.symbols)} until "
                     f"{self.stop_at.astimezone(CT):%H:%M} CT -> {self.path}")
            while reason is None:
                if self._past_stop():
                    reason = "stop_at"
                    break
                self._conn += 1
                self.summary.connections = self._conn
                wait = None
                try:
                    last_id, reason, healthy = self._stream_once(last_id)
                    if reason is None:
                        if healthy:
                            backoff = 1.0
                            continue  # server-requested reconnect or normal end: reconnect now
                        wait = backoff
                except StreamHTTPError as e:
                    self._meta("conn_error", status=e.status, body=e.body[:500])
                    if not e.retryable:
                        reason = f"http_{e.status}"
                        break
                    wait = e.retry_after or backoff
                except StopRequested:
                    raise
                except RETRYABLE_NET_ERRORS as e:
                    self._meta("conn_error", error=f"{type(e).__name__}: {e}")
                    wait = backoff
                if wait is not None and reason is None:
                    wait = min(wait, max(0.0, (self.stop_at - self.clock()).total_seconds()))
                    self._meta("backoff", seconds=wait)
                    self.log(f"recorder: reconnect in {wait:.0f}s")
                    self.sleep(wait)
                    backoff = min(backoff * 2, self.max_backoff)
        except StopRequested:
            reason = "signal"
        finally:
            self.summary.stop_reason = reason
            self.summary.stopped_utc = self.clock().isoformat()
            if self._fh is not None:
                self._meta("stop", reason=reason, credits_used=self.summary.credits_used)
                self._fh.close()
                self._fh = None
            self.summary.file = str(self._finalize_file())
            (self.day_dir / RUN_FILE).write_text(json.dumps(asdict(self.summary), indent=2) + "\n")
            self.log(f"recorder: stopped ({reason}); {self.summary.events} events, "
                     f"credits used {self.summary.credits_used}")
        return self.summary

    def _finalize_file(self) -> Path:
        if not self.compress or not self.path.exists():
            return self.path
        gz = self.path.with_suffix(self.path.suffix + ".gz")
        # Append as a new gzip member if an earlier run today already compressed; gzip reads both.
        with self.path.open("rb") as src, gzip.open(gz, "ab") as dst:
            shutil.copyfileobj(src, dst)
        self.path.unlink()
        return gz


def _json_field(data: str, key: str):
    try:
        v = json.loads(data)
    except json.JSONDecodeError:
        return None
    return v.get(key) if isinstance(v, dict) else None


# ---------- CLI ----------

def resolve_api_key() -> str:
    """SKYLIT_API_KEY env var, else macOS Keychain item (architecture: secrets in Keychain)."""
    key = os.environ.get("SKYLIT_API_KEY")
    if key:
        return key.strip()
    item = os.environ.get("SKYLIT_API_KEY_KEYCHAIN_ITEM", "agentic-trading.skylit.api_key")
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", item, "-w"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise SystemExit(
            f"No Skylit API key. Set SKYLIT_API_KEY or add it to Keychain:\n"
            f"  security add-generic-password -s {item} -a skylit -w '<key>'"
        )


def _at_today(hhmm: str, now: datetime, tz: ZoneInfo) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    local = now.astimezone(tz)
    return local.replace(hour=h, minute=m, second=0, microsecond=0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Record the Skylit Heatseeker stream for one trading day (recorder trial).")
    p.add_argument("--symbols", default="SPY,QQQ,IWM,SPX", help="comma list, max 10 (default SPY,QQQ,IWM,SPX)")
    p.add_argument("--start-at", default=None, help="HH:MM ET; wait until then (default: start now)")
    p.add_argument("--stop-at", default="16:00", help="HH:MM ET (default 16:00)")
    p.add_argument("--credit-cap", type=int, default=2000, help="stop when credits used reach this (0 = no cap)")
    p.add_argument("--out", default="runtime/recordings", help="output root (default runtime/recordings)")
    p.add_argument("--metric", default="gamma", choices=["gamma", "vanna"])
    p.add_argument("--max-strikes", default=None, help="Skylit maxStrikes (default: server default 92)")
    p.add_argument("--max-expirations", default=None, help="Skylit maxExpirations (default: server default 5)")
    p.add_argument("--base-url", default="https://api.skylit.ai")
    p.add_argument("--no-compress", action="store_true", help="keep the raw .jsonl instead of gzipping at stop")
    a = p.parse_args(argv)

    now = datetime.now(timezone.utc)
    stop_at = _at_today(a.stop_at, now, ET)
    if stop_at <= now:
        print(f"stop time {a.stop_at} ET has already passed today", file=sys.stderr)
        return 2
    if a.start_at:
        start = _at_today(a.start_at, now, ET)
        wait = (start - now).total_seconds()
        if wait > 0:
            print(f"recorder: waiting {timedelta(seconds=int(wait))} until {a.start_at} ET", file=sys.stderr, flush=True)
            time.sleep(wait)

    stream = SkylitStream(
        resolve_api_key(), [s.strip() for s in a.symbols.split(",") if s.strip()],
        base_url=a.base_url, metric=a.metric, max_strikes=a.max_strikes, max_expirations=a.max_expirations,
    )
    rec = Recorder(stream, Path(a.out), stop_at=stop_at, credit_cap=a.credit_cap or None, compress=not a.no_compress)

    def _stop(signum, frame):
        raise StopRequested()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    s = rec.run()
    print(f"recording: {s.file}\nnext: PYTHONPATH=src python3 -m agentic_trading.logging.trial_report {s.trading_day}")
    return 0 if s.stop_reason in ("stop_at", "signal", "credit_cap") else 1


if __name__ == "__main__":
    raise SystemExit(main())
