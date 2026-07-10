"""
Trend regression analysis: Mann-Kendall + linear regression untuk annual loss.

Tests:
  - Mann-Kendall trend test (non-parametric) untuk significance arah trend
  - Linear regression untuk slope (ha/year/year acceleration)
  - Sen's slope estimator (robust to outliers)

Aggregations:
  - Kalimantan total (1 series)
  - Per provinsi (5 series)
  - Per komoditas (top 6)
  - Top 10 konsesi by loss

Output:
  - data/analysis/trend_kalimantan.csv
  - data/analysis/trend_per_provinsi.csv
  - data/analysis/trend_per_komoditas.csv
  - data/figures/fig_trend_provinsi.png
"""

import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def mann_kendall(y):
    """Non-parametric trend test. Returns (S, var_S, z, p, trend_direction)."""
    n = len(y)
    if n < 4:
        return None
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += np.sign(y[j] - y[i])
    # Variance (no ties for our annual data assumption)
    var_s = n * (n - 1) * (2 * n + 5) / 18
    # Z-statistic (corrected for continuity)
    if s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)
    else:
        z = 0
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    if p < 0.05:
        trend = "increasing" if z > 0 else "decreasing"
    else:
        trend = "no_significant_trend"
    return {"s": int(s), "var_s": float(var_s), "z": float(z),
            "p_value": float(p), "trend": trend}


def sens_slope(x, y):
    """Theil-Sen estimator: robust slope."""
    n = len(x)
    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            if x[j] != x[i]:
                slopes.append((y[j] - y[i]) / (x[j] - x[i]))
    return float(np.median(slopes)) if slopes else None


def analyze_series(years, values, label=""):
    """Compute full trend analysis for one annual series."""
    x = np.array(years, dtype=float)
    y = np.array(values, dtype=float)
    n = len(y)
    if n < 4 or y.sum() == 0:
        return None

    # Mann-Kendall
    mk = mann_kendall(y)

    # OLS regression
    slope, intercept, r, p_reg, se = stats.linregress(x, y)

    # Sen's slope
    sen = sens_slope(x, y)

    # Summary stats
    mean = float(y.mean())
    total = float(y.sum())
    peak_idx = int(np.argmax(y))
    peak_year = int(years[peak_idx])
    peak_value = float(y[peak_idx])

    return {
        "label": label,
        "n_years": n,
        "total_loss_ha": total,
        "mean_annual_ha": mean,
        "peak_year": peak_year,
        "peak_value_ha": peak_value,
        # Trend
        "ols_slope_ha_per_year": float(slope),
        "ols_intercept": float(intercept),
        "ols_r_squared": float(r ** 2),
        "ols_p_value": float(p_reg),
        "sens_slope": sen,
        **{f"mk_{k}": v for k, v in mk.items()},
    }


def fetch_annual_series(conn, where_clause=""):
    """Return dict {year: total_loss_ha} for given filter."""
    q = f"""
        SELECT y.year, COALESCE(SUM(y.loss_ha), 0) AS total
        FROM wiup_loss_yearly y
        JOIN wiup_geoportal g ON g.kode_wiup = y.kode_wiup
        {where_clause}
        GROUP BY y.year
        ORDER BY y.year
    """
    return {row[0]: row[1] for row in conn.execute(q)}


def main():
    conn = sqlite3.connect("data/kalimantan.db")
    Path("data/analysis").mkdir(parents=True, exist_ok=True)
    Path("data/figures").mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  TREND REGRESSION ANALYSIS — Kalimantan 2001-2025")
    print("=" * 60)

    # --- 1. Kalimantan total ---
    series = fetch_annual_series(conn)
    years = sorted(series.keys())
    values = [series[y] for y in years]

    print("\n[1] Kalimantan total")
    result = analyze_series(years, values, "Kalimantan")
    if result:
        print(f"  Mann-Kendall trend  : {result['mk_trend']} (z={result['mk_z']:.2f}, p={result['mk_p_value']:.4f})")
        print(f"  OLS slope           : {result['ols_slope_ha_per_year']:+.0f} ha/year² "
              f"(R²={result['ols_r_squared']:.3f}, p={result['ols_p_value']:.4f})")
        print(f"  Sen's slope         : {result['sens_slope']:+.0f} ha/year²")
        print(f"  Total / Mean        : {result['total_loss_ha']:,.0f} / {result['mean_annual_ha']:,.0f} ha")
        print(f"  Peak year           : {result['peak_year']} ({result['peak_value_ha']:,.0f} ha)")

    # --- 2. Per provinsi ---
    print("\n[2] Per provinsi")
    provs = [
        "KALIMANTAN TIMUR",
        "KALIMANTAN TENGAH",
        "KALIMANTAN SELATAN",
        "KALIMANTAN BARAT",
        "KALIMANTAN UTARA",
    ]
    prov_results = []
    for prov in provs:
        s = fetch_annual_series(conn, f"WHERE g.nama_prov = '{prov}'")
        ys = sorted(s.keys())
        vs = [s[y] for y in ys]
        r = analyze_series(ys, vs, prov)
        if r:
            prov_results.append(r)
            print(f"  {prov:<22}  trend={r['mk_trend']:<26}  slope={r['ols_slope_ha_per_year']:+.0f}  "
                  f"R²={r['ols_r_squared']:.2f}")

    # --- 3. Per komoditas (top 6) ---
    print("\n[3] Per komoditas (top 6 by total loss)")
    komos = [r[0] for r in conn.execute("""
        SELECT g.komoditas
        FROM wiup_geoportal g JOIN wiup_loss l ON l.kode_wiup = g.kode_wiup
        WHERE g.komoditas IS NOT NULL
        GROUP BY g.komoditas
        ORDER BY SUM(l.total_loss_ha) DESC LIMIT 6
    """)]
    komo_results = []
    for komo in komos:
        s = fetch_annual_series(conn, f"WHERE g.komoditas = '{komo}'")
        ys = sorted(s.keys())
        vs = [s[y] for y in ys]
        r = analyze_series(ys, vs, komo)
        if r:
            komo_results.append(r)
            print(f"  {komo:<20}  trend={r['mk_trend']:<26}  slope={r['ols_slope_ha_per_year']:+.0f}  "
                  f"R²={r['ols_r_squared']:.2f}")

    # --- 4. Top 10 konsesi ---
    print("\n[4] Top 10 konsesi by total loss")
    top10 = list(conn.execute("""
        SELECT g.kode_wiup, g.nama_usaha, l.total_loss_ha
        FROM wiup_geoportal g JOIN wiup_loss l ON l.kode_wiup = g.kode_wiup
        ORDER BY l.total_loss_ha DESC LIMIT 10
    """))
    wiup_results = []
    for kode, nama, _ in top10:
        s = fetch_annual_series(conn, f"WHERE g.kode_wiup = '{kode}'")
        ys = sorted(s.keys())
        vs = [s[y] for y in ys]
        r = analyze_series(ys, vs, f"{nama} ({kode})")
        if r:
            wiup_results.append(r)
            print(f"  {nama[:30]:<30}  trend={r['mk_trend']:<26}  slope={r['ols_slope_ha_per_year']:+.0f}  "
                  f"R²={r['ols_r_squared']:.2f}")

    # --- Save CSVs ---
    def save(rows, path):
        if not rows:
            return
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()})

    kalimantan_row = analyze_series(years, values, "Kalimantan")
    save([kalimantan_row], "data/analysis/trend_kalimantan.csv")
    save(prov_results, "data/analysis/trend_per_provinsi.csv")
    save(komo_results, "data/analysis/trend_per_komoditas.csv")
    save(wiup_results, "data/analysis/trend_per_konsesi_top10.csv")
    print(f"\nSaved CSVs to data/analysis/")

    # --- Plot: per-provinsi annual trends ---
    fig, ax = plt.subplots(figsize=(12, 6))
    palette = {
        "KALIMANTAN TIMUR": "#d62728",
        "KALIMANTAN BARAT": "#1f77b4",
        "KALIMANTAN TENGAH": "#2ca02c",
        "KALIMANTAN SELATAN": "#ff7f0e",
        "KALIMANTAN UTARA": "#9467bd",
    }
    for prov in provs:
        s = fetch_annual_series(conn, f"WHERE g.nama_prov = '{prov}'")
        ys = sorted(s.keys())
        vs = [s[y] / 1000 for y in ys]
        label = prov.replace("KALIMANTAN ", "Kal-")
        ax.plot(ys, vs, marker="o", markersize=4, lw=1.5,
                color=palette.get(prov, "#666"), label=label)
        # OLS line
        slope, intercept, *_ = stats.linregress(ys, vs)
        line = [intercept + slope * y for y in ys]
        ax.plot(ys, line, "--", color=palette.get(prov, "#666"), alpha=0.4, lw=1)
    ax.set_xlabel("Tahun")
    ax.set_ylabel("Tree cover loss (kilo-hektar)")
    ax.set_title("Time Series Tree Cover Loss per Provinsi + OLS Trend Lines\n"
                 "(Kalimantan, Hansen GFC v1.13, 30% canopy threshold)",
                 fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig("data/figures/fig_trend_provinsi.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved chart: data/figures/fig_trend_provinsi.png")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
