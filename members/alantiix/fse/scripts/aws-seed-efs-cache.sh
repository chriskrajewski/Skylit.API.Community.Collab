#!/usr/bin/env bash
# Seed or refresh the Fargate EFS Data_Cache from S3 (one-shot or catch-up).
#
# After the first `cache_efs` Terraform apply (or after a big Mac→S3 upload), run:
#
#   scripts/aws-seed-efs-cache.sh --yes
#   scripts/aws-seed-efs-cache.sh --yes --force   # clear a stale pull.lock first
#   scripts/aws-seed-efs-cache.sh unlock --yes    # delete locks/pull.lock and stop
#
# It starts an `fse-batch` **Fargate On-Demand** task (not Spot — a full ~20 GB /
# 100k+ file sync can take 30–60+ minutes and Spot reclaim leaves a half-done
# tree plus a held lock). The entrypoint runs `seed-efs-cache` with
# `FSE_SEED_LOCK_EXTERNAL=1`: this script owns `locks/pull.lock` and releases it
# on exit/Ctrl-C. The task only does `aws s3 sync s3://$DATA_BUCKET/cache/ →
# ~/.skylit-fse/cache/` (resume-friendly: already-synced objects are skipped).
# EventBridge runs the same entrypoint every 6 hours on On-Demand under the
# in-task lock. A fresh lock is left alone; one whose started= time is at least
# FSE_LOCK_TTL_S seconds old (default 7200) is replaced. A missing or unreadable
# started= line is not replaced. Clear a stuck lock with `unlock --yes`,
# `--yes --force`, or `scripts/aws-batch.sh unlock --yes` (only when no pull
# and no seed is live).
#
# Environment (default in brackets; the last three come from `terraform output`):
#   AWS_PROFILE [default]  AWS_REGION [us-east-1]  FSE_CLUSTER [fse]  FSE_TASK_FAMILY [fse-batch]
#   FSE_SUBNETS         comma-separated subnet ids [output subnet_ids]
#   FSE_SECURITY_GROUP  batch security group id [output batch_security_group_id]
#   DATA_BUCKET         data bucket name [output data_bucket]
#   FSE_SEED_POLL_S     seconds between describe-tasks while waiting [30]
set -euo pipefail

force=0
yes=0
unlock=0
if [[ "${1:-}" == unlock ]]; then
  unlock=1
  shift
fi
for arg in ${1+"$@"}; do
  case "$arg" in
    --yes) yes=1 ;;
    --force) force=1 ;;
    *)
      sed -n -e '/^set -euo/q' -e '2,$p' "$0"
      exit 2
      ;;
  esac
done
if [[ "$yes" != 1 ]]; then
  if [[ "$unlock" == 1 ]]; then
    echo "Run '$0 unlock --yes' to delete locks/pull.lock." >&2
    echo "Only when no pull and no seed-efs-cache task is running." >&2
  else
    sed -n -e '/^set -euo/q' -e '2,$p' "$0"
  fi
  exit 2
fi

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
FSE_CLUSTER="${FSE_CLUSTER:-fse}"
FSE_TASK_FAMILY="${FSE_TASK_FAMILY:-fse-batch}"
container="fse"
log_group="/fse/batch"
lock_key="locks/pull.lock"
log_drain_s="${FSE_LOG_DRAIN_S:-5}"
poll_s="${FSE_SEED_POLL_S:-30}"

tf_output() {
  local value
  if ! value="$(terraform -chdir="$project_dir/infra" output -json "$1" 2>/dev/null |
    python3 -c 'import json, sys
v = json.load(sys.stdin)
print(",".join(v) if isinstance(v, list) else v)')" || [[ -z "$value" ]]; then
    echo "error: set $2, or run 'terraform init' in infra/ so 'terraform output $1' works" >&2
    exit 2
  fi
  printf '%s\n' "$value"
}

if [[ -z "${DATA_BUCKET:-}" ]]; then DATA_BUCKET="$(tf_output data_bucket DATA_BUCKET)"; fi

if [[ "$unlock" == 1 ]]; then
  aws s3api delete-object --bucket "$DATA_BUCKET" --key "locks/pull.lock" >/dev/null
  echo "Deleted s3://$DATA_BUCKET/locks/pull.lock"
  exit 0
fi

if [[ -z "${FSE_SUBNETS:-}" ]]; then FSE_SUBNETS="$(tf_output subnet_ids FSE_SUBNETS)"; fi
if [[ -z "${FSE_SECURITY_GROUP:-}" ]]; then
  FSE_SECURITY_GROUP="$(tf_output batch_security_group_id FSE_SECURITY_GROUP)"
fi

tmp_dir="$(mktemp -d)"
task_arn=""
task_stopped=0
lock_created=0
tailer=""

# shellcheck disable=SC2329,SC2317  # runs from the EXIT trap
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$tailer" ]]; then kill "$tailer" 2>/dev/null || true; fi
  if [[ -n "$task_arn" && "$task_stopped" == 0 ]]; then
    echo "Stopping task $task_arn ..." >&2
    aws ecs stop-task --cluster "$FSE_CLUSTER" --task "$task_arn" \
      --reason "aws-seed-efs-cache.sh interrupted" >/dev/null ||
      echo "warning: stop-task failed" >&2
  fi
  if [[ "$lock_created" == 1 ]]; then
    aws s3api delete-object --bucket "$DATA_BUCKET" --key "$lock_key" >/dev/null ||
      echo "warning: could not delete s3://$DATA_BUCKET/$lock_key; run 'scripts/aws-batch.sh unlock --yes' or re-run with --force" >&2
  fi
  rm -rf "$tmp_dir"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

show_lock() {
  if aws s3api get-object --bucket "$DATA_BUCKET" --key "$lock_key" "$tmp_dir/holder" \
    >/dev/null 2>&1; then
    echo "pull lock s3://$DATA_BUCKET/$lock_key is held by:" >&2
    sed 's/^/  /' "$tmp_dir/holder" >&2
    return 0
  fi
  return 1
}

task_field() {
  python3 -c 'import json, sys
task = (json.load(open(sys.argv[1])).get("tasks") or [{}])[0]
field = sys.argv[2]
if field == "status":
    print(task.get("lastStatus", ""))
elif field == "reason":
    print(task.get("stoppedReason", "unknown"))
else:
    code = (task.get("containers") or [{}])[0].get("exitCode")
    print("" if code is None else code)' "$tmp_dir/task.json" "$1"
}

describe_task() {
  aws ecs describe-tasks --cluster "$FSE_CLUSTER" --tasks "$task_arn" --output json \
    >"$tmp_dir/task.json"
}

# Fail before RunTask if the active revision has no EFS volume/mount (common when
# Terraform has not been applied yet, or ignore_changes left mountPoints off).
aws ecs describe-task-definition --task-definition "$FSE_TASK_FAMILY" --output json \
  >"$tmp_dir/td.json"
python3 -c '
import json, sys
td = json.load(open(sys.argv[1]))["taskDefinition"]
vols = [v for v in (td.get("volumes") or []) if v.get("name") == "fse-cache"]
efs = (vols[0].get("efsVolumeConfiguration") or {}) if vols else {}
ctr = next((c for c in td["containerDefinitions"] if c.get("name") == "fse"), None)
mounts = [
    m for m in ((ctr or {}).get("mountPoints") or [])
    if m.get("sourceVolume") == "fse-cache"
    and m.get("containerPath") == "/home/fse/.skylit-fse/cache"
]
env = {e["name"]: e["value"] for e in ((ctr or {}).get("environment") or []) if "name" in e}
ok = bool(vols and efs.get("fileSystemId") and mounts and env.get("FSE_CACHE_BACKEND") == "efs")
if not ok:
    print(
        "error: fse-batch has no usable EFS Data_Cache mount "
        "(need volume fse-cache + mountPoints + FSE_CACHE_BACKEND=efs).",
        file=sys.stderr,
    )
    print(
        "Apply the main stack (run the fse-infra workflow from your release branch with apply=true, "
        "or terraform apply), then re-run fse-deploy so mountPoints are registered.",
        file=sys.stderr,
    )
    sys.exit(2)
print(
    "task definition %s:%s has EFS mount at /home/fse/.skylit-fse/cache"
    % (td.get("family"), td.get("revision")),
    file=sys.stderr,
)
' "$tmp_dir/td.json"

if [[ "$force" == 1 ]]; then
  if show_lock; then
    echo "Deleting stale pull lock (--force) ..." >&2
    aws s3api delete-object --bucket "$DATA_BUCKET" --key "$lock_key" >/dev/null
  else
    echo "No pull lock at s3://$DATA_BUCKET/$lock_key; continuing." >&2
  fi
fi

printf 'user=%s\nhost=%s\nstarted=%s\njob=aws-seed-efs-cache\n' \
  "${USER:-unknown}" "$(hostname 2>/dev/null || echo unknown)" \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$tmp_dir/lock"
if aws s3api put-object --bucket "$DATA_BUCKET" --key "$lock_key" --if-none-match '*' \
  --body "$tmp_dir/lock" >/dev/null 2>"$tmp_dir/lock.err"; then
  lock_created=1
elif grep -Eq 'PreconditionFailed|ConditionalRequestConflict|412' "$tmp_dir/lock.err"; then
  show_lock || echo "pull lock s3://$DATA_BUCKET/$lock_key is held" >&2
  echo "Another pull or seed is running. If it is not, re-run with --force or 'scripts/aws-batch.sh unlock --yes'." >&2
  exit 75
else
  cat "$tmp_dir/lock.err" >&2
  echo "error: could not write the pull lock" >&2
  exit 1
fi

overrides="$(python3 -c 'import json, sys
print(json.dumps({
  "containerOverrides": [{
    "name": sys.argv[1],
    "command": ["seed-efs-cache"],
    "environment": [{"name": "FSE_SEED_LOCK_EXTERNAL", "value": "1"}],
  }]
}))' "$container")"

echo "Seeding EFS cache from s3://$DATA_BUCKET/cache/ via Fargate On-Demand ..." >&2
aws ecs run-task --cluster "$FSE_CLUSTER" --task-definition "$FSE_TASK_FAMILY" --count 1 \
  --capacity-provider-strategy capacityProvider=FARGATE,weight=1 \
  --network-configuration \
  "awsvpcConfiguration={subnets=[$FSE_SUBNETS],securityGroups=[$FSE_SECURITY_GROUP],assignPublicIp=ENABLED}" \
  --started-by aws-seed-efs-cache.sh --overrides "$overrides" --output json >"$tmp_dir/run.json"

task_arn="$(python3 -c 'import json, sys
out = json.load(open(sys.argv[1]))
for f in out.get("failures") or []:
    print("run-task failure: %s %s" % (f.get("reason", ""), f.get("detail", "")), file=sys.stderr)
tasks = out.get("tasks") or []
print(tasks[0]["taskArn"] if tasks else "")' "$tmp_dir/run.json")"
if [[ -z "$task_arn" ]]; then
  echo "error: run-task started no task" >&2
  exit 1
fi
task_id="${task_arn##*/}"
echo "Started seed-efs-cache as task $task_id; waiting for Fargate On-Demand capacity ..." >&2

# Poll describe-tasks (do not use `aws ecs wait`: default max attempts ~40 × 15s
# false-fails a 30–60+ min sync while the task is still RUNNING).
log_started=0
while true; do
  describe_task
  status="$(task_field status)"
  if [[ "$status" == RUNNING && "$log_started" == 0 ]]; then
    aws logs tail "$log_group" --follow --format short \
      --log-stream-name-prefix "batch/$container/$task_id" &
    tailer=$!
    log_started=1
  fi
  if [[ "$status" == STOPPED ]]; then
    break
  fi
  sleep "$poll_s"
done
task_stopped=1

if [[ -n "$tailer" ]]; then
  steps=$((log_drain_s * 5))
  while ((steps > 0)) && kill -0 "$tailer" 2>/dev/null; do
    sleep 0.2
    steps=$((steps - 1))
  done
  kill "$tailer" 2>/dev/null || true
  wait "$tailer" 2>/dev/null || true
  tailer=""
fi

describe_task
code="$(task_field exit_code)"
if [[ -z "$code" ]]; then
  echo "error: task $task_id stopped without an exit code: $(task_field reason)" >&2
  exit 1
fi
echo "Task $task_id exited $code" >&2
exit "$code"
