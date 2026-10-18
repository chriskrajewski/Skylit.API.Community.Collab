#!/usr/bin/env bash
# Run one fse batch command on Fargate Spot (ARM64, 8 vCPU, 32 GB) and wait for it.
#
# Run every command from this Mac, in the project folder:
#
#   scripts/aws-batch.sh pull --start 2026-01-02 --end 2026-03-31 ...   # fse pull in the cloud
#   scripts/aws-batch.sh backtest   <fse backtest args...>
#   scripts/aws-batch.sh experiment <fse experiment args...>
#   scripts/aws-batch.sh report     <fse report args...>
#   scripts/aws-batch.sh unlock --yes   # delete a stale pull lock (only when no pull runs)
#
# The task copies s3://$DATA_BUCKET/cache/ down, runs `fse <command> <args...>`, and uploads
# the cache and runs back. Its log streams here, and this script exits with fse's exit code.
# A pull that exits 2 (invalid input), 3 (API key rejected) or 4 (cache write error) needs a
# person. One pull at a time: the lock is s3://$DATA_BUCKET/locks/pull.lock, and a held lock
# exits 75. Ctrl-C stops the task (a rerun of the pull resumes) and releases the lock.
#
# Environment (default in brackets; the last three come from `terraform output` in infra/):
#   AWS_PROFILE [default]  AWS_REGION [us-east-1]  FSE_CLUSTER [fse]  FSE_TASK_FAMILY [fse-batch]
#   FSE_SUBNETS         comma-separated subnet ids [output subnet_ids]
#   FSE_SECURITY_GROUP  batch security group id [output batch_security_group_id]
#   DATA_BUCKET         data bucket name [output data_bucket]
set -euo pipefail

usage() { sed -n -e '/^set -euo/q' -e '2,$p' "$0"; }

cmd="${1:-}"
case "$cmd" in
pull | backtest | experiment | report | unlock) shift ;;
"")
  usage
  exit 2
  ;;
*)
  echo "unknown command: $cmd" >&2
  usage
  exit 2
  ;;
esac

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
FSE_CLUSTER="${FSE_CLUSTER:-fse}"
FSE_TASK_FAMILY="${FSE_TASK_FAMILY:-fse-batch}"
container="fse"                       # container name in the fse-batch task definition
log_group="/fse/batch"
lock_key="locks/pull.lock"
log_drain_s="${FSE_LOG_DRAIN_S:-5}"   # seconds to keep tailing after the task stops

# One value from the infra stack, for a variable that is not set.
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

tmp_dir="$(mktemp -d)"
task_arn=""      # set once run-task returned a task
task_stopped=0   # 1 once the task reached STOPPED
lock_created=0   # 1 once this run wrote the pull lock
waiter=""        # pid of a background `aws ecs wait`
tailer=""        # pid of the background `aws logs tail`

# shellcheck disable=SC2329  # runs from the EXIT trap
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$waiter" ]]; then kill "$waiter" 2>/dev/null || true; fi
  if [[ -n "$tailer" ]]; then kill "$tailer" 2>/dev/null || true; fi
  if [[ -n "$task_arn" && "$task_stopped" == 0 ]]; then
    echo "Stopping task $task_arn (a rerun of a pull resumes where it stopped) ..." >&2
    aws ecs stop-task --cluster "$FSE_CLUSTER" --task "$task_arn" \
      --reason "aws-batch.sh interrupted" >/dev/null || echo "warning: stop-task failed" >&2
  fi
  if [[ "$lock_created" == 1 ]]; then
    aws s3api delete-object --bucket "$DATA_BUCKET" --key "$lock_key" >/dev/null ||
      echo "warning: could not delete s3://$DATA_BUCKET/$lock_key; run '$0 unlock --yes'" >&2
  fi
  rm -rf "$tmp_dir"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Seconds before a lock with a parseable started= line may be replaced.
lock_ttl_s() {
  local ttl="${FSE_LOCK_TTL_S:-7200}"
  if ! [[ "$ttl" =~ ^[0-9]+$ ]] || ((ttl <= 0)); then
    ttl=7200
  fi
  printf '%s\n' "$ttl"
}

# True when the saved holder is at least FSE_LOCK_TTL_S old. Missing started= is not.
lock_file_expired() {
  local file="$1" raw started now ttl
  ttl="$(lock_ttl_s)"
  raw="$(sed -n 's/^started=//p' "$file" | head -n 1 | tr -d '\r')"
  if [[ -z "$raw" ]]; then
    return 1
  fi
  if ! started="$(date -u -d "$raw" +%s 2>/dev/null)"; then
    return 1
  fi
  now="$(date -u +%s)"
  ((now - started >= ttl))
}

# Print the current lock holder, if any.
show_lock() {
  if aws s3api get-object --bucket "$DATA_BUCKET" --key "$lock_key" "$tmp_dir/holder" \
    >/dev/null 2>&1; then
    echo "pull lock s3://$DATA_BUCKET/$lock_key is held by:" >&2
    sed 's/^/  /' "$tmp_dir/holder" >&2
    return 0
  fi
  return 1
}

# Run an `aws ecs wait` in the background, so a signal interrupts the shell's `wait`.
ecs_wait() {
  local rc=0
  aws ecs wait "$1" --cluster "$FSE_CLUSTER" --tasks "$task_arn" &
  waiter=$!
  wait "$waiter" || rc=$?
  waiter=""
  return "$rc"
}

# Read one field from the describe-tasks JSON: exit_code, status or reason.
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

if [[ "$cmd" == unlock ]]; then
  if [[ "${1:-}" != --yes ]]; then
    show_lock || echo "no pull lock at s3://$DATA_BUCKET/$lock_key" >&2
    echo "Run '$0 unlock --yes' to delete it. Make sure no pull task is running first." >&2
    exit 2
  fi
  aws s3api delete-object --bucket "$DATA_BUCKET" --key "$lock_key" >/dev/null
  echo "Deleted s3://$DATA_BUCKET/$lock_key"
  exit 0
fi

if [[ -z "${FSE_SUBNETS:-}" ]]; then FSE_SUBNETS="$(tf_output subnet_ids FSE_SUBNETS)"; fi
if [[ -z "${FSE_SECURITY_GROUP:-}" ]]; then
  FSE_SECURITY_GROUP="$(tf_output batch_security_group_id FSE_SECURITY_GROUP)"
fi

# The container command is ["batch", "<cmd>", args...]; json.dumps keeps every arg intact.
overrides="$(python3 -c 'import json, sys
print(json.dumps({"containerOverrides": [{"name": sys.argv[1], "command": ["batch", *sys.argv[2:]]}]}))' \
  "$container" "$cmd" ${1+"$@"})"

if [[ "$cmd" == pull ]]; then
  printf 'user=%s\nhost=%s\nstarted=%s\n' "${USER:-unknown}" "$(hostname)" \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$tmp_dir/lock"
  if aws s3api put-object --bucket "$DATA_BUCKET" --key "$lock_key" --if-none-match '*' \
    --body "$tmp_dir/lock" >/dev/null 2>"$tmp_dir/lock.err"; then
    lock_created=1
  elif grep -Eq 'PreconditionFailed|ConditionalRequestConflict|412' "$tmp_dir/lock.err"; then
    if aws s3api get-object --bucket "$DATA_BUCKET" --key "$lock_key" "$tmp_dir/holder" \
      >/dev/null 2>"$tmp_dir/lock.err" && lock_file_expired "$tmp_dir/holder"; then
      echo "pull lock s3://$DATA_BUCKET/$lock_key is past its $(lock_ttl_s)s TTL; replacing it" >&2
      aws s3api delete-object --bucket "$DATA_BUCKET" --key "$lock_key" >/dev/null
      if aws s3api put-object --bucket "$DATA_BUCKET" --key "$lock_key" --if-none-match '*' \
        --body "$tmp_dir/lock" >/dev/null 2>"$tmp_dir/lock.err"; then
        lock_created=1
      else
        cat "$tmp_dir/lock.err" >&2
        echo "error: could not write the pull lock after replacing an expired one" >&2
        exit 1
      fi
    else
      show_lock || echo "pull lock s3://$DATA_BUCKET/$lock_key is held" >&2
      echo "Another pull is running. If it is not, run '$0 unlock --yes'." >&2
      exit 75
    fi
  else
    cat "$tmp_dir/lock.err" >&2
    echo "error: could not write the pull lock" >&2
    exit 1
  fi
fi

aws ecs run-task --cluster "$FSE_CLUSTER" --task-definition "$FSE_TASK_FAMILY" --count 1 \
  --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
  --network-configuration \
  "awsvpcConfiguration={subnets=[$FSE_SUBNETS],securityGroups=[$FSE_SECURITY_GROUP],assignPublicIp=ENABLED}" \
  --started-by aws-batch.sh --overrides "$overrides" --output json >"$tmp_dir/run.json"
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
echo "Started $cmd as task $task_id; waiting for Fargate Spot capacity ..." >&2

# A task that stops before RUNNING fails this waiter; the stopped wait below still decides.
ecs_wait tasks-running || true
aws logs tail "$log_group" --follow --format short \
  --log-stream-name-prefix "batch/$container/$task_id" &
tailer=$!

# The waiter gives up after 10 minutes; keep waiting until the task is really STOPPED.
until ecs_wait tasks-stopped; do
  describe_task
  if [[ "$(task_field status)" == STOPPED ]]; then break; fi
done
task_stopped=1
# Keep tailing up to log_drain_s seconds for the last log lines; stop early if the
# tailer has already ended. (Integer steps of 0.2 s: bash 3.2 has no float math.)
steps=$((log_drain_s * 5))
while ((steps > 0)) && kill -0 "$tailer" 2>/dev/null; do
  sleep 0.2
  steps=$((steps - 1))
done
kill "$tailer" 2>/dev/null || true
wait "$tailer" 2>/dev/null || true
tailer=""

describe_task
code="$(task_field exit_code)"
if [[ -z "$code" ]]; then
  echo "error: task $task_id stopped without an exit code: $(task_field reason)" >&2
  exit 1
fi
echo "Task $task_id exited $code" >&2
if [[ "$cmd" == pull && ("$code" == 2 || "$code" == 3 || "$code" == 4) ]]; then
  echo "pull exit $code needs a person (see README): 2 input, 3 API key, 4 cache write" >&2
fi
exit "$code"
