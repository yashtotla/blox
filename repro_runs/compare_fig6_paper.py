"""
Three-way comparison for Fig 6 / Fig 7 (FIFO vs Tiresias on the Philly trace):

  paper   : data points read from the vector markers of Figs 6 and 7 in the
            EuroSys '24 PDF (repro_runs/paper_fig6_fig7_points.json, committed;
            extracted with PyMuPDF; axis mapped through the tick-label centres,
            so values carry ~0.05% of full scale of extraction error)
  blox    : the shipped code's own output, from repro_runs/run_fig6.sh
            (repro_runs/<exp_prefix>/, not committed)
  offline : repro_runs/offline_sched.py --out-dir repro_runs/fig6_offline, an
            independent re-simulation of the same job stream under the
            scheduler's round semantics (not committed; '-' where missing)

The last column is paper minus ours, "ours" being blox where it exists and
offline otherwise.

usage: compare_fig6_paper.py [exp_prefix=fig6] [start=3000] [end=4000]
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "fig6"
START = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
END = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
OFF = os.path.join(HERE, "fig6_offline")
LOADS = range(1, 10)
SCHEDS = ["Tiresias", "Fifo"]
KINDS = {"job_stats": "avg JCT", "responsivness": "avg responsiveness"}
FIG = {"job_stats": "Fig6 JCT", "responsivness": "Fig7 Resp"}


def read(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return {int(k): v[1] - v[0] for k, v in json.load(f).items()}


def blox(s, lo, kind):
    return read(
        os.path.join(
            HERE, PREFIX, f"{s}_load_{lo}",
            f"{PREFIX}_{START}_{END}_{s}_AcceptAll_load_{lo}.0_{kind}.json",
        )
    )


def offline(s, lo, kind):
    return read(os.path.join(OFF, f"{s}_load_{lo}_{kind}.json"))


with open(os.path.join(HERE, "paper_fig6_fig7_points.json")) as f:
    raw = json.load(f)
paper = {}  # (fig, sched) -> {load: value}
for fig, series in raw.items():
    for s, pts in series.items():
        # keep markers that sit on an integer load; the legend's marker does not
        paper[(fig, s)] = {
            round(x): y for x, y in pts if abs(x - round(x)) < 0.1 and 1 <= round(x) <= 9
        }


def fmt(v):
    return f"{v:>9,.0f}" if v is not None else f"{'-':>9}"


for kind, label in KINDS.items():
    print(f"\n{label} in seconds, jobs {START}-{END}, 128 GPUs, Accept All")
    hdr = f"{'load':>4}"
    for s in SCHEDS:
        hdr += f" | {s + ' paper':>14}{'blox':>10}{'offline':>10}{'paper-ours':>10}"
    print(hdr)
    print("-" * len(hdr))
    for lo in LOADS:
        row = f"{lo:>4}"
        for s in SCHEDS:
            p = paper[(FIG[kind], s)].get(lo)
            b = blox(s, lo, kind)
            o = offline(s, lo, kind)
            bm = statistics.mean(b.values()) if b else None
            om = statistics.mean(o.values()) if o else None
            ours = bm if bm is not None else om
            d = p - ours if (p is not None and ours is not None) else None
            row += f" | {fmt(p):>14}{fmt(bm)}{fmt(om)}{fmt(d)}"
        print(row)

print("\nBlox output vs offline model, per job (tracked jobs only)")
print(f"{'config':<18}{'blox jobs':>10}{'JCT same':>10}{'JCT diff':>10}{'resp same':>11}{'resp diff':>10}")
for s in SCHEDS:
    for lo in LOADS:
        bj, oj = blox(s, lo, "job_stats"), offline(s, lo, "job_stats")
        br, orr = blox(s, lo, "responsivness"), offline(s, lo, "responsivness")
        if bj is None:
            print(f"{s + '_load_' + str(lo):<18}{'(blox not finished)':>10}")
            continue
        if oj is None:
            print(f"{s + '_load_' + str(lo):<18}{'(offline missing)':>10}")
            continue
        js = sum(1 for k in bj if k in oj and bj[k] == oj[k])
        rs = sum(1 for k in br if k in orr and br[k] == orr[k])
        print(f"{s + '_load_' + str(lo):<18}{len(bj):>10}{js:>10}{len(bj) - js:>10}{rs:>11}{len(br) - rs:>10}")

print("\nOptimus in the paper's Fig 6: flat-line JCT divided by Optimus JCT, per load,")
print("against the per-load table commented out at grpc_client_rm.py:227")
table = [5, 3, 1.4, 1.2, 1.1, 1.0, 1.0, 1.0, 1.0]
po, pt = paper[("Fig6 JCT", "Optimus")], paper[("Fig6 JCT", "Tiresias")]
print(f"{'load':>4}{'Tiresias':>10}{'Optimus':>10}{'ratio':>8}{'table':>7}")
for lo in LOADS:
    if lo in po and lo in pt:
        print(f"{lo:>4}{pt[lo]:>10,.0f}{po[lo]:>10,.0f}{pt[lo] / po[lo]:>8.2f}{table[lo - 1]:>7}")
