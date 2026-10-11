"""
Serve one cell of simulator_runner/simulator.py's Figure 6 grid on
caller-chosen ports, so the cells can run in parallel.

simulator.py's launch_server() hardcodes the whole grid (Tiresias, Optimus,
Fifo x loads 1..9 x Place x AcceptAll) and the ports 50050/50051, and hands the
cells to one scheduler process one after another. This file uses the very same
SimulatorRunner class and defaults (exponential=True, multigpu=False,
32 machines x 4 GPUs, round 300 s, random.seed(1) per cell) but with a
one-cell grid and the ports taken from the command line. Nothing about the
workload or the simulation changes; repro_runs/run_fig6.sh starts one of these
per cell next to one blox_examples/blox_new_flow_multi_run.py.
"""
import argparse
import os
import sys
from concurrent import futures

import grpc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "simulator_runner"))

# Simulator first: importing blox appends blox/ to sys.path, after which
# workload/parse_philly_jobs.py's bare `from utils import` gets blox/utils.py.
from simulator import SimulatorRunner  # noqa: E402
from blox.deployment.grpc_stubs import simulator_pb2_grpc  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--cluster-job-log", required=True, help="Philly cluster_job_log")
parser.add_argument("--load", type=float, required=True, help="jobs per hour")
parser.add_argument("--scheduler", required=True, help="Fifo, Tiresias or Optimus")
parser.add_argument("--acceptance-policy", default="AcceptAll")
parser.add_argument("--start-job-track", type=int, default=3000)
parser.add_argument("--end-job-track", type=int, default=4000)
parser.add_argument("--exp-prefix", required=True)
parser.add_argument("--simulator-rpc-port", type=int, required=True)
parser.add_argument("--central-scheduler-port", type=int, required=True)
args = parser.parse_args()

runner = SimulatorRunner(
    args.cluster_job_log,
    [args.load],
    (args.start_job_track, args.end_job_track),
    [args.scheduler],
    ["Place"],
    [args.acceptance_policy],
    exp_prefix=args.exp_prefix,
)
runner.ipaddr_rm = f"localhost:{args.central_scheduler_port}"

server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
simulator_pb2_grpc.add_SimServerServicer_to_server(runner, server)
server.add_insecure_port(f"[::]:{args.simulator_rpc_port}")
server.start()
print("Print Server started", flush=True)
server.wait_for_termination()
