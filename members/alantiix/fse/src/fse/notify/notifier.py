"""The Notifier: delivers Finding_Cards and 2R alerts to the configured sinks (design §25).

Sinks (``notify.sinks``):

- ``console``: the card text through the Log_Writer's ``echo``;
- ``file``: one canonical JSON line per message, appended to ``cards.jsonl``
  in the live run directory through the Log_Writer;
- ``webhook``: an HTTP ``POST`` of ``{"text", "message"}`` to
  ``NOTIFIER_WEBHOOK_URL``, read from the ``EnvView`` and never from the
  config (Req 17.5). The URL is a Secret_Variable, so the Log_Writer redacts
  it from every log line. Without the variable the webhook sink is skipped
  and that is logged once.

**Delivery** (Req 25.13-25.14). :meth:`Notifier.submit` only queues the
message and returns at once, so the Decision_Time never waits. :meth:`run`
is a separate task: it delivers each message to each sink within
:data:`DELIVERY_TIMEOUT_S`. A failure or a timeout is logged through the
Log_Writer (``notifier.jsonl``, plus one stderr line) and the next message
goes on; nothing in the order path reads the Notifier.

**Narrator** (Req 25.10-25.12, ``notify.narrator.enabled``). Before a
Finding_Card goes to the sinks, :meth:`deliver` hands its
:attr:`Message.narrator_fields` to the :class:`~fse.notify.narrator.Narrator`.
With prose, the one message sent holds the prose and the unchanged card
(text: the prose, a blank line, the card text; JSON: ``{"narration",
"card"}``). Without prose, it holds the unchanged card and
:data:`~fse.notify.narrator.NARRATION_UNAVAILABLE` (JSON: ``{"narration":
null, "narration_note", "card"}``), and the cause is logged. 2R alerts are not
narrated. With the Narrator off, messages go out exactly as queued.

Every sent message is also kept in :attr:`Notifier.sent` order for tests and
the run summary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from fse.clock import Clock
from fse.config.schema.notify import NotifyConfig
from fse.live._wait import TIMED_OUT, within
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue, ny_iso
from fse.notify.finding_card import Alert2R, FindingCard, render_alert, render_card
from fse.notify.narrator import NARRATION_UNAVAILABLE, Narrator
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND

__all__ = [
    "CARDS_FILE_NAME",
    "DELIVERY_TIMEOUT_S",
    "NOTIFIER_LOG_FILE_NAME",
    "WEBHOOK_ENV",
    "Message",
    "Notifier",
]

CARDS_FILE_NAME: Final = "cards.jsonl"
NOTIFIER_LOG_FILE_NAME: Final = "notifier.jsonl"
WEBHOOK_ENV: Final = "NOTIFIER_WEBHOOK_URL"
DELIVERY_TIMEOUT_S: Final = 10
"""How long one sink may take to deliver one message."""


@dataclass(frozen=True, slots=True)
class Message:
    """One Finding_Card or 2R alert, as text and as JSON.

    ``narrator_fields`` is the card's Narrator input; ``None`` for an alert and
    for a message that was already narrated.
    """

    kind: str
    text: str
    body: dict[str, JsonValue]
    narrator_fields: dict[str, JsonValue] | None = None

    @classmethod
    def card(cls, card: FindingCard) -> Message:
        return cls(
            f"card:{card.kind}", render_card(card), card.to_jsonable(), card.to_narrator_fields()
        )

    @classmethod
    def alert(cls, alert: Alert2R) -> Message:
        return cls("alert_2r", render_alert(alert), alert.to_jsonable())


type Sink = Callable[[Message], Awaitable[None]]


class Notifier:
    """Queues messages and delivers them in its own task (see the module notes)."""

    __slots__ = (
        "_clock",
        "_closed",
        "_http",
        "_log_path",
        "_narrator",
        "_queue",
        "_sinks",
        "_writer",
        "sent",
    )

    def __init__(
        self,
        cfg: NotifyConfig,
        env: EnvView,
        writer: LogWriter,
        clock: Clock,
        out_dir: Path,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._http = http
        self._log_path = Path(out_dir) / NOTIFIER_LOG_FILE_NAME
        self._queue: asyncio.Queue[Message | None] = asyncio.Queue()
        self._closed = False
        self.sent: list[Message] = []
        cards_path = Path(out_dir) / CARDS_FILE_NAME
        sinks: list[tuple[str, Sink]] = []
        for name in cfg.sinks:
            if name == "console":
                sinks.append((name, self._console))
            elif name == "file":
                sinks.append((name, self._file_sink(cards_path)))
            else:
                url = env.get(WEBHOOK_ENV)
                if url is None or not url.strip():
                    self._log({"event": "webhook_skipped", "cause": f"{WEBHOOK_ENV} is not set"})
                else:
                    sinks.append((name, self._webhook_sink(url.strip())))
        self._sinks: tuple[tuple[str, Sink], ...] = tuple(sinks)
        self._narrator = (
            Narrator(cfg.narrator, env, writer, clock, http=http) if cfg.narrator.enabled else None
        )

    @property
    def sink_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._sinks)

    def submit(self, message: Message) -> None:
        """Queue ``message``; never waits and never raises for a delivery problem."""
        if not self._closed:
            self._queue.put_nowait(message)

    def close(self) -> None:
        """Stop :meth:`run` after the queued messages."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    async def run(self) -> None:
        """Deliver queued messages until :meth:`close`; each sink within the timeout."""
        while True:
            message = await self._queue.get()
            if message is None:
                return
            await self.deliver(message)

    async def deliver(self, message: Message) -> Message:
        """Narrate ``message`` (when on) and deliver it to each sink; the message sent."""
        if self._narrator is not None and message.narrator_fields is not None:
            message = await self._narrated(self._narrator, message)
        for name, sink in self._sinks:
            await self._deliver(name, sink, message)
        self.sent.append(message)
        return message

    async def _narrated(self, narrator: Narrator, message: Message) -> Message:
        assert message.narrator_fields is not None
        narration = await narrator.narrate(message.narrator_fields)
        if narration.prose is not None:
            return Message(
                message.kind,
                f"{narration.prose}\n\n{message.text}",
                {"narration": narration.prose, "card": message.body},
            )
        self._log({"event": "narration_failed", "message": message.kind, "cause": narration.cause})
        return Message(
            message.kind,
            f"{message.text}\nNarration: {NARRATION_UNAVAILABLE}",
            {"narration": None, "narration_note": NARRATION_UNAVAILABLE, "card": message.body},
        )

    async def _deliver(self, name: str, sink: Sink, message: Message) -> None:
        try:
            result = await within(self._clock, sink(message), DELIVERY_TIMEOUT_S * NS_PER_SECOND)
        except Exception as exc:  # a sink failure never stops the Live_Runner (Req 25.14)
            self._failed(name, message, f"{type(exc).__name__}: {exc}")
            return
        if result is TIMED_OUT:
            self._failed(name, message, f"no delivery within {DELIVERY_TIMEOUT_S} s")

    def _failed(self, sink: str, message: Message, cause: str) -> None:
        self._log(
            {"event": "delivery_failed", "sink": sink, "message": message.kind, "cause": cause}
        )
        self._writer.error(f"notifier: {sink} delivery of a {message.kind} failed: {cause}")

    def _log(self, entry: dict[str, JsonValue]) -> None:
        now = self._clock.now()
        self._writer.append_json(self._log_path, {"at": now, "at_ny": ny_iso(now), **entry})

    # ------------------------------------------------------------ sinks

    async def _console(self, message: Message) -> None:
        self._writer.echo(message.text)

    def _file_sink(self, path: Path) -> Sink:
        async def deliver(message: Message) -> None:
            self._writer.append_json(path, message.body)

        return deliver

    def _webhook_sink(self, url: str) -> Sink:
        async def deliver(message: Message) -> None:
            client = self._http
            if client is None:
                client = self._http = httpx.AsyncClient()
            body = self._writer.json_bytes({"text": message.text, "message": message.body})
            response = await client.post(
                url, content=body, headers={"Content-Type": "application/json"}
            )
            if response.status_code >= 300:
                raise RuntimeError(f"the webhook returned HTTP {response.status_code}")

        return deliver

    async def aclose(self) -> None:
        """Close the HTTP client, if one was opened, and the Narrator's."""
        if self._narrator is not None:
            await self._narrator.aclose()
        if self._http is not None:
            await self._http.aclose()

    def pending(self) -> int:
        """Messages queued and not yet delivered."""
        return self._queue.qsize()
