"""
Cross-check temporal: tree cover loss vs tanggal IUP terbit.

Hipotesis: Apakah loss accelerate setelah izin terbit?
Untuk setiap WIUP, hitung:
  - loss_pre_iup_ha   : loss tahun-tahun SEBELUM tgl_berlak
  - loss_post_iup_ha  : loss tahun-tahun SETELAH tgl_berlak
  - loss_rate_pre     : ha/tahun sebelum IUP
  - loss_rate_post    : ha/tahun setelah IUP
  - ratio             : post/pre (>1 = accelerated post-IUP)

Input dari GeoJSON (tgl_berlak) + batch CSV (loss per tahun).

Usage:
    python temporal_iup.py
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

YEARS = list(range(2001, 2026))


def to_year(val) -> int | None:
    """Extract a 4-digit year from either format the Geoportal layers use:
    - epoch milliseconds (int/float) — layer Join_WIUP_vs_IPPKH
    - ISO date string 'YYYY-MM-DD'    — layer WIUP_Publish (rescrape bundle)
    Returns None if unparseable.
    """
    if val is None or val == "":
        return None
    # ISO date string?
    if isinstance(val, str) and "-" in val:
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d").year
        except ValueError:
            return None
    # else assume epoch milliseconds
    try:
        ms = float(val)
    except (TypeError, ValueError):
        return None
    if ms < 0 or ms > 5e12:  # sanity bounds (~year 2128)
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path,
                        default=Path("data/analysis/batch_KALIMANTAN_t30_wide.csv"))
    parser.add_argument("--geojson", type=Path,
                        default=Path("data/wiup/kalimantan_unique.geojson"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/analysis/temporal_iup_analysis.csv"))
    args = parser.parse_args()

    # Load IUP dates from GeoJSON
    gj = json.loads(args.geojson.read_text())
    iup_dates = {}
    for f in gj["features"]:
        p = f["properties"]
        kw = p.get("kode_wiup")
        if not kw:
            continue
        # SK validity start date. WIUP_Publish uses tgl_berlaku (ISO);
        # the older Join_WIUP_vs_IPPKH layer used tgl_berlak (epoch ms).
        year = to_year(p.get("tgl_berlaku") if p.get("tgl_berlaku") not in (None, "") else p.get("tgl_berlak"))
        iup_dates[kw] = year

    valid_dates = sum(1 for v in iup_dates.values() if v)
    print(f"Loaded {len(iup_dates)} WIUPs, {valid_dates} have valid IUP date")

    # Load batch results
    with args.batch.open() as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    has_iup = 0
    accelerated = 0
    decelerated = 0
    for r in rows:
        kw = r["kode_wiup"]
        iup_year = iup_dates.get(kw)
        if not iup_year or iup_year < 2001 or iup_year > 2025:
            # Can't compare meaningfully
            out_rows.append({**r, "iup_year": iup_year or "",
                             "loss_pre_iup_ha": "", "loss_post_iup_ha": "",
                             "n_years_pre": "", "n_years_post": "",
                             "rate_pre_ha_per_year": "",
                             "rate_post_ha_per_year": "",
                             "ratio_post_pre": "", "verdict": "no_iup_date_or_out_of_range"})
            continue
        has_iup += 1

        # Pre-IUP = tahun < iup_year, Post-IUP = tahun >= iup_year
        pre = sum(float(r[f"loss_{y}_ha"]) for y in YEARS if y < iup_year)
        post = sum(float(r[f"loss_{y}_ha"]) for y in YEARS if y >= iup_year)
        n_pre = sum(1 for y in YEARS if y < iup_year)
        n_post = sum(1 for y in YEARS if y >= iup_year)
        rate_pre = pre / n_pre if n_pre > 0 else 0
        rate_post = post / n_post if n_post > 0 else 0
        ratio = rate_post / rate_pre if rate_pre > 0 else float("inf") if rate_post > 0 else 0

        if rate_pre == 0 and rate_post > 0:
            verdict = "loss_only_after_iup"
            accelerated += 1
        elif ratio > 1.5:
            verdict = "accelerated_post_iup"
            accelerated += 1
        elif ratio < 0.67 and ratio > 0:
            verdict = "decelerated_post_iup"
            decelerated += 1
        elif ratio == 0:
            verdict = "no_loss_either"
        else:
            verdict = "stable"

        out_rows.append({**r, "iup_year": iup_year,
                         "loss_pre_iup_ha": round(pre, 2),
                         "loss_post_iup_ha": round(post, 2),
                         "n_years_pre": n_pre,
                         "n_years_post": n_post,
                         "rate_pre_ha_per_year": round(rate_pre, 2),
                         "rate_post_ha_per_year": round(rate_post, 2),
                         "ratio_post_pre": (round(ratio, 2) if ratio != float("inf")
                                            else "inf"),
                         "verdict": verdict})

    # Write
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) + ["iup_year", "loss_pre_iup_ha",
                                      "loss_post_iup_ha", "n_years_pre",
                                      "n_years_post", "rate_pre_ha_per_year",
                                      "rate_post_ha_per_year", "ratio_post_pre",
                                      "verdict"]
    with args.output.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Saved → {args.output}")

    # Summary
    print(f"\n{'='*55}")
    print(f"  TEMPORAL ANALYSIS — Loss vs IUP issuance")
    print(f"{'='*55}")
    print(f"  WIUPs total                  : {len(rows)}")
    print(f"  With valid IUP year (2001-25): {has_iup}")
    print(f"\n  Verdict distribution:")
    from collections import Counter
    verdicts = Counter(r["verdict"] for r in out_rows)
    for v, c in verdicts.most_common():
        print(f"    {v:30s} {c:5d}")

    # Aggregate rate comparison
    valid = [r for r in out_rows if r["verdict"] not in
             ("no_iup_date_or_out_of_range", "no_loss_either")]
    if valid:
        total_pre = sum(float(r["loss_pre_iup_ha"]) for r in valid)
        total_post = sum(float(r["loss_post_iup_ha"]) for r in valid)
        avg_rate_pre = sum(float(r["rate_pre_ha_per_year"]) for r in valid)/len(valid)
        avg_rate_post = sum(float(r["rate_post_ha_per_year"]) for r in valid)/len(valid)
        print(f"\n  Aggregate (over {len(valid)} WIUPs with loss & IUP date):")
        print(f"    Total loss PRE-IUP  : {total_pre:>12,.0f} ha")
        print(f"    Total loss POST-IUP : {total_post:>12,.0f} ha")
        print(f"    Mean rate PRE  : {avg_rate_pre:>9,.1f} ha/year/WIUP")
        print(f"    Mean rate POST : {avg_rate_post:>9,.1f} ha/year/WIUP")
        if avg_rate_pre > 0:
            print(f"    Ratio POST/PRE : {avg_rate_post/avg_rate_pre:>9.2f}x "
                  f"({'accelerated' if avg_rate_post/avg_rate_pre > 1 else 'stable'})")

    # Top examples of accelerated cases
    accel = [r for r in out_rows if r["verdict"] == "accelerated_post_iup"
             and float(r["loss_post_iup_ha"]) > 1000]
    accel.sort(key=lambda r: -float(r["loss_post_iup_ha"]))
    print(f"\n  Top 10 'accelerated post-IUP' (>1000ha post-loss):")
    print(f"  {'#':<3} {'Perusahaan':<28} {'IUP':<6} {'Pre/y':>6} {'Post/y':>7} {'Ratio':>6}")
    for i, r in enumerate(accel[:10], 1):
        nu = (r["nama_usaha"] or "")[:28]
        print(f"  {i:<3} {nu:<28} {r['iup_year']:<6} "
              f"{float(r['rate_pre_ha_per_year']):>6.0f} "
              f"{float(r['rate_post_ha_per_year']):>7.0f} "
              f"{r['ratio_post_pre']!s:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
