# ACME-on-Blox — findings so far

Group 14 · last updated 2026-10-02 · all numbers below computed from the real files, not quoted from papers unless marked [paper]

Paths: this repo (fork of msr-fiddle/blox) · traces `~/Desktop/gt/AcmeTrace` (116 MB, cloned from InternLM/AcmeTrace)

---

## 1. What's in the ACME traces

Seren `node_num,gpu_num,cpu_num,type,state,submit_time,start_time,end_time,duration,queue,gpu_time` (+`job_id,user`)
Kalos adds `mem_per_pod_GB,shared_mem_per_pod,fail_time,stop_time`. **Timezones differ: Seren +08:00, Kalos +00:00.**

| | Seren | Kalos |
|---|---|---|
| rows / GPU jobs | 818,327 / 450,590 | 62,413 / 19,907 |
| max GPUs | 1024 | 1024 |
| 1-GPU jobs | 54.0% | 86.3% |
| failed+preempted | 43.6% | 39.7% |

GPU-time is extremely concentrated:

| GPU-time share | Seren | Kalos |
|---|---|---|
| ≥256-GPU jobs | 56.4% | **96.3%** |
| Pretrain | 69.5% (0.9% of jobs) | 95.3% (3.2% of jobs) |
| Eval | 6.2% (64.9% of jobs) | 0.4% (92.8% of jobs) |

**Not in the trace:** iterations, tokens, throughput, loss, checkpoints, model name/size, job name, node list, retry linkage, VC/quota. `gpu_time` is just `duration × gpu_num` — no independent measure of work.
Utilization time series exist (80 GB on HF `Qinghao/AcmeTrace`) but are keyed by IP, and the job trace has no node list → **job↔utilization join is impossible.**

Caveat: `cluster_summary.csv` reports 1,031,550 Seren jobs (pre-filter, as in the paper); the released CSV has 818,327.

## 2. Arrivals are not Poisson

| | Seren | Kalos | Poisson |
|---|---|---|---|
| CV of inter-arrival | 14.7 | 11.3 | 1.0 |
| same-second arrivals | 32.1% | 31.0% | ~0 |
| max/median hourly rate | 272× | 171× | ~1 |
| top-5 users' job share | 63.7% | 99.4% | — |

A third of jobs arrive in batches (users firing off many Eval jobs at once) — a Poisson process cannot generate this.
Blox discards trace timestamps entirely and synthesizes Poisson arrivals from `--jobs-per-hour` (`workload/workload.py:296-304`), cycling the job list modulo.

## 3. Blox has no speedup model — verified in source

- `workload/parse_philly_jobs.py:123` → `iteration_time_seconds = 1` hardcoded; `job_total_iteration` = wall-clock seconds.
- `blox/deployment/grpc_client_rm.py:251` → progress = `round_duration / job_iteration_time`, **independent of GPUs held.**
- Only GPU-scaling hook is `optimus_scale_by_gpus` at `:268`; its definition is commented out at `:232-243` → **Optimus raises AttributeError.**
- `workload/workload.py:348` → `add_synergy_profile(job)` commented out, so per-model iteration times are never applied.
- `--sim-type` is parsed but read nowhere.
- Placement bugs: `placement/placement.py:242` assigns a typo'd var (`node_with_min_moRE_gpUs`) so the consolidated fallback never fires; `:264-268` overshoots `numGPUs_needed` when scattering across nodes.
- Repo has 4 near-duplicate workload stacks — **`workload/` is the live one**; `helpers/`+`jobs/` and `workload_synergy/` are unreachable from the simulators.

**Consequence: today, 8 GPUs vs 1 GPU changes whether a job *fits*, not when it finishes.** This breaks Philly reproduction too, independent of ACME.

### 3a. Blox's inherited Philly filters would destroy the ACME workload

`workload/parse_philly_jobs.py:110-115` keeps only jobs with **31.6 min < duration < 10,000 min** (a "gavel/gandiva-like" window). Measured against ACME GPU jobs:

| | survives filter | of GPU-time retained |
|---|---|---|
| Seren | **7.4%** of jobs (33,511/450,590) | 90.1% |
| Kalos | **7.8%** of jobs (1,547/19,907) | 90.2% |

It deletes ~92% of jobs — and specifically the short Eval jobs whose **queueing-delay inversion is the headline ACME finding** (§5). Additionally `:75-77` caps jobs at 16 GPUs under `multigpu`, which would delete every ≥256-GPU job (56–96% of GPU-time), and `:141-142` forces `gpu_demand = 1` when `multigpu=False` — **the default** (`simulator_simple.py:65`, not CLI-exposed).

→ The ACME loader must be written fresh, not by adapting the Philly parser.

### 3b. In default config, almost nothing of the Philly trace survives

`SimulatorRunner.__init__` defaults `exponential=True` and `multigpu=False` (`simulator_simple.py:42-43`), neither CLI-exposed. Consequences, in order:

| Trace attribute | What the default simulator actually uses |
|---|---|
| arrival time | **discarded** → Poisson at `--jobs-per-hour` |
| duration | **discarded** → `get_gavel_like_iter()`: 20% draw `60·10^U(3,4)` s, else `60·10^U(1.5,3)` s |
| GPU demand | **discarded** → forced to 1 |
| model | assigned from `model_zoo` by a 34/33/33 image/lang/speech split |

What's left of Philly is the job *count* and which jobs pass the duration filter. Set `exponential=False` to use real trace durations and `multigpu=True` for real GPU demand (capped at 16).

This matters twice over: it's a caveat on any Philly "reproduction", and it means the ACME replay question is less about porting a parser than about deciding which trace attributes we actually want to honor.

Also: Blox expects Philly's **nested JSON** (`attempts[].detail[].gpus`, from msr-fiddle/philly-traces), keyed on per-attempt retries. ACME has no retry linkage at all, so the two schemas don't align structurally. AcmeTrace's bundled `philly_trace.csv` is flattened CSV and won't drop into Blox either.

## 4. The core modeling problem

To simulate any scheduler that preempts/migrates/resizes, we need throughput-vs-GPU-count. ACME gives one (allocation, duration) pair per job.

**This is not identifiable** — one observation can't separate iteration count from iteration time, let alone fit a scaling parameter. No published work infers it. Everyone either keeps jobs rigid or imports a measured catalog.

What others do: Pollux profiled 146 allocations and interpolates · Sia profiles 1 GPU × ~10 batch sizes, then assumes perfect DP scaling until it observes otherwise · Gavel uses a measured throughput matrix, rigid jobs · Tiresias/Synergy need no GPU-scaling model at all · **tLoRA** uses ACME for arrivals + GPU counts only and micro-benchmarks throughput itself · **SchedMate** (arXiv 2510.03334) samples ACME and reuses a semantically-matched job's performance model.

**Proposed approach — anchored scaling.** Define each job's work as `duration × gpu_num` at its recorded `gpu_num = G₀`; then `throughput(G) = throughput(G₀) · S(G)/S(G₀)`. The trace replays *exactly* when `G = G₀` (free correctness invariant), and the assumed curve only bites when a scheduler resizes. Sweep the curve's free parameter; report whether scheduler **ranking** is invariant. Ratios with error bars survive review; a single absolute JCT number does not.

Measured envelope for the sweep [paper]: MegaScale 175B at fixed global batch — 59.1% MFU @3072 GPUs → 55.2% @12288, i.e. ~1–2pp per doubling. Megatron-LM SC'21: 52% of peak at 3072 A100s. ACME paper: median SM activity ~40%; MoE/all-to-all jobs degrade much faster (single IB NIC per server).

Constraint to respect: allocations are **discrete** — TP×PP group sizes are fixed at launch, so legal resizes are multiples of TP×PP, not arbitrary.

Risk: profiling on 8–16 GPUs extrapolates 1–2 orders of magnitude to where ~96% of Kalos GPU-time actually lives (§1).

## 5. Findings that are themselves results

- **Queueing inverts.** Philly: big jobs wait longest. ACME: short Eval jobs wait longest, from head-of-line blocking behind pretrain quota reservation. [paper] — ready-made H1 evidence.
- **GPU-sharing/packing policies have little headroom.** ACME median GPU util 97–99%, bimodal at 0/100%, vs Philly 48%. [paper]
- ACME avg job duration is 12.8× shorter than Philly [paper]; Seren median GPU-job duration 135 s, p99 25,200 s (measured).

## 6. Open questions

1. **Sia + gradient noise scale.** Sia inherits Pollux's goodput = throughput × statistical efficiency; statistical efficiency needs GNS, which ACME can't supply and we can't synthesize honestly. Leaning toward importing a published GNS curve per job type and labelling it an assumption — undecided. Affects the Oct 8 deliverable.
2. Arrivals: replay real timestamps, keep a Poisson control arm for matched ACME-vs-Philly comparison, or both as separate experiments.
3. Which jobs to include — all GPU jobs, or Pretrain/SFT only? Eval is 65–93% of job count but <7% of GPU-time.
4. Cluster size — Blox defaults to 32×4 = 128 GPUs; Seren is 2,288 and Kalos 2,416. Scale up, subsample the trace, or both?
5. How to treat FAILED/CANCELLED jobs (~40%). Replay as-is, or only COMPLETED? Cancelled jobs are ~7% of count but >60% of GPU-time [paper].
6. **Novelty check:** SchedMate already simulates on ACME *and* enhances Sia. Read it early.

## 7. Running Blox — verified setup recipe

The Readme's instructions are incomplete. These steps are tested on macOS (Darwin 27, Apple silicon), 2026-10-02.

```bash
cd ~/Desktop/gt/blox

# 1. Python 3.11. The system default (3.14) is too new to build older wheels.
uv venv --python 3.11 .venv

# 2. Deps. These pins are all REQUIRED — modern versions each break Blox:
#      protobuf >=5.26  removed MessageToDict(including_default_value_fields=)  [grpc_server_rm.py:42]
#      pandas   >=2.0   removed DataFrame.append                               [cluster_state.py:89]
#      setuptools >=81  removed pkg_resources, which grpcio-tools 1.60 imports
#    pandas 1.5.3 rather than the Readme's 1.3.0: same .append, but supports Python 3.11.
uv pip install --python .venv/bin/python \
  "grpcio==1.60.0" "grpcio-tools==1.60.0" "protobuf==4.25.3" \
  "pandas==1.5.3" "numpy<2" "setuptools<81" matplotlib

# 3. gRPC stubs. The Readme omits this entirely. Stubs are gitignored
#    (`*pb2*.py`) and grpc_stubs/ does not exist in a fresh clone.
mkdir -p blox/deployment/grpc_stubs
touch blox/deployment/grpc_stubs/__init__.py
cd blox/deployment && PATH=~/Desktop/gt/blox/.venv/bin:$PATH make grpc && cd ../..

# 4. Philly trace — NOT DONE YET. Git LFS, 1.06 GB compressed / 6.6 GB unpacked.
brew install git-lfs && git lfs install
git clone https://github.com/msr-fiddle/philly-traces.git ~/Desktop/gt/philly-traces
tar -xzf ~/Desktop/gt/philly-traces/trace-data.tar.gz -C ~/Desktop/gt/philly-traces
```

Then two terminals, from the repo root:
```bash
env PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python .venv/bin/python simulator_simple.py \
  --cluster-job-log <path>/cluster_job_log --jobs-per-hour 4 --exp-prefix test --simulator-rpc-port 50599
env PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python .venv/bin/python las_scheduler.py \
  --simulate --load 4 --exp-prefix test --simulator-rpc-port 50599 --start-id-track 0 --stop-id-track 20
```

**Verified working:** venv + deps, stub generation, both entry points import, and the scheduler↔simulator gRPC handshake round-trips. The only failure was reading the (absent) trace.

### Gotchas

- **The Philly trace is mandatory — synthetic mode is unreachable.** `--cluster-job-log` defaults to `""` (`simulator_simple.py:401`), but the mode check is `elif cluster_job_log is None` (`workload/workload.py:51`). An empty string never matches, so every CLI run takes the `"philly"` branch and calls `open("")` → `FileNotFoundError` inside a gRPC handler. One-line fix if you want synthetic runs: change the default to `None`.
- **Artifact scripts aren't where the Readme says.** `simulator.py`, `simulator_acceptance_policy.py`, `simulator_dual_load.py` are in `simulator_runner/`; `blox_new_flow_multi_run.py` is in `blox_examples/`. They use root-relative imports (`from workload import Workload`), so they need `PYTHONPATH=.` plus an explicit path.
- **The scheduler's `--start-id-track` / `--stop-id-track` flags are dead.** The simulator owns the range (`--start-job-track`/`--end-job-track`, default **3000–4000**) and pushes it over RPC, overwriting the scheduler's args (`las_scheduler.py:118`). Set the range on the *simulator* or your run tracks 1000 jobs.
- **`--scheduler` is a dead flag** (like `--sim-type`). It's parsed (`simulator_simple.py:413`, default `Fifo`) but never passed to `SimulatorRunner`, which is constructed with a hardcoded `["Las"]` (`:441-443`). `las_scheduler.py:93` independently hardcodes `schedulers.Las(args)`. So output is correctly labeled `Las` — but changing the policy means editing both files, not passing a flag.
- With one scheduler, one placement, one acceptance policy and `list_jobs_per_hour = np.arange(load, load+1, 1.0)` → **exactly one config per run**, after which the scheduler prints `No Config Sent` and exits.
- Output lands in the **cwd** as `{exp_prefix}_{first}_{last}_{scheduler}_load_{load}_{job,cluster,run_time}_stats.json` (`blox/utils.py:324-344`).
- Readme estimates **~8 hours** for the Figure 6 reproduction (FIFO/LAS/Optimus CDF+JCT at load 6).
- Optimus will crash — see §3 (`optimus_scale_by_gpus` commented out).
- **A stale simulator on the same port silently hijacks your run — and costs hours.** gRPC enables `SO_REUSEPORT`, so a second simulator binds the same port with *no error* and the kernel splits connections between them. If the stale one has already handed out its single config, `GetConfig` takes the `IndexError` branch (`simulator_simple.py:112-123`) and returns `{'scheduler': '', 'load': -1, 'start_id_track': 0, 'stop_id_track': 0}`. The run then does nothing forever. **Check `lsof -nP -iTCP:<port> -sTCP:LISTEN` before every run.**
- **Handshake check:** the simulator must print `Job config {...}` within seconds of the scheduler starting. If it prints only `Print Server started` and then stays silent, it is receiving no RPCs — kill everything and check the port.
- **That null config never terminates the run** — a real bug. `las_scheduler.py:102` guards on `args.scheduler_name == ""`, but the sentinel arrives in `new_config["scheduler"]` and is only copied to `args.scheduler_name` eight lines later. So the intended `No Config Sent` → `sys.exit()` path is unreachable, and the scheduler loops on an empty config indefinitely instead of exiting. Fix: test `new_config["scheduler"] == ""`.
- Fixed gRPC ports: only one run per port per machine. Use `--simulator-rpc-port` to parallelize.
- `.venv/` self-ignores (uv writes `.venv/.gitignore` with `*`), so it stays out of `git status`. But `blox/deployment/grpc_stubs/` does show up untracked — `.gitignore` covers `*pb2*.py`, not the `__init__.py` we add. Add `grpc_stubs/` to `.gitignore` if that bothers you.
- Stale pickles (`philly_jobs_*.pickle`) silently win over the trace if present (`workload/workload.py:112-177`). Delete them after changing parse logic.

## 8. Status

**Verified 2026-10-02: Blox runs end to end on the Philly trace.** LAS, 21 tracked jobs (ids 0–20), load 4 jobs/h, `--round-duration 3600`, 128 GPUs. All 21 completed; makespan 457,200 s; median JCT 13,452 s. Writes two identically-contented sets of JSON (`utils.py:327` and `blox_manager.py:253` use different filename patterns).

That run is a plumbing test, not a result: `--round-duration 3600` quantizes every completion to an hour boundary, durations were synthetic (§3b), and all jobs were 1 GPU so there was no contention on 128 GPUs.

Still open:
- Whether published Philly results reproduce with real durations (`exponential=False`), real GPU demand (`multigpu=True`) and `--round-duration 300`.
- Whether Figure 6 reproduces at all given §3 — it compares FIFO/LAS/Optimus, and Optimus crashes as shipped.
