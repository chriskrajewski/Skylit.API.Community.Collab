# research/

Put your own plugins here. This copy ships none.

1. Create `research/<name>/plugin.py` with a class implementing the plugin API
   (see `src/fse/live/plugins/template.py` for a minimal example).
2. List it in the runner YAML:
   ```yaml
   plugins:
     - id: <name>
       module: research.<name>.plugin:ClassName
       enabled: true
       fills: {trade_through_ticks: 1, slippage_ticks: 0, stop_fill: at_stop, commission_rt: {MNQ: "1.24"}}
       risk: {max_contracts: 1}
   ```
3. To ship it in the Docker image, add `!research/<name>/**` to `.dockerignore`.
   To run a research CLI of yours as a batch job, add its module to `BATCH_PY_MODULES` in
   `docker/entrypoint.sh` and to `PROGRAMS` in `ui/api/src/fse_ui/catalog.py`.

`fse` discovers `research/<name>/plugin.py` automatically for the console listing.
