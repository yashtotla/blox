"""
Serve simulator_runner/simulator_acceptance_policy.SimulatorRunner for a single
config on caller-chosen ports, so configs can run in parallel.

Same class and defaults as the shipped script (exponential=True, multigpu=False,
32 machines x 4 GPUs, round 300 s, random.seed(1) per config). The shipped
launch_server() hardcodes the config list and ports 50050/50051; that is the
only thing replaced here.
"""
import argparse
import os
import sys
from concurrent import futures

import grpc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "simulator_runner"))

from simulator_acceptance_policy import SimulatorRunner  # noqa: E402
from blox.deployment.grpc_stubs import simulator_pb2_grpc  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--cluster-job-log", required=True)
parser.add_argument("--load", type=float, default=8.0)
parser.add_argument("--scheduler", default="Las")
parser.add_argument("--acceptance-policy", required=True)
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
