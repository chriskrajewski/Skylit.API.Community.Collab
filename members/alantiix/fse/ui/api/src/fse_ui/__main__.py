"""``fse-ui``: run the console API with uvicorn on ``FSE_UI_BIND_HOST:FSE_UI_PORT``."""

from __future__ import annotations

import sys

import uvicorn

from fse_ui.settings import Settings, SettingsError


def main() -> int:
    try:
        settings = Settings.from_env()
        settings.check()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from fse_ui.app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ws="websockets-sansio",
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
