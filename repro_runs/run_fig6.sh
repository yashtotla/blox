#!/usr/bin/env bash
# Fig 6 / Fig 7: FIFO, Tiresias, Optimus on the Philly trace, 128 GPUs
# (32 x 4), consolidated placement, Accept All, loads 1..9 jobs/hour, jobs
# 3000-4000 tracked. Avg JCT (Fig 6) and avg responsiveness (Fig 7).
#
# The shipped simulator_runner/simulator.py hardcodes exactly this grid
# (Tiresias/Optimus/Fifo x np.arange(1,10) x Place x AcceptAll) on fixed ports
# and feeds the cells to one scheduler in sequence. repro_runs/sim_one_config.py
# serves one cell of it per process with its own ports, so the cells run in
# parallel. Scheduler side is the shipped blox_examples/blox_new_flow_multi_run.py,
# unchanged.
#
# usage: run_fig6.sh <exp_prefix> <scheds> <loads> [start=3000] [end=4000] [base_port=50400]
#   scheds: comma list from Fifo,Tiresias,Optimus     e.g. Fifo,Tiresias
#   loads:  comma list of integer jobs/hour            e.g. 2,5,8
# output: repro_runs/<exp_prefix>/<sched>_load_<L>/  (stats JSON + logs)
# summary: python repro_runs/summarize_fig6.py <exp_prefix> [start] [end]
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY=$REPO/.venv/bin/python
# Philly trace: set PHILLY_TRACE, or clone msr-fiddle/philly-traces next to the repo.
TRACE=${PHILLY_TRACE:-$REPO/../philly-traces/trace-data/cluster_job_log}
if [ ! -f "$TRACE" ] && [ ! -f "$REPO/philly_jobs_sum_attempts_exp_single.pickle" ]; then
  echo "Philly trace not found at $TRACE (set PHILLY_TRACE) and no cached pickle in $REPO"; exit 1
fi
if [ $# -lt 3 ]; then
  echo "usage: $0 <exp_prefix> <scheds> <loads> [start=3000] [end=4000] [base_port=50400]"; exit 1
fi
PREFIX=$1
IFS=, read -r -a SCHEDS <<< "$2"
IFS=, read -r -a LOADS <<< "$3"
START=${4:-3000}
END=${5:-4000}
BASE=${6:-50400}
OUT=$REPO/repro_runs/$PREFIX
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
export PYTHONUNBUFFERED=1

for s in "${SCHEDS[@]}"; do
  case $s in Fifo|Tiresias|Optimus) ;; *) echo "unknown scheduler '$s' (Fifo, Tiresias, Optimus)"; exit 1;; esac
done

# Completion is detected by an output file appearing, so a previous run's
# files under the same prefix would make every config look done instantly.
if [ -e "$OUT" ]; then
  echo "$OUT already exists; use a new prefix or delete it first"; exit 1
fi

# One (sim, rm, nm) port triple per config. Refuse to start if any is taken:
# gRPC sets SO_REUSEPORT, so a stale process on the same port silently steals
# RPCs instead of failing to bind.
n=$(( ${#SCHEDS[@]} * ${#LOADS[@]} ))
for i in $(seq 0 $((n - 1))); do
  for port in $((BASE + 10*i)) $((BASE + 10*i + 1)) $((BASE + 10*i + 2)); do
    if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null; then
      echo "port $port already in use, aborting"; exit 1
    fi
  done
done

run_one() {
  local sched=$1 load=$2 sim_port=$3 rm_port=$4 nm_port=$5
  local dir=$OUT/${sched}_load_${load}
  mkdir -p "$dir"
  local t0=$(date +%s)

  # simulator: cwd = repo root so it finds the cached Philly pickle
  # (philly_jobs_sum_attempts_exp_single.pickle: exponential=True, 1 GPU/job).
  cd "$REPO"
  "$PY" repro_runs/sim_one_config.py \
    --cluster-job-log "$TRACE" --load "$load.0" --scheduler "$sched" \
    --acceptance-policy AcceptAll --start-job-track "$START" --end-job-track "$END" \
    --exp-prefix "$PREFIX" --simulator-rpc-port "$sim_port" \
    --central-scheduler-port "$rm_port" \
    > "$dir/simulator.log" 2> "$dir/simulator.err" &
  local sim=$!
  for _ in $(seq 60); do
    lsof -nP -iTCP:"$sim_port" -sTCP:LISTEN >/dev/null && break
    sleep 1
  done

  # scheduler: cwd = its own dir so output JSONs can't collide across runs.
  # Its --load is int-typed and overwritten by the simulator's config anyway.
  # stdout is filtered: the driver prints the full 128-row GPU DataFrame and
  # the metrics dict every round (~9 KB/round, ~400 MB for a load-1 config), so
  # keep only the one-line-per-round marker, the final averages and the exit
  # messages. Tracebacks go to scheduler.err, which is kept whole.
  cd "$dir"
  PYTHONPATH="$REPO" "$PY" "$REPO/blox_examples/blox_new_flow_multi_run.py" \
    --simulate --load "$load" --exp-prefix "$PREFIX" \
    --simulator-rpc-port "$sim_port" --central-scheduler-port "$rm_port" \
    --node-manager-port "$nm_port" \
    > >(grep --line-buffered -E '^(Job IDs to terminate|Scheduler: |Terminate current config|No Config)' > "$dir/scheduler.log") \
    2> "$dir/scheduler.err" &
  local sched_pid=$!

  # Done when BloxManager writes custom_metrics.json, the last of its five
  # output files (blox_manager.py update_metrics). Also stop if the scheduler
  # process dies (e.g. Optimus AttributeError).
  local done_file="$dir/${PREFIX}_${START}_${END}_${sched}_AcceptAll_load_${load}.0_custom_metrics.json"
  while kill -0 $sched_pid 2>/dev/null && [ ! -f "$done_file" ]; do
    sleep 15
  done
  sleep 5
  kill $sched_pid $sim 2>/dev/null
  local rounds
  rounds=$(grep -c 'Job IDs to terminate' "$dir/scheduler.log")
  if [ -f "$done_file" ]; then status=finished; else status="FAILED (see $dir/scheduler.err)"; fi
  echo "$sched load $load: $status, $rounds rounds, $(( $(date +%s) - t0 )) s wall, at $(date '+%H:%M:%S')"
}

echo "Fig 6 run '$PREFIX': scheds ${SCHEDS[*]}, loads ${LOADS[*]}, jobs $START-$END, $n configs in parallel, started $(date '+%H:%M:%S')"
i=0
for s in "${SCHEDS[@]}"; do
  for l in "${LOADS[@]}"; do
    p=$((BASE + 10*i))
    run_one "$s" "$l" $p $((p+1)) $((p+2)) &
    i=$((i+1))
  done
done
wait
echo "all done at $(date '+%H:%M:%S')"
