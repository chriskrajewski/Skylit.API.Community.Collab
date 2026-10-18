#!/bin/bash
# fse-ui-host-remove: the console off-switch on the host, run once from Systems
# Manager Run Command (no association) before the apply that sets
# enable_ui = false. Removes everything the bundle and the
# fse-runner-control document wrote. The drop-in and its scripts go together,
# so the next runner start uses Phase 1's run + configs/runner.yaml.
set -u

systemctl disable --now fse-ui.service fse-edge.service || true
systemctl disable --now fse-cache-sync.timer fse-cache-sync.service || true
systemctl disable --now fse-offload-recordings.timer fse-offload-recordings.service || true
rm -f \
  /etc/systemd/system/fse-offload-recordings.service \
  /etc/systemd/system/fse-offload-recordings.timer \
  /usr/local/bin/fse-offload-recordings.sh \
  /etc/systemd/system/fse-ui.service \
  /etc/systemd/system/fse-edge.service \
  /etc/systemd/system/fse-cache-sync.service \
  /etc/systemd/system/fse-cache-sync.timer \
  /usr/local/bin/fse-sync-cache.sh \
  /etc/systemd/system/fse-runner.service.d/20-console.conf \
  /usr/local/bin/fse-ui-fetch-env.sh \
  /usr/local/bin/fse-ui-start.sh \
  /usr/local/bin/fse-edge-start.sh \
  /usr/local/bin/fse-runner-mode.sh \
  /usr/local/bin/fse-console-start.sh \
  /usr/local/bin/fse-runner-state.sh \
  /etc/fse/ui.env \
  /var/lib/fse-host/runner-held \
  /var/lib/fse-host/runner-state.json
# The plugin-orders control file (fse-runner-mode.sh); Phase 1's fse-start.sh never mounts it.
rm -rf /run/fse-control
systemctl daemon-reload
docker rm -f fse-ui fse-edge || true
echo "fse console removed from this host"
