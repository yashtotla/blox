"""
Plot Fig 6 (avg JCT) and Fig 7 (avg responsiveness) for FIFO vs Tiresias:
the paper's points, read from the PDF's vector markers, over our curves from
the shipped code (Blox output of run_fig6.sh where finished, offline model
otherwise).

usage: plot_fig6.py [exp_prefix=fig6] [out=repro_runs/<exp_prefix>/fig6_fifo_vs_tiresias.png]
"""
import json
import os
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "fig6"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, PREFIX, "fig6_fifo_vs_tiresias.png")
OFF = os.path.join(HERE, "fig6_offline")
START, END = 3000, 4000
LOADS = list(range(1, 10))

# colour follows the scheduler (categorical slots 1 and 2 of the reference
# palette, validated for the light surface); line style follows the source.
COLOR = {"Tiresias": "#2a78d6", "Fifo": "#eb6834"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
FIG = {"job_stats": "Fig6 JCT", "responsivness": "Fig7 Resp"}
TITLE = {"job_stats": "Average JCT", "responsivness": "Average responsiveness"}


def read(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return {int(k): v[1] - v[0] for k, v in json.load(f).items()}


def ours(s, lo, kind):
    b = read(os.path.join(HERE, PREFIX, f"{s}_load_{lo}",
                          f"{PREFIX}_{START}_{END}_{s}_AcceptAll_load_{lo}.0_{kind}.json"))
    src = "blox"
    if b is None:
        b, src = read(os.path.join(OFF, f"{s}_load_{lo}_{kind}.json")), "offline"
    return (statistics.mean(b.values()) if b else None), src


with open(os.path.join(HERE, "paper_fig6_fig7_points.json")) as f:
    raw = json.load(f)
paper = {
    (fig, s): {round(x): y for x, y in pts if abs(x - round(x)) < 0.1 and 1 <= round(x) <= 9}
    for fig, series in raw.items() for s, pts in series.items()
}

plt.rcParams.update({
    "font.size": 10, "text.color": INK, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.edgecolor": GRID,
})
fig, axes = plt.subplots(1, 2, figsize=(11, 4), facecolor=SURFACE)
offline_used = False
for ax, kind in zip(axes, FIG):
    ax.set_facecolor(SURFACE)
    for s in ("Tiresias", "Fifo"):
        ys, srcs = zip(*(ours(s, lo, kind) for lo in LOADS))
        offline_used |= "offline" in srcs
        name = "LAS (Tiresias)" if s == "Tiresias" else "FIFO"
        ax.plot(LOADS, [y / 3600 for y in ys], color=COLOR[s], lw=2, marker="o", ms=6,
                label=f"{name}, this repo")
        px = sorted(paper[(FIG[kind], s)])
        ax.plot(px, [paper[(FIG[kind], s)][x] / 3600 for x in px], color=COLOR[s], lw=0,
                marker="o", ms=11, mfc="none", mew=1.6, label=f"{name}, paper Fig {6 if kind == 'job_stats' else 7}")
    ax.set_title(f"{TITLE[kind]}, Philly trace, 128 GPUs", loc="left", color=INK)
    ax.set_xlabel("jobs per hour")
    ax.set_ylabel("hours")
    ax.set_xticks(LOADS)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
note = "Hollow circles: points read from the paper's PDF. Filled: the shipped Blox code, jobs 3000-4000 tracked"
note += ", offline model where Blox had not finished." if offline_used else "."
fig.text(0.01, 0.005, note, color=INK2, fontsize=8)
fig.tight_layout(rect=(0, 0.04, 1, 1))
os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
fig.savefig(OUT, dpi=160, facecolor=SURFACE)
print(f"wrote {OUT}" + (" (some points from the offline model)" if offline_used else ""))
