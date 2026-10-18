"""A plugin that may send broker orders: ``TemplatePlugin`` with ``broker_orders=True``.

The shipped plugins are paper only, so the order-gate tests (``fse run`` with Practice or
Combine requested) load this one by its dotted path. It trades exactly like the template.
"""

from __future__ import annotations

from dataclasses import replace

from fse.live.plugins import DataNeeds
from fse.live.plugins.template import TemplatePlugin

__all__ = ["PracticeTemplatePlugin"]


class PracticeTemplatePlugin(TemplatePlugin):
    """The template, declaring that it can send broker orders."""

    label = "Practice template"

    def needs(self) -> DataNeeds:
        return replace(super().needs(), broker_orders=True)
