"""
Summarize a Fig 12 run against the paper.

usage: summarize_fig12.py <exp_prefix> [start_job=3000] [end_job=4000]

JCT = finish - submit; responsiveness = first round run - submit
(blox_manager.py update_metrics). Paper targets read off Fig 12 (arXiv
2312.12621, p.9) by pixel; Accept 1.2x JCT is approximate, the legend
overlaps that bar.
"""
import json
import os
import statistics
import sys

PREFIX = sys.argv[1]
START = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
END = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), PREFIX)

# policy: (avg JCT s, avg responsiveness s)
PAPER = {
    "AcceptAll": (188_500, 450),
    "LoadBasedAccept-1.5x": (179_600, 32_100),
    "LoadBasedAccept-1.2x": (159_000, 69_900),
    "LoadBasedAccept-1.0x": (131_100, 74_700),
}


def load(policy, kind):
    path = os.path.join(
        RUN_DIR, policy, f"{PREFIX}_{START}_{END}_Las_{policy}_load_8.0_{kind}.json"
    )
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return {int(k): v[1] - v[0] for k, v in json.load(f).items()}


rows = []
for policy, (p_jct, p_resp) in PAPER.items():
    jct = load(policy, "job_stats")
    resp = load(policy, "responsivness")
    if jct is None:
        rows.append((policy, None))
        continue
    rows.append(
        (
            policy,
            dict(
                n=len(jct),
                n_resp=len(resp),
                jct=statistics.mean(jct.values()),
                jct_med=statistics.median(jct.values()),
                resp=statistics.mean(resp.values()),
                p_jct=p_jct,
                p_resp=p_resp,
            ),
        )
    )

base = next((r for p, r in rows if p == "AcceptAll" and r), None)
hdr = f"{'policy':<22}{'jobs':>6}{'avg JCT':>11}{'paper':>9}{'dev':>7}{'vs AA':>8}{'paper':>7}  {'avg resp':>9}{'paper':>8}"
print(f"run '{PREFIX}', jobs {START}-{END}")
print(hdr)
print("-" * len(hdr))
for policy, r in rows:
    if r is None:
        print(f"{policy:<22}  (no output yet)")
        continue
    dev = (r["jct"] - r["p_jct"]) / r["p_jct"] * 100
    vs_aa = f"{(r['jct'] / base['jct'] - 1) * 100:+.1f}%" if base else "-"
    p_vs_aa = f"{(r['p_jct'] / PAPER['AcceptAll'][0] - 1) * 100:+.1f}%"
    print(
        f"{policy:<22}{r['n']:>6}{r['jct']:>11,.0f}{r['p_jct']:>9,}{dev:>+6.0f}%"
        f"{vs_aa:>8}{p_vs_aa:>7}  {r['resp']:>9,.0f}{r['p_resp']:>8,}"
    )
