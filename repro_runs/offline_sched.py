"""
Offline re-simulation of a Blox FIFO or Tiresias run, to cross-check a Blox
output directory job by job without gRPC.

It reuses the shipped simulator class to generate the identical job stream
(same seed, same GetJobs calls at t = 0, 300, 600, ...), then applies the
scheduler's round semantics as read from blox_new_flow_multi_run.py,
blox_manager.py, placement.py and grpc_client_rm.py:

  top of round T : every running job gains one round of service and
                   round/iter_time iterations; it exits when iterations reach
                   job_total_iteration, finish time = T. The first such call
                   after a launch records responsiveness = T - arrival.
                   Stop when every tracked id has finished.
  arrivals       : jobs with arrival <= T, ids in arrival order.
  order          : Fifo  -> (job_priority, submit_time)
                   Tiresias (= single-queue LAS) -> (job_priority, attained_service)
                   ties keep id order (stable sort over an insertion-ordered dict).
  placement      : with 1-GPU jobs the placement loop plus its preemption scan
                   is exactly "the first <gpus> jobs in order run"; everything
                   else is queued or preempted.
  bookkeeping    : a job that is neither running, launched nor preempted this
                   round accumulates time_since_scheduled; at >= priority
                   threshold (3,600,000 s) its job_priority drops from 999 to 1.

usage: offline_sched.py --scheduler Fifo|Tiresias --load L [--start 3000 --end 4000]
                        [--compare-dir repro_runs/<prefix>/<Sched>_load_<L>]
"""
import argparse
import contextlib
import glob
import json
import os
import statistics
import sys

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACE = os.environ.get(
    "PHILLY_TRACE", os.path.join(REPO, "..", "philly-traces", "trace-data", "cluster_job_log")
)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "simulator_runner"))
os.chdir(REPO)  # the cached Philly pickle is read from the cwd

# Simulator first: importing blox appends blox/ to sys.path, after which
# workload/parse_philly_jobs.py's bare `from utils import` gets blox/utils.py.
import simulator_acceptance_policy as sim  # noqa: E402
from blox.deployment.grpc_stubs import rm_pb2  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--scheduler", choices=["Fifo", "Tiresias"], required=True)
ap.add_argument("--load", type=float, required=True)
ap.add_argument("--start", type=int, default=3000)
ap.add_argument("--end", type=int, default=4000)
ap.add_argument("--gpus", type=int, default=128)
ap.add_argument("--round", type=int, default=300)
ap.add_argument("--priority-thresh", type=int, default=3600 * 1000)
ap.add_argument("--compare-dir")
ap.add_argument("--out-dir", help="write <Sched>_load_<L>_{job_stats,responsivness}.json in Blox's format")
args = ap.parse_args()

runner = sim.SimulatorRunner(
    TRACE, [args.load], (args.start, args.end), [args.scheduler], ["Place"], ["AcceptAll"]
)
cfg = runner.simulator_config.pop(0)  # what GetConfig does, minus node registration
runner.workload = runner._generate_workload(cfg)
runner.prev_job_time = 0
runner.prev_job = None
devnull = open(os.devnull, "w")


def pull(t):
    with contextlib.redirect_stdout(devnull):
        resp = runner.GetJobs(rm_pb2.IntVal(value=t), None)
    return list(json.loads(resp.response).values())


tracked = set(range(args.start, args.end + 1))
active = {}
finished = set()
jct, resp = {}, {}
running = set()
next_id = 0
T = 0
rounds = 0
preemptions = 0
while True:
    exits = []
    for jid in running:
        j = active[jid]
        if not j["launched_once"]:
            j["launched_once"] = True
            if jid in tracked:
                resp[jid] = (j["submit"], T)
        j["executed"] += args.round / j["iter_time"]
        j["attained"] += args.round
        if j["executed"] >= j["total_iter"]:
            exits.append(jid)
    for jid in exits:
        if jid in tracked:
            jct[jid] = (active[jid]["submit"], T)
        finished.add(jid)
        running.discard(jid)
        del active[jid]
    if tracked <= finished:
        break

    for job in pull(T):
        active[next_id] = dict(
            submit=job["job_arrival_time"],
            total_iter=job["job_total_iteration"],
            iter_time=job["job_iteration_time"],
            attained=0,
            executed=job.get("job_executed_iteration", 0),
            priority=999,
            tss=0,
            launched_once=False,
        )
        next_id += 1

    if args.scheduler == "Fifo":
        order = sorted(active, key=lambda k: (active[k]["priority"], active[k]["submit"]))
    else:
        order = sorted(active, key=lambda k: (active[k]["priority"], active[k]["attained"]))
    new_running = set(order[: args.gpus])
    preempted = running - new_running
    launched = new_running - running
    preemptions += len(preempted)
    running = new_running

    for jid, j in active.items():
        if jid in preempted or jid in launched or jid in running:
            j["tss"] = 0
        else:
            j["tss"] += args.round
            if j["tss"] >= args.priority_thresh:
                j["priority"] = 1
    T += args.round
    rounds += 1

jcts = {k: v[1] - v[0] for k, v in jct.items()}
resps = {k: v[1] - v[0] for k, v in resp.items()}
print(
    f"offline {args.scheduler} load {args.load:g}, jobs {args.start}-{args.end}: "
    f"{rounds} rounds, {next_id} jobs generated, {preemptions} preemptions, "
    f"avg JCT {statistics.mean(jcts.values())/3600:.3f} h, "
    f"avg responsiveness {statistics.mean(resps.values())/3600:.4f} h"
)

if args.out_dir:
    os.makedirs(args.out_dir, exist_ok=True)
    stem = os.path.join(args.out_dir, f"{args.scheduler}_load_{args.load:g}")
    with open(stem + "_job_stats.json", "w") as f:
        json.dump({str(k): list(v) for k, v in jct.items()}, f)
    with open(stem + "_responsivness.json", "w") as f:
        json.dump({str(k): list(v) for k, v in resp.items()}, f)
    print(f"  wrote {stem}_job_stats.json and _responsivness.json")

if args.compare_dir:

    def load_blox(kind):
        files = glob.glob(os.path.join(args.compare_dir, f"*_{kind}.json"))
        if len(files) != 1:
            print(f"  {kind}: expected one file in {args.compare_dir}, found {len(files)}")
            return None
        with open(files[0]) as f:
            return {int(k): v[1] - v[0] for k, v in json.load(f).items()}

    for kind, mine in (("job_stats", jcts), ("responsivness", resps)):
        theirs = load_blox(kind)
        if theirs is None:
            continue
        common = sorted(set(mine) & set(theirs))
        same = [k for k in common if mine[k] == theirs[k]]
        diff = [k for k in common if mine[k] != theirs[k]]
        print(
            f"  {kind}: blox has {len(theirs)} tracked jobs, offline {len(mine)}; "
            f"identical {len(same)}/{len(common)}, different {len(diff)}"
        )
        for k in diff[:5]:
            print(f"    job {k}: offline {mine[k]}  blox {theirs[k]}")
