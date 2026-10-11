#!/usr/bin/env bash
# Fig 12: LAS scheduling + FIFO admission control, Philly trace, 8 jobs/hr,
# 128 GPUs. One simulator+scheduler pair per acceptance threshold, in parallel.
#
# usage: run_fig12.sh <exp_prefix> [start_job=3000] [end_job=4000] [base_port=50200]
# output: repro_runs/<exp_prefix>/<policy>/  (stats JSON + logs)
set -u
REPO=/Users/yash/Desktop/gt/blox
PY=$REPO/.venv/bin/python
TRACE=/Users/yash/Desktop/gt/philly-traces/trace-data/cluster_job_log
if [ $# -lt 1 ]; then
  echo "usage: $0 <exp_prefix> [start_job=3000] [end_job=4000] [base_port=50200]"; exit 1
fi
PREFIX=$1
START=${2:-3000}
END=${3:-4000}
BASE=${4:-50200}
OUT=$REPO/repro_runs/$PREFIX
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
export PYTHONUNBUFFERED=1

# Completion is detected by an output file appearing, so a previous run's
# files under the same prefix would make every config look done instantly.
if [ -e "$OUT" ]; then
  echo "$OUT already exists; use a new prefix or delete it first"; exit 1
fi

POLICIES=(AcceptAll LoadBasedAccept-1.5x LoadBasedAccept-1.2x LoadBasedAccept-1.0x)

# Refuse to start if any port is taken: gRPC sets SO_REUSEPORT, so a stale
# process on the same port silently steals RPCs instead of failing to bind.
for i in "${!POLICIES[@]}"; do
  for port in $((BASE + 10*i)) $((BASE + 10*i + 1)) $((BASE + 10*i + 2)); do
    if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null; then
      echo "port $port already in use, aborting"; exit 1
    fi
  done
done

run_one() {
  local pol=$1 sim_port=$2 rm_port=$3 nm_port=$4
  local dir=$OUT/$pol
  mkdir -p "$dir"
  local t0=$(date +%s)

  # simulator: cwd = repo root so it finds the cached Philly pickle.
  cd "$REPO"
  "$PY" repro_runs/sim_one_config.py --cluster-job-log "$TRACE" --load 8.0 \
    --acceptance-policy "$pol" --start-job-track "$START" --end-job-track "$END" \
    --exp-prefix "$PREFIX" --simulator-rpc-port "$sim_port" \
    --central-scheduler-port "$rm_port" \
    > "$dir/simulator.log" 2> "$dir/simulator.err" &
  local sim=$!
  for _ in $(seq 60); do
    lsof -nP -iTCP:"$sim_port" -sTCP:LISTEN >/dev/null && break
    sleep 1
  done

  # scheduler: cwd = its own dir so output JSONs can't collide across runs.
  cd "$dir"
  PYTHONPATH="$REPO" "$PY" "$REPO/blox_examples/blox_new_flow_multi_run.py" \
    --simulate --load 8 --exp-prefix "$PREFIX" \
    --simulator-rpc-port "$sim_port" --central-scheduler-port "$rm_port" \
    --node-manager-port "$nm_port" \
    > "$dir/scheduler.log" 2> "$dir/scheduler.err" &
  local sched=$!

  # Done when BloxManager writes custom_metrics.json, the last of its five
  # output files (blox_manager.py:290). The scheduler may not exit on its own.
  local done_file="$dir/${PREFIX}_${START}_${END}_Las_${pol}_load_8.0_custom_metrics.json"
  while kill -0 $sched 2>/dev/null && [ ! -f "$done_file" ]; do
    sleep 15
  done
  sleep 5
  kill $sched $sim 2>/dev/null
  if [ -f "$done_file" ]; then status=finished; else status="FAILED (scheduler exited, see $dir/scheduler.err)"; fi
  echo "$pol $status in $(( $(date +%s) - t0 )) s at $(date '+%H:%M:%S')"
}

echo "Fig 12 run '$PREFIX': jobs $START-$END, started $(date '+%H:%M:%S')"
for i in "${!POLICIES[@]}"; do
  p=$((BASE + 10*i))
  run_one "${POLICIES[$i]}" $p $((p+1)) $((p+2)) &
done
wait
echo "all done at $(date '+%H:%M:%S')"
