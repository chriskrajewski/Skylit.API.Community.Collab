"""The optional Narrator: an LLM that rewords a Finding_Card as prose (design §25, Req 25.10-25.15).

The Narrator is off by default (``notify.narrator.enabled``). When it is on,
the Notifier hands it each Finding_Card before delivery; the Narrator makes no
decision and nothing in the order path reads it.

**Input** (Req 25.10). Only :meth:`~fse.notify.finding_card.FindingCard.to_narrator_fields`,
an allow-list of card fields with no account id, passed through the
Log_Writer's Redactor, so no Secret_Variable value reaches the LLM. The
request is an OpenAI-compatible ``POST {base_url}/chat/completions`` with the
instructions in ``prompts/narrator.md`` (:data:`PROMPT_PATH`) as the system
message and the redacted fields as canonical JSON in the user message. The
``NARRATOR_API_KEY`` (:data:`API_KEY_ENV`, read from the ``EnvView``, never
from the config) goes in the ``Authorization`` header when it is set; a local
LLM may need none.

**Bounds** (Req 25.11-25.12). The call runs in its own task and is cancelled
after ``timeout_s`` (default 10 s) on the injected clock. The reply is the
first choice's ``message.content``, stripped. It is used only when it is a
non-empty string of at most ``max_chars`` characters (default 1,500).

**Fallback** (Req 25.12). Any failure (no ``base_url`` or ``model``, no
prompt file, an HTTP or network error, a reply of the wrong shape, empty
prose, prose over the cap, or the timeout) gives a :class:`Narration` without
prose and with a short cause. The Notifier then sends the unchanged card with
:data:`NARRATION_UNAVAILABLE` and logs the cause.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from fse.clock import Clock
from fse.config.schema.notify import NarratorConfig
from fse.live._wait import TimedOut, within
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND

__all__ = [
    "API_KEY_ENV",
    "COMPLETIONS_PATH",
    "NARRATION_UNAVAILABLE",
    "PROMPT_PATH",
    "Narration",
    "Narrator",
    "load_prompt",
]

API_KEY_ENV: Final = "NARRATOR_API_KEY"
COMPLETIONS_PATH: Final = "/chat/completions"
NARRATION_UNAVAILABLE: Final = "narration unavailable"
"""The note sent with a card whose narration failed (Req 25.12)."""
PROMPT_PATH: Final = Path(__file__).resolve().parents[3] / "prompts" / "narrator.md"
"""``prompts/narrator.md`` in the project folder (design "Module layout")."""


def load_prompt(path: Path = PROMPT_PATH) -> str | None:
    """The Narrator instructions, or ``None`` when the file cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    return text if text.strip() else None


@dataclass(frozen=True, slots=True)
class Narration:
    """The prose of one card, or ``None`` and why there is none."""

    prose: str | None
    cause: str | None = None


class _NoProse(Exception):
    """A reply that cannot be used; its message is the cause."""


class Narrator:
    """Rewords Finding_Card fields as prose, within the configured bounds (see the module notes)."""

    __slots__ = ("_cfg", "_clock", "_http", "_key", "_own_http", "_prompt", "_writer")

    def __init__(
        self,
        cfg: NarratorConfig,
        env: EnvView,
        writer: LogWriter,
        clock: Clock,
        *,
        http: httpx.AsyncClient | None = None,
        prompt: str | None = None,
    ) -> None:
        self._cfg = cfg
        self._writer = writer
        self._clock = clock
        self._http = http
        self._own_http = False
        key = env.get(API_KEY_ENV)
        self._key = key.strip() if key is not None and key.strip() else None
        self._prompt = load_prompt() if prompt is None else prompt

    def request_input(self, fields: dict[str, JsonValue]) -> JsonValue:
        """``fields`` passed through the Log_Writer's Redactor: what the LLM receives."""
        return self._writer.redact_json(fields)

    async def narrate(self, fields: dict[str, JsonValue]) -> Narration:
        """Prose for the card ``fields``, or a :class:`Narration` without prose and its cause."""
        timeout_s = self._cfg.timeout_s
        try:
            result = await within(
                self._clock, self._call(self.request_input(fields)), timeout_s * NS_PER_SECOND
            )
        except _NoProse as exc:
            return Narration(None, str(exc))
        except Exception as exc:  # any failure falls back to the card alone (Req 25.12)
            return Narration(None, self._writer.redact(f"{type(exc).__name__}: {exc}"))
        if isinstance(result, TimedOut):
            return Narration(None, f"no reply within {timeout_s} s")
        return Narration(result)

    async def _call(self, payload: JsonValue) -> str:
        cfg = self._cfg
        if cfg.base_url is None or cfg.model is None:
            raise _NoProse("notify.narrator.base_url and model must both be set")
        if self._prompt is None:
            raise _NoProse(f"the Narrator prompt {PROMPT_PATH.name} cannot be read")
        body = self._writer.json_bytes(
            {
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": self._prompt},
                    {"role": "user", "content": self._writer.json_text(payload)},
                ],
            }
        )
        headers = {"Content-Type": "application/json"}
        if self._key is not None:
            headers["Authorization"] = f"Bearer {self._key}"
        client = self._http
        if client is None:
            client = self._http = httpx.AsyncClient()
            self._own_http = True
        response = await client.post(
            cfg.base_url.rstrip("/") + COMPLETIONS_PATH,
            content=body,
            headers=headers,
            timeout=float(cfg.timeout_s),
        )
        if response.status_code >= 300:
            raise _NoProse(f"the Narrator returned HTTP {response.status_code}")
        return self._prose(response.content)

    def _prose(self, content: bytes) -> str:
        try:
            reply = json.loads(content)
            text = reply["choices"][0]["message"]["content"]
        except ValueError, KeyError, IndexError, TypeError:
            raise _NoProse("the Narrator reply has no choices[0].message.content") from None
        if not isinstance(text, str):
            raise _NoProse("the Narrator reply content is not text")
        prose = text.strip()
        if not prose:
            raise _NoProse("the Narrator returned empty prose")
        if len(prose) > self._cfg.max_chars:
            raise _NoProse(
                f"the Narrator prose has {len(prose)} characters, over {self._cfg.max_chars}"
            )
        return prose

    async def aclose(self) -> None:
        """Close the HTTP client the Narrator opened itself."""
        if self._own_http and self._http is not None:
            await self._http.aclose()
