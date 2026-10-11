# Reproducing Figures 6 and 7 of Blox: FIFO vs Tiresias

Blox (Agarwal, Phanishayee, Venkataraman, EuroSys '24) compares FIFO, Tiresias
and Optimus on the "Philly trace" at 1 to 9 jobs per hour on 128 GPUs, plotting
average JCT (Figure 6) and average responsiveness (Figure 7). This folder
reproduces the FIFO and Tiresias curves with the code as shipped in this repo,
checks the shipped code against an independent re-simulation job by job, and
compares both with the data points read out of the paper's PDF.

**Result (2026-10-11):** the shipped code reproduces both curves at every load
to within one scheduling round (300 s); the Blox output and the independent
re-simulation agree on all 1001 tracked jobs in all 18 configs; and the paper's
Optimus curve turns out to be a hardcoded per-load speed table, not a scheduler
effect. Nothing from our run is committed; the exact numbers are in section 6,
so you can check that your own run lands on the same values.

## 1. What the paper claims (Section 4.2)

* Philly trace, 128 GPUs as 32 servers of 4, consolidated placement, Accept All
  admission, Poisson arrivals at 1 to 9 jobs/hour, jobs 3000 to 4000 tracked.
* Below 4 jobs/hour Optimus has the lowest JCT. Above 7 jobs/hour Tiresias has
  a higher JCT than FIFO and Optimus, and FIFO has the worst responsiveness.
* The paper gives no numbers for these figures; the values in section 6 were
  read out of the PDF's vector graphics (section 7).

## 2. What the shipped code actually simulates

Read this before quoting any number. The simulator keeps the Philly trace's job
*count* and little else:

* **Durations are synthetic.** `exponential=True` by default, so each job's
  duration is drawn from a Gavel-like distribution (80%: 31.6 min to 16.7 h,
  20%: 16.7 h to 167 h), not taken from the trace. Draws are seeded, so every
  run sees the same jobs. 45,528 jobs survive the trace filter; mean duration
  16.9 h.
* **Every job asks for 1 GPU** (`multigpu=False`). 8 jobs/hour therefore
  offers ~135 GPU-hours per hour to 128 GPUs: loads 1 to 6 never saturate,
  7 is at the edge, 8 and 9 oversubscribe. That is why the curves bend at 7.
* **Progress ignores GPU count.** A running job advances by `round / iteration
  time` per round, iteration time is always 1 s, so a job finishes when it has
  held a GPU for its duration.
* **Tiresias is single-queue LAS.** `schedulers/tiresias.py` sorts by
  `(job_priority, attained_service)` exactly like `las.py`; its multi-queue
  code computes a list it never returns.
* **Placement preference is never set**, so every job goes through the
  scattered placer. With 1-GPU jobs that is the same as consolidated.
* **Optimus crashes as shipped** (`AttributeError: optimus_scale_by_gpus`). The
  table it needs is commented out at `blox/deployment/grpc_client_rm.py:227`.
  It was active from 2023-10-26 (commit `d9b4516`) until 2024-02-09
  (`199b4da`), i.e. when the paper's numbers were produced. It is keyed by the
  *load*, not by GPUs: an Optimus job runs 5x faster at 1 job/hour, 3x at 2,
  1.4x at 3, 1.2x at 4, 1.1x at 5, 1.0x from 6 up. Section 6 shows the paper's
  Optimus points are exactly the flat line divided by those factors. Separately,
  the extra GPUs Optimus assigns go into `job_gpu_demand`, which placement never
  reads (it reads `num_GPUs`).

Commands to confirm each of these:

```sh
grep -n "exponential=\|multigpu=" simulator_runner/simulator.py
grep -n "num_GPUs\|job_gpu_demand" placement/placement.py schedulers/optimus.py
grep -n "optimus_scale_by_gpus\|sched_load" blox/deployment/grpc_client_rm.py schedulers/optimus.py
git log --format='%h %ad %s' --date=short -S optimus_scale_by_gpus -- blox/deployment/grpc_client_rm.py
diff <(sed -n '/sorted_job_order = sorted/,/)/p' schedulers/las.py) <(sed -n '/sorted_job_order = sorted/,/)/p' schedulers/tiresias.py)
```

## 3. How a run works

A run is two processes talking over gRPC on localhost.

* **Simulator** (`simulator_runner/simulator.py`): owns the workload and the
  config grid. Its `launch_server()` hardcodes the whole Figure 6 grid
  (Tiresias, Optimus, Fifo x loads 1..9) on fixed ports and feeds the cells to
  one scheduler one after another (the Readme's "about 8 hours").
  `sim_one_config.py` here serves the same `SimulatorRunner` class with a
  one-cell grid on chosen ports, so the cells run in parallel.
* **Scheduler** (`blox_examples/blox_new_flow_multi_run.py`, unchanged): asks
  the simulator for a config, instantiates the policy by name, then loops:
  metrics (simulated progress, exits), pull arrivals, admission, schedule
  (sort), placement (GPUs + preemptions), execute, advance 300 s. When all
  tracked jobs have finished it writes five JSON files to its working
  directory. JCT = finish - arrival from `*_job_stats.json`; responsiveness =
  first launch - arrival from `*_responsivness.json` (the typo is the code's).
  Its `--load` / `--scheduler-name` flags are overwritten by the simulator's
  config, so the simulator decides what runs.

`run_fig6.sh` starts one simulator + one scheduler per config, each on its own
port triple, and waits for the output files.

## 4. Setup (once)

Tested on macOS (Apple silicon) with Python 3.11. Modern versions of
protobuf, pandas and setuptools each break Blox, hence the pins.

```sh
cd <repo>
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python \
  "grpcio==1.60.0" "grpcio-tools==1.60.0" "protobuf==4.25.3" \
  "pandas==1.5.3" "numpy<2" "setuptools<81" matplotlib

# gRPC stubs are gitignored; generate them
mkdir -p blox/deployment/grpc_stubs && touch blox/deployment/grpc_stubs/__init__.py
(cd blox/deployment && PATH="$PWD/../../.venv/bin:$PATH" make grpc)

# Philly trace (Git LFS, ~1 GB download). Default location: next to the repo.
brew install git-lfs && git lfs install
git clone https://github.com/msr-fiddle/philly-traces.git ../philly-traces
tar -xzf ../philly-traces/trace-data.tar.gz -C ../philly-traces
# elsewhere? export PHILLY_TRACE=/path/to/trace-data/cluster_job_log
```

Two things to know:

* **The parsed trace is cached** in `philly_jobs_sum_attempts_exp_single.pickle`
  at the repo root (gitignored, 15 MB). The first simulator to start parses the
  39 MB JSON and writes it, which takes a minute. Run the single-config smoke
  test below *before* the 18-way sweep so that only one process creates it.
* **One code change is required** and is part of this branch:
  `blox/deployment/grpc_server_rm.py` keeps one gRPC channel to the simulator
  (`_sim_stub`) instead of opening a new one for every RPC. Same RPCs, same
  payloads, byte-identical outputs; but with 18 simulations at ~30 rounds/s the
  original exhausts macOS's 16,384 ephemeral ports in about 40 s and every run
  dies with `Can't assign requested address`. See `git log -p` for the diff.

## 5. Run

All commands from the repo root. Each run gets a fresh prefix; the runner
refuses to reuse one.

```sh
# 1. smoke test: FIFO at 6 jobs/h, jobs 0-20 tracked. ~30 s. Also builds the pickle.
repro_runs/run_fig6.sh smoke Fifo 6 0 20
.venv/bin/python repro_runs/summarize_fig6.py smoke 0 20
#    expect: 21 jobs, avg JCT 16.37 h, avg responsiveness 0.131 h

# 2. the sweep: FIFO and Tiresias at loads 1-9, jobs 3000-4000, 18 configs in parallel.
#    ~15 min on a 10-core laptop (longest config, Tiresias 9, ~14 min). Uses ports 50400-50572.
repro_runs/run_fig6.sh fig6 Fifo,Tiresias 1,2,3,4,5,6,7,8,9
#    progress: grep -c "Job IDs" repro_runs/fig6/*/scheduler.log   (rounds done per config; totals in the table below)

# 3. the offline reference (section 7), 18 configs one after another, ~30 s in total
for s in Fifo Tiresias; do for l in 1 2 3 4 5 6 7 8 9; do
  .venv/bin/python repro_runs/offline_sched.py --scheduler $s --load $l --out-dir repro_runs/fig6_offline
done; done

# 4. results
.venv/bin/python repro_runs/summarize_fig6.py fig6          # table + the paper's ordering checks
.venv/bin/python repro_runs/compare_fig6_paper.py fig6      # paper vs your run vs offline model, per-job agreement
.venv/bin/python repro_runs/plot_fig6.py fig6               # -> repro_runs/fig6/fig6_fifo_vs_tiresias.png
```

Rounds per config (the simulation stops once job 4000 has finished) and wall
time with all 18 running at once on a 10-core Apple-silicon laptop:

| load | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| FIFO rounds | 48,471 | 24,985 | 17,156 | 13,242 | 10,932 | 9,440 | 8,374 | 7,825 | 7,753 |
| Tiresias rounds | 48,471 | 24,985 | 17,156 | 13,242 | 10,932 | 9,440 | 8,402 | 20,413 | 19,944 |
| wall s, FIFO / Tiresias | 308 | 247 / 232 | 218 | 203 | 188 | 173 | 173 | 218 / 607 | 293 / 833 |

Tiresias at 8 and 9 takes longest: under LAS the tracked jobs finish much
later, so the simulation runs 2.5x more rounds, and each round sorts a queue
that has grown to thousands of jobs.

`run_fig6.sh <prefix> <scheds> <loads> [start] [end] [base_port]` takes comma
lists, so a subset such as `Fifo,Tiresias 2,5,8` is fine. Output lands in
`repro_runs/<prefix>/<Sched>_load_<L>/`; everything under `repro_runs/*/` is
gitignored, so runs never end up in a commit. Only `simulator.log`, a filtered
`scheduler.log` (one line per round plus the final averages) and the two
`.err` files are kept as logs; the driver prints the full GPU table every
round, which would be ~400 MB for a load-1 config.

If something hangs: a stale simulator on the same port silently steals RPCs
(gRPC sets `SO_REUSEPORT`). `lsof -nP -iTCP -sTCP:LISTEN | grep 504` before a
run, and `pkill -f sim_one_config; pkill -f blox_new_flow_multi_run` to clean up.

## 6. Expected numbers

Paper values are from the PDF's vector markers (section 7). "Ours" is what
`compare_fig6_paper.py` printed for our run of 2026-10-11; the outputs are not
committed, these tables are the record. The same seeded workload should give
you the same values to the second, apart from possible last-bit float
differences when the pickle is regenerated on another machine (those would
show as a handful of jobs moving by one 300 s round).

Average JCT in seconds (Figure 6):

| load | paper Tiresias | ours Tiresias | paper FIFO | ours FIFO |
|---|---|---|---|---|
| 1 | 57,031 | 56,717 | 57,031 | 56,717 |
| 2 | 57,026 | 56,712 | 57,026 | 56,712 |
| 3 | 57,030 | 56,716 | 57,030 | 56,716 |
| 4 | 57,026 | 56,713 | 57,026 | 56,713 |
| 5 | 57,025 | 56,711 | 57,025 | 56,711 |
| 6 | 57,033 | 56,719 | 57,033 | 56,719 |
| 7 | 57,072 | 56,759 | 57,196 | 56,882 |
| 8 | 189,300 | 188,995 | 131,720 | 131,412 |
| 9 | 323,469 | 323,174 | 281,443 | 281,145 |

Average responsiveness in seconds (Figure 7):

| load | paper Tiresias | ours Tiresias | paper FIFO | ours FIFO |
|---|---|---|---|---|
| 1 to 6 | 735 to 743 | 445 to 452 | 735 to 743 | 445 to 452 |
| 7 | 738 | 448 | 906 | 616 |
| 8 | 741 | 451 | 75,394 | 75,145 |
| 9 | 743 | 453 | 225,043 | 224,878 |

Every paper point is ours plus one round (290 to 314 s), including the few
seconds of load-to-load wobble in the flat region, which follows the same
pattern in both. The authors' bookkeeping stamped completion one round later
than the current code; the workload and the scheduling are the same.

Optimus in the paper's Figure 6, flat line divided by the Optimus value:

| load | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| ratio | 4.89 | 2.97 | 1.40 | 1.20 | 1.10 | 1.00 | 1.00 | 2.52 | 2.83 |
| table at `grpc_client_rm.py:227` | 5 | 3 | 1.4 | 1.2 | 1.1 | 1.0 | 1.0 | 1.0 | 1.0 |

Loads 1 to 5 are the table. Loads 8 and 9 are the shortest-remaining-time
effect, which the shipped Optimus (oracle SRTF ordering) would still produce
if the table were restored.

## 7. The two checks behind the numbers

**Independent re-simulation** (`offline_sched.py`). Generates the identical job
stream through the shipped `SimulatorRunner` (same seed, same `GetJobs` calls at
t = 0, 300, 600, ...), then applies the scheduler's round rules directly in
~100 lines, with no gRPC and none of Blox's scheduling code: top-of-round
progress and exits, arrivals, sort by `(priority, submit_time)` for FIFO or
`(priority, attained_service)` for Tiresias, and the fact that with 1-GPU jobs
Blox's placement loop plus its preemption scan is exactly "the first 128 jobs
in sort order run". The docstring lists every rule and where it was read from.

```sh
.venv/bin/python repro_runs/offline_sched.py --scheduler Tiresias --load 8 --compare-dir repro_runs/fig6/Tiresias_load_8
#   job_stats: ... identical 1001/1001 ...   responsivness: ... identical 1001/1001
```

It matched the Blox output on every tracked job's JCT and responsiveness in all
18 configs (the third table of `compare_fig6_paper.py`), including Tiresias at
load 9 with 234,000 preemptions. `--out-dir` writes its results in Blox's
file format; the loop in section 5 writes all 18 configs to `fig6_offline/`
(not committed, ~30 s in total), and `compare_fig6_paper.py`
reads them from there. Its stdout also gives the preemption count per config.

**Paper points** (`extract_paper_points.py`). Figures 6 and 7 are vector
graphics in the PDF; each data point is a filled marker in the series colour.
The script maps marker centres through the tick-label positions and writes
`paper_fig6_fig7_points.json` in this folder. Extraction error is ~0.05% of
full scale. That file is committed because the PDF is not in the repo;
re-running the extraction is optional:

```sh
uv run --with pymupdf python repro_runs/extract_paper_points.py "/path/to/2024 EuroSYS - Blox.pdf"
```

## 8. Files

| File | Role |
|---|---|
| `run_fig6.sh` | Runs one simulator + scheduler pair per config in parallel; `<prefix> <scheds> <loads> [start end base_port]`. |
| `sim_one_config.py` | Serves one cell of `simulator.py`'s grid on chosen ports. Only the grid and ports differ from the shipped `launch_server()`. |
| `summarize_fig6.py` | Table of avg JCT / responsiveness per config and the paper's three ordering claims. |
| `offline_sched.py` | Independent re-simulation; `--compare-dir` checks a Blox config job by job, `--out-dir` writes reference files. |
| `compare_fig6_paper.py` | Paper vs Blox run vs offline model, plus the Blox-vs-offline per-job table and the Optimus ratios. |
| `plot_fig6.py` | The two-panel figure, paper points over our curves. |
| `extract_paper_points.py` | Reads the paper's data points out of the PDF (needs PyMuPDF). |
| `paper_fig6_fig7_points.json` | The paper's Figure 6 and 7 data points as read from the PDF. The only data file committed. |
| `<prefix>/`, `fig6_offline/` | Run outputs and the offline reference files. Gitignored (`repro_runs/*/`); regenerate with section 5. |

This folder used to hold a Figure 12 harness (`run_fig12.sh`,
`summarize_fig12.py`) and an import checker for the dead-code cleanup; they
were removed in the commit that added this README and are in git history.
`sim_one_config.py` still accepts `--acceptance-policy`, so the Figure 12
configs (LAS, load 8, `LoadBasedAccept-1.5x` / `1.2x` / `1.0x`) can be served
the same way if anyone picks that figure up.
