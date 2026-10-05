"""A small in-memory ProjectX Gateway for respx (Broker_Adapter tests).

:class:`FakeGateway` answers ``loginKey``, the account and contract searches,
``Order/place|modify|cancel|search|searchOpen`` and ``Position/searchOpen``
from its own state. Every id is fake. Tests drive fills with :meth:`fill`,
outages with :attr:`down`, and the bracket mode with :attr:`auto_oco`
(``False`` = Position Brackets: a bracket is rejected with ``errorCode 2``
and the rejected order still gets an id, as the docs say).

On an entry fill with brackets, the stop-loss and take-profit legs appear as
untagged working orders on the closing side, priced from the fill price and
the bracket ticks. A market order fills at once at :attr:`last_price`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
import respx

BASE: Final = "https://api.projectx.invalid"
TOKEN: Final = "fake-session-token-0000"
TICK: Final = 0.25


@dataclass
class FakeGateway:
    accounts: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": 101, "name": "FAKE-PRACTICE", "canTrade": True, "isVisible": True},
            {"id": 202, "name": "FAKE-COMBINE", "canTrade": True, "isVisible": True},
        ]
    )
    contracts: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": "CON.F.US.MES.Z26", "name": "MESZ6", "tickSize": 0.25, "activeContract": True},
            {"id": "CON.F.US.MNQ.Z26", "name": "MNQZ6", "tickSize": 0.25, "activeContract": True},
        ]
    )
    auto_oco: bool = True
    tag_field: bool = True
    down: bool = False
    last_price: float = 6000.0
    orders: dict[int, dict[str, Any]] = field(default_factory=dict)
    positions: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    next_id: int = 9000

    def install(self, router: respx.MockRouter) -> None:
        router.route(host="api.projectx.invalid").mock(side_effect=self)

    # ------------------------------------------------------------ driving

    def order_paths(self) -> list[str]:
        """Each order request's path, in order."""
        return [p for p, _ in self.calls if p in ("Order/place", "Order/modify", "Order/cancel")]

    def place_bodies(self) -> list[dict[str, Any]]:
        return [b for p, b in self.calls if p == "Order/place"]

    def working(self) -> list[dict[str, Any]]:
        return [o for o in self.orders.values() if o["status"] == 1]

    def fill(self, order_id: int, price: float | None = None, *, legs: bool = True) -> None:
        """Fill an open order; an entry with brackets gets its legs (Auto OCO)."""
        o = self.orders[order_id]
        px = price if price is not None else (o["limitPrice"] or o["stopPrice"] or self.last_price)
        o.update(status=2, filledPrice=px, fillVolume=o["size"])
        sign = 1 if o["side"] == 0 else -1
        self.positions[o["contractId"]] = self.positions.get(o["contractId"], 0) + sign * o["size"]
        close_side = 1 - o["side"]
        if legs and o.get("_sl"):
            self._new(
                o["contractId"], 4, close_side, o["size"], None, px - sign * o["_sl"] * TICK, None
            )
        if legs and o.get("_tp"):
            self._new(
                o["contractId"], 1, close_side, o["size"], px + sign * o["_tp"] * TICK, None, None
            )
        if o.get("_oco_of") is not None:  # a leg filled: cancel its sibling
            for other in self.working():
                if other.get("_oco_of") == o["_oco_of"]:
                    other["status"] = 3

    def _new(
        self,
        contract: str,
        type_: int,
        side: int,
        size: int,
        limit: float | None,
        stop: float | None,
        tag: str | None,
        status: int = 1,
    ) -> dict[str, Any]:
        self.next_id += 1
        o = {
            "id": self.next_id,
            "accountId": 0,
            "contractId": contract,
            "status": status,
            "type": type_,
            "side": side,
            "size": size,
            "limitPrice": limit,
            "stopPrice": stop,
            "fillVolume": 0,
            "filledPrice": None,
            "customTag": tag,
        }
        self.orders[self.next_id] = o
        return o

    def add_foreign(self, contract: str, *, side: int = 1, stop: float = 5900.0) -> int:
        """A working stop order the Project did not send (no customTag)."""
        return int(self._new(contract, 4, side, 1, None, stop, None)["id"])

    # ------------------------------------------------------------ the gateway

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/")
        body = json.loads(request.content) if request.content else {}
        self.calls.append((path, body))
        if path == "Auth/loginKey":
            return _ok(token=TOKEN)
        if self.down:
            return httpx.Response(503, json={"success": False, "errorCode": 7})
        match path:
            case "Account/search":
                return _ok(accounts=self.accounts)
            case "Contract/search":
                text = str(body.get("searchText", ""))
                return _ok(contracts=[c for c in self.contracts if f".{text}." in c["id"]])
            case "Order/place":
                return self._place(body)
            case "Order/modify":
                o = self.orders.get(int(body["orderId"]))
                if o is None or o["status"] != 1:
                    return _ok(success=False, errorCode=2)
                for key in ("size", "limitPrice", "stopPrice"):
                    if body.get(key) is not None:
                        o[key] = body[key]
                return _ok()
            case "Order/cancel":
                o = self.orders.get(int(body["orderId"]))
                if o is None or o["status"] != 1:
                    return _ok(success=False, errorCode=2)
                o["status"] = 3
                return _ok()
            case "Order/searchOpen":
                return _ok(orders=[self._public(o, tag=False) for o in self.working()])
            case "Order/search":
                return _ok(
                    orders=[self._public(o, tag=self.tag_field) for o in self.orders.values()]
                )
            case "Position/searchOpen":
                return _ok(
                    positions=[
                        {
                            "id": i,
                            "accountId": 0,
                            "contractId": c,
                            "type": 1 if q > 0 else 2,
                            "size": abs(q),
                            "averagePrice": self.last_price,
                        }
                        for i, (c, q) in enumerate(sorted(self.positions.items()))
                        if q
                    ]
                )
        return httpx.Response(404)

    def _place(self, body: dict[str, Any]) -> httpx.Response:
        brackets = (
            body.get("stopLossBracket") is not None or body.get("takeProfitBracket") is not None
        )
        tag = body.get("customTag")
        if tag is not None and any(o["customTag"] == tag for o in self.orders.values()):
            return _ok(success=False, errorCode=2, orderId=None)
        o = self._new(
            body["contractId"],
            int(body["type"]),
            int(body["side"]),
            int(body["size"]),
            body.get("limitPrice"),
            body.get("stopPrice"),
            tag,
        )
        if brackets and not self.auto_oco:
            o["status"] = 5
            return _ok(success=False, errorCode=2, orderId=o["id"])
        if brackets:
            o["_sl"] = body["stopLossBracket"]["ticks"]
            o["_tp"] = body["takeProfitBracket"]["ticks"]
        if o["type"] == 2:
            self.fill(o["id"], self.last_price, legs=False)
        return _ok(orderId=o["id"])

    @staticmethod
    def _public(o: dict[str, Any], *, tag: bool) -> dict[str, Any]:
        out = {k: v for k, v in o.items() if not k.startswith("_")}
        if not tag:
            out.pop("customTag", None)
        return out


def _ok(**fields: Any) -> httpx.Response:
    body: dict[str, Any] = {"success": True, "errorCode": 0, "errorMessage": None}
    body.update(fields)
    return httpx.Response(200, json=body)
