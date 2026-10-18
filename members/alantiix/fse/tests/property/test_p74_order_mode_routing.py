"""Property 74: Order_Mode resolution and routing.

*For any* combination of configured Order_Mode, ``COMBINE_ACCOUNT_ID``,
``PRACTICE_ACCOUNT_ID``, resolved account list and live-orders flag, the
selected mode is Combine only when all three Combine_Opt_In conditions hold
and Practice only when the practice id is set, resolved and different from
the combine id, otherwise Paper with every failed condition named in the
first Finding_Card; and every order sent in Combine or Practice mode carries
that mode's account id.

Each example draws the inputs, checks :func:`resolve_order_mode` against an
independent statement of the rules, and, for a broker mode, sends one order
through a real :class:`~fse.projectx.broker.BrokerAdapter` bound with
``for_mode`` to a respx-mocked ProjectX, checking the ``accountId`` sent.

**Validates: Requirements 24.1, 24.3, 24.4, 24.5, 24.6**
"""

from __future__ import annotations

import asyncio
import json

import httpx
import respx
from hypothesis import event, given
from hypothesis import strategies as st

from fse.live.order_router import BROKER_MODES, COMBINE, PAPER, PRACTICE, resolve_order_mode
from fse.logio import Redactor
from fse.projectx.broker import BrokerAdapter, BrokerFailure, OrderRequest
from fse.projectx.models import AccountRef
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.secrets.env import EnvView
from tests.fakes.clock import FakeClock

BASE = "https://api.projectx.invalid"
CREDS = {USERNAME_VARIABLE: "fake-projectx-user-0000", API_KEY_VARIABLE: "fake-projectx-key-0000"}
IDS = ("101", "202", "303")


def _send_one(env: EnvView, mode: str) -> list[int]:
    """Send one market order in ``mode``; the accountId of each order request."""
    seen: list[int] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("loginKey"):
            return httpx.Response(
                200, json={"token": "fake-token", "success": True, "errorCode": 0}
            )
        seen.append(int(json.loads(request.content)["accountId"]))
        return httpx.Response(200, json={"orderId": 7, "success": True, "errorCode": 0})

    async def main() -> None:
        clock = FakeClock(0)
        async with httpx.AsyncClient() as http:
            px = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
            bound = BrokerAdapter(px, env, clock).for_mode(mode)
            request = OrderRequest("fse-0a1b2c3d-1", "CON.F.US.MES.Z26", "sell", 1, "market")
            await clock.run(bound.place(request))

    with respx.mock(assert_all_mocked=True) as router:
        router.route(host="api.projectx.invalid").mock(side_effect=gateway)
        asyncio.run(main())
    return seen


@given(
    requested=st.sampled_from([PAPER, PRACTICE, COMBINE]),
    combine=st.none() | st.sampled_from(IDS),
    practice=st.none() | st.sampled_from(IDS),
    resolved=st.none() | st.sets(st.sampled_from([101, 202])),
    flag=st.booleans(),
)
def test_order_mode_resolution_and_routing(
    requested: str,
    combine: str | None,
    practice: str | None,
    resolved: set[int] | None,
    flag: bool,
) -> None:
    values = dict(CREDS)
    if combine is not None:
        values["COMBINE_ACCOUNT_ID"] = combine
    if practice is not None:
        values["PRACTICE_ACCOUNT_ID"] = practice
    env = EnvView(values, {})
    accounts: list[AccountRef] | BrokerFailure = (
        BrokerFailure("/api/Account/search", "HTTP 503")
        if resolved is None
        else [AccountRef(i, f"FAKE-{i}", True) for i in sorted(resolved)]
    )
    ids = set() if resolved is None else {str(i) for i in resolved}
    decision = resolve_order_mode(requested, env, accounts, live_orders=flag)

    combine_ok = combine is not None and combine in ids and flag
    practice_ok = practice is not None and practice in ids and practice != combine
    if requested == COMBINE:
        want = COMBINE if combine_ok else PAPER
        failed = [combine is None or combine not in ids, not flag]
    elif requested == PRACTICE:
        want = PRACTICE if practice_ok else PAPER
        failed = [
            practice is None or practice not in ids,
            practice is not None and practice == combine,
        ]
    else:
        want, failed = PAPER, []
    event(f"{requested} -> {decision.mode}")
    assert decision.mode == want
    assert (decision.mode == requested) == (requested == PAPER or not any(failed))
    if decision.mode != requested:  # every failed condition is named in the first card
        assert decision.note is not None
        assert len(decision.failures) == sum(failed)
        for text in decision.failures:
            assert not any(i in text for i in IDS)  # names variables, never account ids
    else:
        assert decision.failures == ()
        assert decision.note is None

    if decision.mode in BROKER_MODES:
        variable = "COMBINE_ACCOUNT_ID" if decision.mode == COMBINE else "PRACTICE_ACCOUNT_ID"
        assert _send_one(env, decision.mode) == [int(values[variable])]
