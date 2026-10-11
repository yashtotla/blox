"""
Read the data points of Figures 6 and 7 (FIFO / Tiresias / Optimus avg JCT and
avg responsiveness vs. jobs per hour) out of the EuroSys '24 Blox PDF.

The figures are vector graphics: each data point is a small filled marker in
the series colour. This maps every marker's centre through the tick-label
positions on page 6 (x labels "1".."9", y labels "0" and "300000" / "200000")
and writes repro_runs/paper_fig6_fig7_points.json, which
compare_fig6_paper.py and plot_fig6.py read. Expect ~0.05% of full scale of
mapping error (the tick labels are centred under their ticks to within a few
hundredths of a point).

Needs PyMuPDF, which is not in the project venv:
    uv run --with pymupdf python repro_runs/extract_paper_points.py "/path/to/2024 EuroSYS - Blox.pdf"
"""
import json
import os
import sys

import pymupdf

PDF = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "paper_fig6_fig7_points.json"
)
page = pymupdf.open(PDF)[5]  # page 6 of the PDF

# Series colours as matplotlib wrote them (RGB in 0..1).
COL = {"Tiresias": (0.839, 0.153, 0.157), "Optimus": (0.0, 0.0, 0.502), "Fifo": (0.0, 0.502, 0.502)}


def series(color):
    if color is None:
        return None
    for name, ref in COL.items():
        if all(abs(a - b) < 0.01 for a, b in zip(color, ref)):
            return name


def centre(bbox):
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


# Tick labels: the x labels "1".."9" sit on the same columns in both figures;
# the y labels differ. Figure 6 is the upper one on the page.
labels = []
for b in page.get_text("dict")["blocks"]:
    for ln in b.get("lines", []):
        for sp in ln["spans"]:
            labels.append((sp["text"].strip(), sp["bbox"]))


def label_x(text, y_lo, y_hi):
    for t, bb in labels:
        if t == text and y_lo < bb[1] < y_hi and bb[0] > 330:
            return centre(bb)[0]
    raise KeyError(text)


def label_y(text, y_lo, y_hi):
    for t, bb in labels:
        if t == text and y_lo < bb[1] < y_hi and bb[0] > 330:
            return centre(bb)[1]
    raise KeyError(text)


FIGS = {
    # name: (region of the figure on the page, y label at 0, y label at full scale, full-scale value)
    "Fig6 JCT": ((180, 262), "0", "300000", 300000),
    "Fig7 Resp": ((295, 375), "0", "200000", 200000),
}
out = {}
for name, ((y_lo, y_hi), zero, top, top_val) in FIGS.items():
    x1, x9 = label_x("1", y_lo, y_hi), label_x("9", y_lo, y_hi)
    y0, ytop = label_y(zero, y_lo, y_hi), label_y(top, y_lo, y_hi)
    pts = {k: [] for k in COL}
    for d in page.get_drawings():
        s = series(d.get("fill"))
        r = d["rect"]
        # data markers: filled, thin outline, inside the plot area of this figure
        if s and d.get("width") and abs(d["width"] - 0.333) < 0.01 and r.x0 > 355 and y_lo < r.y0 < y_hi:
            cx, cy = centre((r.x0, r.y0, r.x1, r.y1))
            x = 1 + (cx - x1) / (x9 - x1) * 8
            y = (y0 - cy) / (y0 - ytop) * top_val
            pts[s].append((round(x, 2), round(y)))
    out[name] = {k: sorted(v) for k, v in pts.items()}
    print(name)
    for k, v in out[name].items():
        # the legend's marker shows up at a non-integer x; the readers drop it
        print(f"  {k:9}", " ".join(f"{x:.0f}:{y:>7}" for x, y in v if abs(x - round(x)) < 0.1))

with open(OUT, "w") as f:
    json.dump(out, f, indent=1)
print(f"wrote {OUT}")
