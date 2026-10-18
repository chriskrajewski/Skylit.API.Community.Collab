"""CloudFront's response headers policy must send the same CSP as the app."""

from __future__ import annotations

import re
from pathlib import Path

from fse_ui.app import CSP

UI_EDGE = Path(__file__).resolve().parents[3] / "infra" / "modules" / "ui_edge"
POLICY = re.compile(r'^\s*content_security_policy\s*=\s*"((?:[^"\\]|\\.)*)"', re.M)


def test_cloudfront_csp_equals_the_app_csp() -> None:
    found = [
        (path.name, value)
        for path in sorted(UI_EDGE.glob("*.tf"))
        for value in POLICY.findall(path.read_text())
    ]
    assert len(found) == 1, f"expected one content_security_policy string in {UI_EDGE}: {found}"
    assert found[0][1] == CSP
