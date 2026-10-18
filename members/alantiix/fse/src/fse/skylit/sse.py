"""Server-sent events framing for ``GET /v1/stream`` (design §23, Req 23.3-23.4).

Source: the WHATWG HTML "Server-sent events" event-stream format, and the
Skylit stream page
https://www.skylit.ai/docs/api-reference/heatmap/live-sse-stream-up-to-10-symbols-per-connection
(content rephrased for compliance with licensing restrictions).

:class:`SseDecoder` turns the stream's lines into :class:`SseMessage` values:

- ``event:`` sets the event name (default ``message``); ``data:`` lines are
  joined with ``\\n``; ``id:`` sets the last event id, which persists across
  events until another ``id:`` line (an id holding a NUL is ignored);
- a line starting with ``:`` is a comment (a keep-alive) and is dropped;
- a blank line dispatches the event, unless no ``data:`` line was seen;
- one space after the colon is removed; a line without a colon is a field
  with an empty value; ``retry:`` and unknown fields are ignored.

:func:`fse.skylit.models.parse_stream_event` decodes each message's data.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SseDecoder", "SseMessage"]


@dataclass(frozen=True, slots=True)
class SseMessage:
    """One dispatched event: name, data text and the last event id at dispatch."""

    event: str
    data: str
    event_id: str | None


class SseDecoder:
    """Feeds event-stream lines (without their line ending) and returns dispatched messages."""

    __slots__ = ("_data", "_event", "_last_id")

    def __init__(self, last_event_id: str | None = None) -> None:
        self._event = ""
        self._data: list[str] = []
        self._last_id = last_event_id

    @property
    def last_event_id(self) -> str | None:
        return self._last_id

    def feed(self, line: str) -> SseMessage | None:
        """Process one line; a blank line returns the dispatched message, if any."""
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        elif name == "id" and "\0" not in value:
            self._last_id = value
        return None

    def _dispatch(self) -> SseMessage | None:
        data, event = self._data, self._event
        self._data, self._event = [], ""
        if not data:
            return None
        return SseMessage(event or "message", "\n".join(data), self._last_id)
