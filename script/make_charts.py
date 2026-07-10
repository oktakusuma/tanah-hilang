"""
Generate matplotlib charts untuk thesis dari batch results.

Outputs (di data/figures/):
  - fig_annual_loss.png         : bar chart Kalimantan total per tahun
  - fig_loss_by_provinsi.png    : stacked bar per provinsi per tahun
  - fig_loss_by_komoditas.png   : bar komoditas (top + others)
  - fig_top_konsesi.png         : horizontal bar top 15 WIUPs
  - fig_loss_pct_distribution.png: histogram distribusi loss %

Usage:
    python make_charts.py --input data/analysis/batch_KALIMANTAN_t30_wide.csv
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no GUI
import matplotlib.pyplot as plt

YEARS = list(range(2001, 2026))
PALETTE_PROV = {
    "KALIMANTAN TIMUR": "#d62728",
    "KALIMANTAN BARAT": "#1f77b4",
    "KALIMANTAN TENGAH": "#2ca02c",
    "KALIMANTAN SELATAN": "#ff7f0e",
    "KALIMANTAN UTARA": "#9467bd",
}
PALETTE_KOMO = {
    "BATUBARA": "#5C4033",
    "BAUKSIT": "#C49102",
    "EMAS": "#FFD700",
    "EMAS DMP": "#DAA520",
    "BIJIH BESI": "#8B4513",
    "ZIRKON": "#4682B4",
    "TIMAH": "#A9A9A9",
    "BESI": "#8B0000",
}


def load_rows(path):
    rows = list(csv.DictReader(path.open()))
    for r in rows:
        for k in ("polygon_area_ha", "forest_2000_ha", "total_loss_ha",
                  "loss_pct_of_forest", "loss_pct_of_polygon"):
            r[k] = float(r.get(k) or 0)
        for y in YEARS:
            r[f"loss_{y}_ha"] = float(r.get(f"loss_{y}_ha") or 0)
    # Map kode_wiup → nama_prov from source GeoJSON
    gj = json.loads(Path("data/wiup/kalimantan_unique.geojson").read_text())
    w2p = {f["properties"]["kode_wiup"]: f["properties"]["nama_prov"]
           for f in gj["features"]}
    for r in rows:
        r["nama_prov_simple"] = (w2p.get(r["kode_wiup"], "?") or "?").split(",")[0].strip()
    return rows


def fig_annual_loss(rows, outpath):
    """Bar chart of total loss per year."""
    annual = {y: sum(r[f"loss_{y}_ha"] for r in rows) for y in YEARS}
    fig, ax = plt.subplots(figsize=(11, 5))
    bars = ax.bar(annual.keys(), [v/1000 for v in annual.values()],
                  color="#c44e3e", edgecolor="white")
    # highlight peaks (top 3 years)
    sorted_years = sorted(annual.items(), key=lambda x: -x[1])[:3]
    peak_years = {y for y, _ in sorted_years}
    for bar, (y, v) in zip(bars, annual.items()):
        if y in peak_years:
            bar.set_color("#8B0000")
            ax.annotate(f"{v/1000:.1f}", (y, v/1000), ha="center",
                        va="bottom", fontsize=8, fontweight="bold")
    ax.set_xlabel("Tahun")
    ax.set_ylabel("Tree cover loss (kilo-hektar)")
    ax.set_title("Annual Tree Cover Loss dalam Konsesi Tambang Kalimantan\n"
                 "(824 WIUPs · Hansen GFC v1.13 · canopy threshold 30%)",
                 fontsize=11)
    ax.set_xticks(YEARS[::2])
    ax.grid(axis="y", alpha=0.3)
    total = sum(annual.values())
    ax.text(0.02, 0.95, f"Total 2001-2025: {total:,.0f} ha\n"
            f"Mean: {total/25:,.0f} ha/year",
            transform=ax.transAxes, va="top", fontsize=9,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))
    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close()


def fig_loss_by_provinsi(rows, outpath):
    """Stacked bar per provinsi per year."""
    provs = list(PALETTE_PROV.keys())
    data = {p: [0]*len(YEARS) for p in provs}
    for r in rows:
        p = r["nama_prov_simple"]
        if p not in data:
            continue
        for i, y in enumerate(YEARS):
            data[p][i] += r[f"loss_{y}_ha"]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    bottom = [0]*len(YEARS)
    for p in provs:
        vals = [v/1000 for v in data[p]]
        ax.bar(YEARS, vals, bottom=bottom, label=p.replace("KALIMANTAN ", "Kal-"),
               color=PALETTE_PROV[p], edgecolor="white", linewidth=0.5)
        bottom = [b+v for b, v in zip(bottom, vals)]
    ax.set_xlabel("Tahun")
    ax.set_ylabel("Tree cover loss (kilo-hektar)")
    ax.set_title("Tree Cover Loss per Provinsi per Tahun (2001-2025)", fontsize=11)
    ax.set_xticks(YEARS[::2])
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close()


def fig_loss_by_komoditas(rows, outpath):
    """Bar chart by commodity (top 8 + others)."""
    totals = defaultdict(float)
    forest = defaultdict(float)
    counts = defaultdict(int)
    for r in rows:
        k = r["komoditas"]
        totals[k] += r["total_loss_ha"]
        forest[k] += r["forest_2000_ha"]
        counts[k] += 1
    items = sorted(totals.items(), key=lambda x: -x[1])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    labels = [k for k, _ in items]
    values = [totals[k]/1000 for k in labels]
    pcts = [100*totals[k]/forest[k] if forest[k] else 0 for k in labels]
    colors = [PALETTE_KOMO.get(k, "#888888") for k in labels]

    # Left: absolute
    ax1.barh(labels[::-1], values[::-1], color=colors[::-1])
    ax1.set_xlabel("Total loss (kilo-hektar)")
    ax1.set_title("Absolute Tree Cover Loss")
    for i, v in enumerate(values[::-1]):
        ax1.text(v, i, f" {v:,.0f}", va="center", fontsize=8)
    ax1.grid(axis="x", alpha=0.3)

    # Right: % of forest baseline
    ax2.barh(labels[::-1], pcts[::-1], color=colors[::-1])
    ax2.set_xlabel("Loss (% of forest 2000)")
    ax2.set_title("Loss Intensity (% of baseline forest)")
    for i, v in enumerate(pcts[::-1]):
        n = counts[labels[::-1][i]]
        ax2.text(v, i, f"  {v:.1f}% (n={n})", va="center", fontsize=8)
    ax2.set_xlim(0, max(pcts)*1.25)
    ax2.grid(axis="x", alpha=0.3)

    fig.suptitle("Tree Cover Loss per Komoditas — Kalimantan 2001-2025", fontsize=12)
    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close()


def fig_top_konsesi(rows, outpath, top=15):
    """Horizontal bar chart top WIUPs."""
    sorted_rows = sorted(rows, key=lambda r: -r["total_loss_ha"])[:top]
    labels = [(r["nama_usaha"] or "?")[:32] for r in sorted_rows]
    values = [r["total_loss_ha"]/1000 for r in sorted_rows]
    pcts = [r["loss_pct_of_forest"] for r in sorted_rows]
    komos = [r["komoditas"] for r in sorted_rows]
    colors = [PALETTE_KOMO.get(k, "#888888") for k in komos]

    fig, ax = plt.subplots(figsize=(11, 7))
    bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1])
    for i, (v, p) in enumerate(zip(values[::-1], pcts[::-1])):
        ax.text(v, i, f"  {v:,.1f}k ha ({p:.0f}%)", va="center", fontsize=8)
    ax.set_xlabel("Tree cover loss (kilo-hektar)")
    ax.set_title(f"Top {top} Konsesi by Absolute Tree Cover Loss (2001-2025)",
                 fontsize=11)
    ax.set_xlim(0, max(values)*1.25)
    ax.grid(axis="x", alpha=0.3)
    # Legend for commodities
    seen = []
    handles = []
    for k, c in zip(komos, colors):
        if k not in seen:
            seen.append(k)
            handles.append(plt.Rectangle((0, 0), 1, 1, color=c, label=k))
    ax.legend(handles=handles, loc="lower right", fontsize=8)
    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close()


def fig_loss_pct_distribution(rows, outpath):
    """Histogram: distribution of loss % across concessions."""
    pcts = [r["loss_pct_of_forest"] for r in rows
            if r["forest_2000_ha"] > 100]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(pcts, bins=30, color="#c44e3e", edgecolor="white")
    ax.set_xlabel("Loss as % of forest 2000")
    ax.set_ylabel("Number of WIUPs")
    ax.set_title(f"Distribution of Forest Loss Intensity\n"
                 f"({len(pcts)} konsesi dengan forest baseline >100 ha)",
                 fontsize=11)
    # Vertical line for mean
    import statistics as stats
    mean_pct = stats.mean(pcts)
    median_pct = stats.median(pcts)
    ax.axvline(mean_pct, color="black", linestyle="--", label=f"Mean = {mean_pct:.1f}%")
    ax.axvline(median_pct, color="darkblue", linestyle=":",
               label=f"Median = {median_pct:.1f}%")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("data/figures"))
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.input)
    print(f"Loaded {len(rows)} WIUPs")

    figs = [
        ("fig_annual_loss.png", fig_annual_loss),
        ("fig_loss_by_provinsi.png", fig_loss_by_provinsi),
        ("fig_loss_by_komoditas.png", fig_loss_by_komoditas),
        ("fig_top_konsesi.png", fig_top_konsesi),
        ("fig_loss_pct_distribution.png", fig_loss_pct_distribution),
    ]
    for name, fn in figs:
        path = args.outdir / name
        fn(rows, path)
        print(f"  ✅ {path}")

    print(f"\nAll charts saved in {args.outdir}/")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
