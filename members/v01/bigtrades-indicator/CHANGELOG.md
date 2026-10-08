# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [0.1.0] - 2026-10-08

### Added
- `run(..., extra_sinks=[...])` to feed big trades straight into your own bot.
- Sweep aggregator: consecutive same-side prints within `join_ms`, closed on side flip, gap or `quiet_ms`.
- `final` (default) and `first_qualify` publish modes.
- ProjectX REST login (`loginKey` -> JWT, auto refresh via `validate`) and front-month contract resolve.
- Minimal SignalR client for the market hub (`SubscribeContractTrades` / `GatewayTrade`).
- Console and Discord webhook sinks, pluggable `Sink` protocol.
- Optional raw tape recorder and offline `replay` command.
