"""The Config_Schema defines no credential key (design §17 step 5, Req 17.5).

Walks every field name and alias of :class:`StrategyConfig`, through nested
models, optional fields, lists and type aliases, and fails on a name matching
``key|token|secret|password|webhook|url|account_id|account_name|username``.
The Narrator ``base_url`` is the one allow-listed name: it is not a webhook,
and its type refuses user info and query strings, so it cannot hold a
credential. Credentials stay in the environment (``fse.secrets.env``): no
field is named after a Secret_Variable or an account id variable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated, Final, TypeAliasType, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from fse.config.schema import StrategyConfig
from fse.config.schema.notify import NarratorConfig
from fse.secrets.env import SECRET_LIST_VARIABLE, SECRET_VARIABLES

CREDENTIAL_NAME: Final = re.compile(
    r"key|token|secret|password|webhook|url|account_id|account_name|username", re.IGNORECASE
)
ALLOWED_PATHS: Final = frozenset({"notify.narrator.base_url"})
ACCOUNT_ID_VARIABLES: Final = ("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID")


def nested_models(annotation: object) -> Iterator[type[BaseModel]]:
    """Every model class inside ``annotation``: unions, tuples, Annotated and type aliases."""
    if isinstance(annotation, TypeAliasType):
        yield from nested_models(annotation.__value__)
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    if get_origin(annotation) is Annotated:
        yield from nested_models(get_args(annotation)[0])
        return
    origin = get_origin(annotation)
    if isinstance(origin, TypeAliasType):
        yield from nested_models(origin.__value__)
    for arg in get_args(annotation):
        yield from nested_models(arg)


def field_names(model: type[BaseModel], prefix: str = "") -> Iterator[tuple[str, str]]:
    """``(key path, name)`` for every field name and alias below ``model``."""
    for name, info in model.model_fields.items():
        shown = info.alias or name
        for each in dict.fromkeys((name, shown)):
            yield f"{prefix}{shown}", each
        for sub in dict.fromkeys(nested_models(info.annotation)):
            yield from field_names(sub, f"{prefix}{shown}.")


def credential_names(model: type[BaseModel]) -> list[str]:
    return sorted(
        {
            path
            for path, name in field_names(model)
            if CREDENTIAL_NAME.search(name) and path not in ALLOWED_PATHS
        }
    )


def test_no_field_name_holds_a_credential() -> None:
    assert credential_names(StrategyConfig) == []


def test_the_walk_reaches_every_section_and_the_allow_listed_field() -> None:
    paths = {path for path, _ in field_names(StrategyConfig)}
    assert set(StrategyConfig.model_fields) <= paths
    assert "notify.narrator.base_url" in paths
    assert "exits.global.mode" in paths  # an alias
    assert "exits.per_regime.Whipsaw.stop_rule" in paths  # an optional model
    assert "gates.stdev_fib_zone.zones.near" in paths  # a model inside a list alias
    assert "fills.costs.MES.commission" in paths
    assert len(paths) > 300


def test_the_walk_catches_credential_names() -> None:
    class Inner(BaseModel):
        session_token: str = ""

    class Section(BaseModel):
        webhook: str = ""
        inner: tuple[Inner, ...] = ()
        maybe: Inner | None = None

    class Root(BaseModel):
        api_key: str = ""
        section: Section = Section()
        base_url: str = ""  # allow-listed only at notify.narrator.base_url

    assert credential_names(Root) == [
        "api_key",
        "base_url",
        "section.inner.session_token",
        "section.maybe.session_token",
        "section.webhook",
    ]


def test_no_field_is_named_after_an_environment_credential() -> None:
    names = {name.lower() for _, name in field_names(StrategyConfig)}
    for variable in (*SECRET_VARIABLES, *ACCOUNT_ID_VARIABLES, SECRET_LIST_VARIABLE):
        assert variable.lower() not in names


@pytest.mark.parametrize(
    "url", ["https://user:pass@llm.invalid", "https://llm.invalid/v1?api_key=abc"]
)
def test_the_allow_listed_base_url_cannot_hold_a_credential(url: str) -> None:
    with pytest.raises(ValidationError):
        NarratorConfig(base_url=url)
