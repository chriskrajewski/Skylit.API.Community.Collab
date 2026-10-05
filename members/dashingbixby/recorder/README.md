# Recorder

- Discord username: dashingbixby
- Project identifier: recorder
- Kind: agent

## What it does

Records raw Skylit Heatseeker stream events to disk as they arrive, with receive timestamps. After one trading day, `trial_report.py` reads that recording and writes what the day cost and contained, for a keep / reduce / stop decision.

- [recorder.py](recorder.py) writes `runtime/recordings/<trading_day>/heatseeker_stream.jsonl` (gzipped at stop) and `run.json`.
- [trial_report.py](trial_report.py) writes `report.json` and `report.md` next to that recording.

## Skylit surface

- Access: REST
- Products: Heatseeker

Docs: [REST API](https://docs.skylit.ai/api-reference/introduction).

## How to run

These files are the `agentic_trading.logging.recorder` and `agentic_trading.logging.trial_report` modules. Times are configured in ET.

```bash
PYTHONPATH=src python3 -m agentic_trading.logging.recorder --start-at 09:25 --stop-at 16:00
PYTHONPATH=src python3 -m agentic_trading.logging.trial_report 2026-10-05
```

## Environment variables

- `SKYLIT_API_KEY` — Skylit API key. Set it in the environment. Leave it out of git.
- `SKYLIT_API_KEY_KEYCHAIN_ITEM` — optional macOS Keychain item, used when `SKYLIT_API_KEY` is unset. Default: `agentic-trading.skylit.api_key`.
