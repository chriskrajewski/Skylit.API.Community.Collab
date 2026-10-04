"""The ``notify`` section of the Strategy_Config (design §25, "Strategy_Config shape").

Settings of the Finding_Card sender, the Notifier and the optional Narrator
(Req 25):

- ``sinks``: where Finding_Cards go, each once: ``console``, ``file`` and
  ``webhook`` (default ``[console, file]``). The webhook URL is the
  ``NOTIFIER_WEBHOOK_URL`` environment variable, never a config key (Req 17.5).
- ``interval_min``: send the latest Finding_Card when this many minutes have
  passed since the last one (default 5, 1 to 60, Req 25.5).
- ``premarket``: the opening Finding_Card time, a quoted ``"HH:MM"`` New York
  time before 09:30 (default ``"09:00"``, Req 25.7).
- ``alerts_2r``: one alert per Setup_Key the first time it is graded Alert_2R
  (default on, Req 25.8-25.9).
- ``narrator``: off by default (Req 25.10). ``base_url`` is the Narrator
  endpoint, an ``http`` or ``https`` URL with no user info, query or fragment,
  so it cannot carry a credential; the API key is the ``NARRATOR_API_KEY``
  environment variable. ``model`` names the LLM. ``timeout_s`` (default 10,
  1 to 60) and ``max_chars`` (default 1,500, 1 to 10,000) bound the reply
  (Req 25.11-25.12).
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, field_validator
from pydantic_core import PydanticCustomError

from fse.config.schema._base import ClockTime, SchemaModel, YamlList, clock_text, require_unique
from fse.timekit import RTH_OPEN

__all__ = [
    "INTERVAL_MAX_MIN",
    "NARRATOR_MAX_CHARS_MAX",
    "NARRATOR_TIMEOUT_MAX_S",
    "SINK_NAMES",
    "ModelName",
    "NarratorBaseUrl",
    "NarratorConfig",
    "NotifyConfig",
    "SinkName",
]

type SinkName = Literal["console", "file", "webhook"]

SINK_NAMES: Final[tuple[str, ...]] = ("console", "file", "webhook")

INTERVAL_MAX_MIN: Final = 60
NARRATOR_TIMEOUT_MAX_S: Final = 60
NARRATOR_MAX_CHARS_MAX: Final = 10_000

type NarratorBaseUrl = Annotated[
    str,
    StringConstraints(
        pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]*)?$",
        max_length=2048,
    ),
]
"""An ``http(s)`` URL with host, optional port and path; no user info, query or fragment."""

type ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")]
"""An LLM model name such as ``gpt-4o-mini`` or ``llama3.1:8b``."""


class NarratorConfig(SchemaModel):
    """The optional LLM that rewords a Finding_Card; it makes no decision (Req 25.10-25.15)."""

    enabled: bool = False
    base_url: NarratorBaseUrl | None = None
    model: ModelName | None = None
    timeout_s: int = Field(10, ge=1, le=NARRATOR_TIMEOUT_MAX_S)
    max_chars: int = Field(1500, ge=1, le=NARRATOR_MAX_CHARS_MAX)


class NotifyConfig(SchemaModel):
    """Sinks, card interval, premarket time, 2R alerts and the Narrator."""

    sinks: Annotated[YamlList[SinkName], Field(min_length=1)] = ("console", "file")
    interval_min: int = Field(5, ge=1, le=INTERVAL_MAX_MIN)
    premarket: ClockTime = time(9, 0)
    alerts_2r: bool = True
    narrator: NarratorConfig = NarratorConfig()

    @field_validator("sinks")
    @classmethod
    def _unique(cls, value: tuple[SinkName, ...]) -> tuple[SinkName, ...]:
        return require_unique(value)

    @field_validator("premarket")
    @classmethod
    def _before_open(cls, value: time) -> time:
        if not value < RTH_OPEN:
            raise PydanticCustomError(
                "less_than",
                "premarket {value} should be before 09:30",
                {"value": clock_text(value), "lt": "09:30"},
            )
        return value
