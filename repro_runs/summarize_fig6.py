"""
Summarize a Fig 6 / Fig 7 run (repro_runs/run_fig6.sh) and check the paper's
stated trends.

usage: summarize_fig6.py <exp_prefix> [start_job=3000] [end_job=4000]

JCT = finish - submit (job_stats.json); responsiveness = first launch - submit
(responsivness.json, the file name typo is the code's). Both written by
blox_manager.py update_metrics once every tracked job has finished.

Paper (EuroSys '24, Sec 4.2, Figs 6 and 7; Philly trace, 128 GPUs, 1-9 jobs/h):
  T1  load < 4:  Optimus avg JCT  <  FIFO and Tiresias; responsiveness similar
  T2  load > 7:  Tiresias avg JCT >  FIFO and Optimus
  T3  load > 7:  FIFO has the worst (highest) responsiveness
The paper gives no numbers for these figures, so only orderings are checked.
"""
import json
import os
import statistics
import sys

PREFIX = sys.argv[1]
START = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
END = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), PREFIX)
SCHEDS = ["Fifo", "Tiresias", "Optimus"]


def load(sched, lo, kind):
    path = os.path.join(
        RUN_DIR,
        f"{sched}_load_{lo}",
        f"{PREFIX}_{START}_{END}_{sched}_AcceptAll_load_{lo}.0_{kind}.json",
    )
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return {int(k): v[1] - v[0] for k, v in json.load(f).items()}


loads = sorted(
    {
        int(d.split("_load_")[1])
        for d in os.listdir(RUN_DIR)
        if "_load_" in d and os.path.isdir(os.path.join(RUN_DIR, d))
    }
)
res = {}  # (sched, load) -> dict
for s in SCHEDS:
    for lo in loads:
        jct = load(s, lo, "job_stats")
        resp = load(s, lo, "responsivness")
        if jct is None or resp is None:
            continue
        res[(s, lo)] = dict(
            n=len(jct),
            jct=statistics.mean(jct.values()),
            jct_med=statistics.median(jct.values()),
            resp=statistics.mean(resp.values()),
        )

print(f"run '{PREFIX}', jobs {START}-{END}; times in hours")
hdr = f"{'load':>4}  " + "  ".join(f"{s:>22}" for s in SCHEDS)
print(hdr)
print(f"{'':>4}  " + "  ".join(f"{'jobs  avgJCT  avgResp':>22}" for _ in SCHEDS))
print("-" * len(hdr))
for lo in loads:
    cells = []
    for s in SCHEDS:
        r = res.get((s, lo))
        if r is None:
            cells.append(f"{'-':>22}")
        else:
            cells.append(f"{r['n']:>4} {r['jct']/3600:>8.2f} {r['resp']/3600:>8.3f}")
    print(f"{lo:>4}  " + "  ".join(cells))


def check(name, lo, cond, detail):
    print(f"  {name} load {lo}: {'PASS' if cond else 'FAIL'}  ({detail})")


print("\nPaper trend checks (only where all needed configs finished):")
for lo in loads:
    f, t, o = res.get(("Fifo", lo)), res.get(("Tiresias", lo)), res.get(("Optimus", lo))
    if lo < 4 and f and t and o:
        check(
            "T1 Optimus JCT lowest",
            lo,
            o["jct"] < f["jct"] and o["jct"] < t["jct"],
            f"O {o['jct']/3600:.2f} F {f['jct']/3600:.2f} T {t['jct']/3600:.2f} h",
        )
    if lo > 7 and f and t:
        others = [f["jct"]] + ([o["jct"]] if o else [])
        check(
            "T2 Tiresias JCT highest",
            lo,
            all(t["jct"] > x for x in others),
            f"T {t['jct']/3600:.2f} F {f['jct']/3600:.2f}"
            + (f" O {o['jct']/3600:.2f}" if o else "")
            + " h",
        )
        others = [t["resp"]] + ([o["resp"]] if o else [])
        check(
            "T3 FIFO responsiveness worst",
            lo,
            all(f["resp"] > x for x in others),
            f"F {f['resp']/3600:.2f} T {t['resp']/3600:.2f}"
            + (f" O {o['resp']/3600:.2f}" if o else "")
            + " h",
        )
