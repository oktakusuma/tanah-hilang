# STATUS  : ARSIP — perhitungan laju pra/pasca izin v2 (menulis data/analysis/temporal_iup_analysis.csv, dua jendela 2025 & 2024)
# CATATAN : logika vonis (ambang 1,5 / 0,67) DI-PORT ke pipeline/02_hansen.py -> tabel izin_laju, jendela tesis 2001-2024 saja
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""
Cross-check temporal: tree cover loss vs tanggal IUP terbit.

Hipotesis: Apakah loss accelerate setelah izin terbit?
Untuk setiap WIUP, hitung:
  - loss_2001_sampai_tahun_izin_ha   : loss tahun-tahun SEBELUM tgl_berlak
  - loss_tahun_izin_sampai_2025_ha  : loss tahun-tahun SETELAH tgl_berlak
  - loss_rate_pre     : ha/tahun sebelum IUP
  - loss_rate_post    : ha/tahun setelah IUP
  - ratio             : post/pre (>1 = accelerated post-IUP)

Sejak pivot proposal v0.3.2 (2 Sep 2026) dihitung DUA jendela sekaligus:
  - kolom *_sampai_2025 / verdict         : jendela lama 2001-2025 (ARSIP,
    dibekukan sebagai pembanding — konsumennya keluarga backtrack_*)
  - kolom *_sampai_2024 / verdict_jendela_2024 : jendela tesis 2001-2024 —
    inilah yang dibaca permukaan web inti (drawer konsesi). Konsesi dengan
    iup_year 2025 tak punya tahun pasca-izin di jendela tesis → kolom 2024
    kosong, verdict_jendela_2024 = izin_setelah_jendela_2024.

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
                             "loss_2001_sampai_tahun_izin_ha": "", "loss_tahun_izin_sampai_2025_ha": "",
                             "n_tahun_dari_2001_sampai_tahun_izin": "", "n_tahun_dari_tahun_izin_sampai_2025": "",
                             "rate_2001_sampai_tahun_izin_ha_per_year": "",
                             "rate_tahun_izin_sampai_2025_ha_per_year": "",
                             "ratio_laju_sesudah_vs_sebelum_tahun_izin": "", "verdict": "no_iup_date_or_out_of_range",
                             "loss_tahun_izin_sampai_2024_ha": "", "n_tahun_dari_tahun_izin_sampai_2024": "",
                             "rate_tahun_izin_sampai_2024_ha_per_year": "",
                             "ratio_laju_sesudah_vs_sebelum_jendela_2024": "", "verdict_jendela_2024": ""})
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

        def nilai_verdict(rp, rq, rasio):
            if rp == 0 and rq > 0:
                return "loss_only_after_iup"
            if rasio > 1.5:
                return "accelerated_post_iup"
            if 0 < rasio < 0.67:
                return "decelerated_post_iup"
            if rasio == 0:
                return "no_loss_either"
            return "stable"

        verdict = nilai_verdict(rate_pre, rate_post, ratio)
        if verdict in ("loss_only_after_iup", "accelerated_post_iup"):
            accelerated += 1
        elif verdict == "decelerated_post_iup":
            decelerated += 1

        # Jendela tesis 2001-2024 (ambang & rumus identik, hanya batas atasnya).
        if iup_year <= 2024:
            post24 = sum(float(r[f"loss_{y}_ha"]) for y in YEARS if iup_year <= y <= 2024)
            n_post24 = 2024 - iup_year + 1
            rate_post24 = post24 / n_post24 if n_post24 > 0 else 0
            ratio24 = (rate_post24 / rate_pre if rate_pre > 0
                       else float("inf") if rate_post24 > 0 else 0)
            j24 = {"loss_tahun_izin_sampai_2024_ha": round(post24, 2),
                   "n_tahun_dari_tahun_izin_sampai_2024": n_post24,
                   "rate_tahun_izin_sampai_2024_ha_per_year": round(rate_post24, 2),
                   "ratio_laju_sesudah_vs_sebelum_jendela_2024":
                       (round(ratio24, 2) if ratio24 != float("inf") else "inf"),
                   "verdict_jendela_2024": nilai_verdict(rate_pre, rate_post24, ratio24)}
        else:  # iup_year == 2025: tak ada tahun pasca-izin di jendela tesis
            j24 = {"loss_tahun_izin_sampai_2024_ha": "",
                   "n_tahun_dari_tahun_izin_sampai_2024": "",
                   "rate_tahun_izin_sampai_2024_ha_per_year": "",
                   "ratio_laju_sesudah_vs_sebelum_jendela_2024": "",
                   "verdict_jendela_2024": "izin_setelah_jendela_2024"}

        out_rows.append({**r, "iup_year": iup_year,
                         "loss_2001_sampai_tahun_izin_ha": round(pre, 2),
                         "loss_tahun_izin_sampai_2025_ha": round(post, 2),
                         "n_tahun_dari_2001_sampai_tahun_izin": n_pre,
                         "n_tahun_dari_tahun_izin_sampai_2025": n_post,
                         "rate_2001_sampai_tahun_izin_ha_per_year": round(rate_pre, 2),
                         "rate_tahun_izin_sampai_2025_ha_per_year": round(rate_post, 2),
                         "ratio_laju_sesudah_vs_sebelum_tahun_izin": (round(ratio, 2) if ratio != float("inf")
                                            else "inf"),
                         "verdict": verdict, **j24})

    # Write
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) + ["iup_year", "loss_2001_sampai_tahun_izin_ha",
                                      "loss_tahun_izin_sampai_2025_ha", "n_tahun_dari_2001_sampai_tahun_izin",
                                      "n_tahun_dari_tahun_izin_sampai_2025", "rate_2001_sampai_tahun_izin_ha_per_year",
                                      "rate_tahun_izin_sampai_2025_ha_per_year", "ratio_laju_sesudah_vs_sebelum_tahun_izin",
                                      "verdict",
                                      "loss_tahun_izin_sampai_2024_ha", "n_tahun_dari_tahun_izin_sampai_2024",
                                      "rate_tahun_izin_sampai_2024_ha_per_year",
                                      "ratio_laju_sesudah_vs_sebelum_jendela_2024", "verdict_jendela_2024"]
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
    print(f"\n  Verdict distribution (jendela tesis 2001-2024):")
    verdicts24 = Counter(r["verdict_jendela_2024"] or "(kosong)" for r in out_rows)
    for v, c in verdicts24.most_common():
        print(f"    {v:30s} {c:5d}")

    # Aggregate rate comparison
    valid = [r for r in out_rows if r["verdict"] not in
             ("no_iup_date_or_out_of_range", "no_loss_either")]
    if valid:
        total_pre = sum(float(r["loss_2001_sampai_tahun_izin_ha"]) for r in valid)
        total_post = sum(float(r["loss_tahun_izin_sampai_2025_ha"]) for r in valid)
        avg_rate_pre = sum(float(r["rate_2001_sampai_tahun_izin_ha_per_year"]) for r in valid)/len(valid)
        avg_rate_post = sum(float(r["rate_tahun_izin_sampai_2025_ha_per_year"]) for r in valid)/len(valid)
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
             and float(r["loss_tahun_izin_sampai_2025_ha"]) > 1000]
    accel.sort(key=lambda r: -float(r["loss_tahun_izin_sampai_2025_ha"]))
    print(f"\n  Top 10 'accelerated post-IUP' (>1000ha post-loss):")
    print(f"  {'#':<3} {'Perusahaan':<28} {'IUP':<6} {'Pre/y':>6} {'Post/y':>7} {'Ratio':>6}")
    for i, r in enumerate(accel[:10], 1):
        nu = (r["nama_usaha"] or "")[:28]
        print(f"  {i:<3} {nu:<28} {r['iup_year']:<6} "
              f"{float(r['rate_2001_sampai_tahun_izin_ha_per_year']):>6.0f} "
              f"{float(r['rate_tahun_izin_sampai_2025_ha_per_year']):>7.0f} "
              f"{r['ratio_laju_sesudah_vs_sebelum_tahun_izin']!s:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
