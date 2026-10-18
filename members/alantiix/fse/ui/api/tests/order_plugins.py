"""Test-only plugins for Live → Order mode: the Template plugin with ``broker_orders``.

They live inside the Project folder (``ui/api/tests``), so the plugin loader accepts
them as ``tests.order_plugins:<Class>``.
"""

from __future__ import annotations

import dataclasses

from fse.live.plugins.api import BarNeed, DataNeeds
from fse.live.plugins.template import TemplatePlugin


class BrokerTemplate(TemplatePlugin):
    """A second broker plugin (any id): the template that may send broker orders."""

    def needs(self) -> DataNeeds:
        return dataclasses.replace(super().needs(), broker_orders=True)


class AlphaPlugin(BrokerTemplate):
    """The broker plugin of the console's test runner config (see ``runner_fixture``)."""

    label = "Alpha"


class SelfBrokerTemplate(TemplatePlugin):
    """Declares broker orders but brokers itself."""

    def needs(self) -> DataNeeds:
        return dataclasses.replace(super().needs(), brokerage="self", broker_orders=True)


class GoldBrokerTemplate(TemplatePlugin):
    """Declares broker orders on MGC, an instrument the broker path ignores."""

    def needs(self) -> DataNeeds:
        return dataclasses.replace(super().needs(), bars=(BarNeed("MGC"),), broker_orders=True)
