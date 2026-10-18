#!/usr/bin/env bash
# Run the fse history pulls on a dedicated machine: a Mac (launchd) or Linux (systemd).
#
# Run every command from this Mac, in the project folder:
#
#   scripts/fse-remote.sh push   user@host   # copy the code, .env and the Data_Cache to the machine
#   scripts/fse-remote.sh setup  user@host   # install uv, Python 3.14, deps and the background job
#   scripts/fse-remote.sh start  user@host   # start the pull queue in the background
#   scripts/fse-remote.sh status user@host   # running or not, the last log lines, cache size
#   scripts/fse-remote.sh logs   user@host   # follow the log (Ctrl-C to stop watching)
#   scripts/fse-remote.sh stop   user@host   # stop the pulls (rerun `start` to resume)
#   scripts/fse-remote.sh fetch  user@host   # copy the Data_Cache back to this Mac
#
# Options for `start` (environment variables):
#   PULL1=0       skip pull 1 (2025-10-06 to 2026-10-02, 5 expirations); default 1
#   PULL2=1       also pull 2024-01-02 to 2025-10-05 at 5-minute samples; default 0
#   PULL_0DTE=1   also pull 0DTE maps (nearest expiration only) for 2025-10-06 to 2026-10-02;
#                 stored as a separate Heatmap_View next to the 5-expiration maps; default 0
#   Example: PULL1=0 PULL_0DTE=1 scripts/fse-remote.sh start user@host
#
# The pulls resume: a rerun serves finished Cache_Windows from the cache and fetches the rest.
# After a crash or a network failure the queue waits 5 minutes and tries again. It gives up
# on exit 2 (invalid input), 3 (API key rejected) or 4 (cache write error), which need a person.
# On a Mac, `setup` and `start` ask for the remote sudo password (launchd system job).
set -euo pipefail

cmd="${1:-}"
host="${2:-}"
if [[ -z "$cmd" || -z "$host" ]]; then
  sed -n '2,24p' "$0"
  exit 2
fi

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
remote_root="fse"                                    # under the remote home directory
remote_dir="$remote_root/skylit-futures-strategy-engine"
local_cache="$HOME/.skylit-fse/cache"
service="fse-pull"                                   # systemd unit (Linux)
label="com.skylit.fse-pull"                          # launchd label (macOS)
plist="/Library/LaunchDaemons/$label.plist"

# macOS ships openrsync, which lacks --info and --partial; GNU rsync (brew) has both.
if rsync --version 2>&1 | grep -q openrsync; then
  rsync_opts=(-az --progress)
else
  rsync_opts=(-az --partial --info=progress2)
fi

remote() { ssh "$host" "$@"; }
remote_os() { remote uname -s; }
remote_running() { remote "pgrep -f 'run-pulls.sh|fse pull' >/dev/null"; }

case "$cmd" in
push)
  if pgrep -f "fse pull" >/dev/null; then
    echo "error: an fse pull is running on this Mac. Stop it first, so the cache is not copied mid-write." >&2
    exit 1
  fi
  if remote_running; then
    echo "error: a pull is running on $host. Run '$0 stop $host' first." >&2
    exit 1
  fi
  remote "mkdir -p $remote_dir .skylit-fse/cache && chmod 700 .skylit-fse"
  echo "Copying the project (code, calendars, configs and .env) ..."
  rsync -az --delete \
    --exclude .venv --exclude .mypy_cache --exclude .pytest_cache --exclude .ruff_cache \
    --exclude .hypothesis --exclude __pycache__ --exclude .agents --exclude .DS_Store \
    "$project_dir/" "$host:$remote_dir/"
  remote "chmod 600 $remote_dir/.env"
  echo "Copying the Data_Cache ($(du -sh "$local_cache" | cut -f1)) ..."
  rsync "${rsync_opts[@]}" "$local_cache/" "$host:.skylit-fse/cache/"
  echo "Done. Next: $0 setup $host"
  ;;

setup)
  os="$(remote_os)"
  # Unquoted EOF on purpose: $remote_dir expands here, \$-escaped names expand on the server.
  # shellcheck disable=SC2087
  ssh "$host" "bash -s" <<EOF
set -euo pipefail
export PATH="\$HOME/.local/bin:\$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="\$HOME/.local/bin:\$PATH"
cd "$remote_dir"
uv python install 3.14
uv venv --python 3.14 --allow-existing .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/fse --help >/dev/null && echo "fse installed."

cat > "\$HOME/$remote_root/run-pulls.sh" <<'RUN'
#!/usr/bin/env bash
# The pull queue. Each step is idempotent, so a retry serves finished steps from the cache.
set -uo pipefail
cd "\$HOME/$remote_dir"
common=(--instruments MES,MNQ --dark-pool --size-limit-gb 200)
run() {
  echo "\$(date '+%Y-%m-%dT%H:%M:%S%z') start: fse pull \$*"
  .venv/bin/fse pull "\$@"
  rc=\$?
  echo "\$(date '+%Y-%m-%dT%H:%M:%S%z') exit \$rc"
  return \$rc
}
queue() {
  # Pull 1: recent year, full resolution. The second pass picks up bars and incomplete windows.
  if [[ "\${PULL1:-1}" == 1 ]]; then
    run --start 2025-10-06 --end 2026-10-02 "\${common[@]}" --label-sample-minutes 15 || return \$?
    run --start 2025-10-06 --end 2026-10-02 "\${common[@]}" --label-sample-minutes 15 || return \$?
  fi
  # 0DTE maps: the nearest expiration only. A different Heatmap_View (view_id), so it is stored
  # beside the 5-expiration maps and never overwrites them. SPXW, not SPX: SPX's nearest
  # expiration is the monthly (probe 2026-10-01: SPX 2026-10-16, SPXW 2026-10-01). NDX is left
  # out (sparse data). Bars, VIX and dark pool are already cached and served from the cache.
  if [[ "\${PULL_0DTE:-0}" == 1 ]]; then
    zdte=(--symbols SPXW,SPY,QQQ,NDXP --max-expirations 1)
    run --start 2025-10-06 --end 2026-10-02 "\${common[@]}" "\${zdte[@]}" || return \$?
    run --start 2025-10-06 --end 2026-10-02 "\${common[@]}" "\${zdte[@]}" || return \$?
  fi
  # Pull 2 (optional): 2024-01-02 onward, where Atlas has bars, at 5-minute samples.
  if [[ "\${PULL2:-0}" == 1 ]]; then
    run --start 2024-01-02 --end 2025-10-05 "\${common[@]}" --sample-interval-min 5 || return \$?
    run --start 2024-01-02 --end 2025-10-05 "\${common[@]}" --sample-interval-min 5 || return \$?
  fi
}
while true; do
  queue
  rc=\$?
  case \$rc in
    0) echo "all pulls finished"; exit 0 ;;
    2|3|4) echo "stopped on exit \$rc: needs a person (2 input, 3 API key, 4 cache write)"; exit 0 ;;
    *) echo "retrying in 5 minutes after exit \$rc"; sleep 300 ;;
  esac
done
RUN
chmod 700 "\$HOME/$remote_root/run-pulls.sh"

if [[ "$os" == Linux ]]; then
  mkdir -p "\$HOME/.config/systemd/user"
  cat > "\$HOME/.config/systemd/user/$service.service" <<UNIT
[Unit]
Description=fse Skylit history pulls
After=network-online.target

[Service]
Type=simple
Environment=PULL1=1 PULL2=0 PULL_0DTE=0
ExecStart=%h/$remote_root/run-pulls.sh
Restart=on-failure
RestartSec=300
StandardOutput=append:%h/$remote_root/pull.log
StandardError=append:%h/$remote_root/pull.log

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
fi
echo "Setup done."
EOF

  if [[ "$os" == Darwin ]]; then
    # A launchd system job runs as your user without a login session and restarts after a
    # reboot. caffeinate keeps the Mac from idle-sleeping while the pull runs.
    # shellcheck disable=SC2016  # $USER, $HOME and $(id -u) expand on the remote Mac
    ssh -t "$host" 'set -e
      cat > /tmp/fse-pull.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>'"$label"'</string>
  <key>UserName</key><string>$USER</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$HOME/'"$remote_root"'/run-pulls.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PULL1</key><string>1</string>
    <key>PULL2</key><string>0</string>
    <key>PULL_0DTE</key><string>0</string>
  </dict>
  <key>WorkingDirectory</key><string>$HOME/'"$remote_root"'</string>
  <key>StandardOutPath</key><string>$HOME/'"$remote_root"'/pull.log</string>
  <key>StandardErrorPath</key><string>$HOME/'"$remote_root"'/pull.log</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>300</integer>
</dict>
</plist>
PLIST
      plutil -lint /tmp/fse-pull.plist
      sudo install -m 644 -o root -g wheel /tmp/fse-pull.plist '"$plist"'
      # Disabled until `start`, so it does not run at the next boot before you ask for it.
      sudo launchctl disable system/'"$label"'
      rm /tmp/fse-pull.plist
      # Stay awake on power, and power back on after an outage.
      sudo pmset -c sleep 0 disksleep 0
      sudo pmset -a autorestart 1
      echo "launchd job installed."'
  elif [[ "$os" == Linux ]]; then
    # Keep the service running after you log out. Separate ssh -t so sudo can ask for a password.
    # shellcheck disable=SC2016  # $USER expands on the server
    ssh -t "$host" 'loginctl show-user "$USER" -p Linger | grep -q yes || sudo loginctl enable-linger "$USER"'
  else
    echo "error: unsupported remote OS '$os' (macOS or Linux only)" >&2
    exit 1
  fi
  echo "Next: $0 start $host   (for example PULL1=0 PULL_0DTE=1 $0 start $host)"
  ;;

start)
  pull1="${PULL1:-1}" pull2="${PULL2:-0}" pull0="${PULL_0DTE:-0}"
  for v in "$pull1" "$pull2" "$pull0"; do
    [[ "$v" == 0 || "$v" == 1 ]] || { echo "error: PULL1, PULL2 and PULL_0DTE must be 0 or 1" >&2; exit 2; }
  done
  flags="PULL1=$pull1 PULL2=$pull2 PULL_0DTE=$pull0"
  if [[ "$(remote_os)" == Darwin ]]; then
    ssh -t "$host" "set -e
      sudo plutil -replace EnvironmentVariables.PULL1 -string $pull1 $plist
      sudo plutil -replace EnvironmentVariables.PULL2 -string $pull2 $plist
      sudo plutil -replace EnvironmentVariables.PULL_0DTE -string $pull0 $plist
      sudo launchctl bootout system/$label 2>/dev/null || true
      sudo launchctl enable system/$label
      sudo launchctl bootstrap system $plist
      sudo launchctl kickstart system/$label
      echo 'Started ($flags).'"
  else
    remote "sed -i 's/^Environment=.*/Environment=$flags/' ~/.config/systemd/user/$service.service \
      && systemctl --user daemon-reload && systemctl --user restart $service \
      && echo 'Started ($flags).'"
  fi
  ;;

status)
  remote "if pgrep -f 'run-pulls.sh|fse pull' >/dev/null; then echo 'running'; else echo 'not running'; fi; \
    echo; tail -n 15 $remote_root/pull.log 2>/dev/null; echo; du -sh .skylit-fse/cache"
  ;;

logs)
  ssh -t "$host" "tail -n 50 -f $remote_root/pull.log"
  ;;

stop)
  if [[ "$(remote_os)" == Darwin ]]; then
    ssh -t "$host" "sudo launchctl bootout system/$label 2>/dev/null; sudo launchctl disable system/$label; \
      pkill -f 'fse pull' 2>/dev/null; echo Stopped."
  else
    remote "systemctl --user stop $service && echo Stopped."
  fi
  ;;

fetch)
  if remote_running; then
    echo "error: a pull is still running on $host. Run '$0 stop $host' first." >&2
    exit 1
  fi
  mkdir -p "$local_cache"
  rsync "${rsync_opts[@]}" "$host:.skylit-fse/cache/" "$local_cache/"
  echo "Data_Cache copied to $local_cache"
  ;;

*)
  echo "unknown command: $cmd" >&2
  sed -n '2,24p' "$0"
  exit 2
  ;;
esac
