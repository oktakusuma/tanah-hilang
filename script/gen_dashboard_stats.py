#!/usr/bin/env python3
"""Generate ONE source of truth for every hardcoded number in the dashboard.

Queries the built SQLite DBs and writes webapp/src/generated/dashboard-stats.json.
The frontend imports that JSON for all narrative figures (Metodologi, StoryIntro,
LoginPage, …) so a data refresh = re-run this script → every page updates.
Pages that already read the live /api are unaffected.

Run after the pipeline (build_combined_db → filter_minerba):
  python scripts/gen_dashboard_stats.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Sibling-script import (pola sama dgn gen_descals_tiles.py -> attribution_sawit):
# satu-satunya sumber definisi to_periode() -- JANGAN duplikasi jadi CASE WHEN SQL
# di sini, itu jebakan lama (iup_year kosong/di luar jendela 1998-2025 -> harus
# None, bukan diam-diam jatuh ke P3).
from build_periode_tables import to_periode  # noqa: E402


def snapshot(db_path: Path) -> dict:
    """Aggregate figures for one DB (default-minerba or full)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    def one(sql):
        return conn.execute(sql).fetchone()[0]

    wiup = one("SELECT COUNT(*) FROM wiup_master")
    loss = one("SELECT COALESCE(SUM(total_loss_ha),0) FROM wiup_master")
    forest = one("SELECT COALESCE(SUM(forest_2000_ha),0) FROM wiup_master")
    matched = one("SELECT COUNT(*) FROM wiup_match WHERE match_strategy IS NOT NULL")

    per_komoditas = [
        {"nama": r["komoditas"], "n": r["n"], "loss_ha": round(r["loss"] or 0)}
        for r in conn.execute(
            "SELECT komoditas, COUNT(*) n, SUM(total_loss_ha) loss "
            "FROM wiup_master GROUP BY komoditas ORDER BY n DESC")
    ]
    per_provinsi = [
        {"nama": r["nama_prov"], "n": r["n"], "loss_ha": round(r["loss"] or 0)}
        for r in conn.execute(
            "SELECT nama_prov, COUNT(*) n, SUM(total_loss_ha) loss "
            "FROM wiup_master GROUP BY nama_prov ORDER BY loss DESC")
    ]
    match_strategy = {
        (r["s"] or "unmatched"): r["n"]
        for r in conn.execute(
            "SELECT match_strategy s, COUNT(*) n FROM wiup_match GROUP BY match_strategy")
    }
    temporal = {
        r["v"]: r["n"]
        for r in conn.execute(
            "SELECT temporal_verdict v, COUNT(*) n FROM wiup_master "
            "WHERE temporal_verdict IS NOT NULL GROUP BY temporal_verdict")
    }
    conn.close()

    return {
        "wiup": wiup,
        "loss_ha": round(loss),
        "forest_2000_ha": round(forest),
        "loss_pct_forest": round(100.0 * loss / forest, 1) if forest else 0.0,
        "matched": matched,
        "unmatched": wiup - matched,
        "match_pct": round(100.0 * matched / wiup, 1) if wiup else 0.0,
        "n_provinsi": len(per_provinsi),
        "n_komoditas": len(per_komoditas),
        "per_komoditas": per_komoditas,
        "per_provinsi": per_provinsi,
        "match_strategy": match_strategy,
        "temporal": temporal,
    }


def periode(db_path: Path) -> list[dict] | None:
    """Ringkasan 3 periode (+ Pra-2009) dari tabel periode_ringkasan × periode_slope.

    Sumber & metode: scripts/build_periode_tables.py (provenance: analysis_meta).
    None bila tabel belum dibangun (pipeline lama) — frontend wajib toleran.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT r.periode, r.rentang_tahun, r.n, r.luas_total_ha, r.luas_median_ha, "
            "       r.loss_total_ha, r.pct_poligon, r.pct_akselerasi, r.r_luas_loss, "
            "       s.slope_ha_per_year, s.peak_year "
            "FROM periode_ringkasan r LEFT JOIN periode_slope s ON s.periode = r.periode "
            "ORDER BY CASE r.periode WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return None
    conn.close()
    return [
        {
            "periode": r["periode"],
            "rentang_tahun": r["rentang_tahun"],
            "n": r["n"],
            "luas_total_ha": round(r["luas_total_ha"]),
            "luas_median_ha": round(r["luas_median_ha"]),
            "loss_total_ha": round(r["loss_total_ha"]),
            "pct_poligon": r["pct_poligon"],
            "pct_akselerasi": r["pct_akselerasi"],
            "r_luas_loss": r["r_luas_loss"],
            "slope_ha_per_year": r["slope_ha_per_year"],
            "peak_year": r["peak_year"],
            "is_footnote": r["periode"] == "Pra-2009",
        }
        for r in rows
    ]


def lapisan(db_path: Path) -> dict | None:
    """Blok atribusi sawit (Descals) × klasifikasi izin (perpanjangan).

    Sumber: tabel atribusi_sawit (scripts/attribution_sawit.py) & klasifikasi_izin
    (scripts/klasifikasi_perpanjangan.py). None bila KEDUA tabel kosong — frontend
    wajib toleran (sembunyikan blok). Bila hanya salah satu terisi, kunci dari
    tabel yang kosong tetap muncul dengan nilai None/{} — UI menyembunyikan bagian itu.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    n_atribusi = conn.execute("SELECT COUNT(*) FROM atribusi_sawit").fetchone()[0]
    n_klasifikasi = conn.execute("SELECT COUNT(*) FROM klasifikasi_izin").fetchone()[0]
    if n_atribusi == 0 and n_klasifikasi == 0:
        conn.close()
        return None

    out: dict = {}
    if n_atribusi:
        row = conn.execute(
            "SELECT SUM(loss_2001_2021_ha) a, SUM(loss_sawit_tol2th_ha) b, "
            "       SUM(loss_sawit_jeda5th_ha) c, SUM(loss_sawit_tahunsama_ha) d, "
            "       SUM(loss_2022_2025_ha) e "
            "FROM atribusi_sawit"
        ).fetchone()
        loss_2001_2021 = row["a"]
        loss_tol2th = row["b"]
        out["loss_2001_2021_ha"] = loss_2001_2021
        out["loss_sawit_tol2th_ha"] = loss_tol2th
        out["loss_sawit_jeda5th_ha"] = row["c"]
        out["loss_sawit_tahunsama_ha"] = row["d"]
        out["loss_2022_2025_ha"] = row["e"]
        if loss_2001_2021 is not None and loss_tol2th is not None:
            out["loss_bersih_ha"] = loss_2001_2021 - loss_tol2th
            out["persen_sawit"] = (
                round(100.0 * loss_tol2th / loss_2001_2021, 1) if loss_2001_2021 else None
            )
        else:
            out["loss_bersih_ha"] = None
            out["persen_sawit"] = None
    else:
        out["loss_2001_2021_ha"] = None
        out["loss_sawit_tol2th_ha"] = None
        out["loss_sawit_jeda5th_ha"] = None
        out["loss_sawit_tahunsama_ha"] = None
        out["loss_2022_2025_ha"] = None
        out["loss_bersih_ha"] = None
        out["persen_sawit"] = None

    out["n_kelas"] = {
        r["kelas"]: r["n"]
        for r in conn.execute(
            "SELECT kelas, COUNT(*) n FROM klasifikasi_izin GROUP BY kelas")
    }
    out["n_bukti_kuat"] = conn.execute(
        "SELECT COUNT(*) FROM klasifikasi_izin WHERE bukti = 'KUAT'").fetchone()[0]

    # Pangsa "diduga perpanjangan" per periode kewenangan — dihitung di sini (bukan
    # ditulis tangan di frontend/docstring) supaya selalu sinkron dgn DB. Periode
    # via to_periode() (import, BUKAN CASE WHEN SQL duplikat) dari build_periode_tables,
    # sumber tunggal definisi P1/P2/P3/Pra-2009 & jendela iup_year 1998-2025.
    # Pra-2009 sengaja DIKECUALIKAN (catatan kaki di kerangka 3-periode, konsisten dgn
    # periode_ringkasan) -- hanya P1/P2/P3 yang dilaporkan.
    periode_n: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        "SELECT z.kelas kelas, g.iup_year iup_year FROM klasifikasi_izin z "
        "JOIN wiup_geoportal g ON g.kode_wiup = z.kode_wiup"
    ):
        p = to_periode(r["iup_year"])
        if p is None or p == "Pra-2009":
            continue
        d = periode_n.setdefault(p, {"total": 0, "perpanjangan": 0})
        d["total"] += 1
        if r["kelas"] == "PERPANJANGAN":
            d["perpanjangan"] += 1
    out["pangsa_perpanjangan_periode"] = {
        p: round(100.0 * d["perpanjangan"] / d["total"], 1)
        for p, d in periode_n.items()
        if d["total"] > 0
    }

    conn.close()
    out["tile_descals"] = (db_path.resolve().parent.parent / "data" / "tiles" / "descals").is_dir()
    return out


def registry(db_path: Path) -> dict:
    """Company-registry figures (shared, same in both DBs) + snapshot struktur
    DB (jumlah tabel/view/indeks, ukuran berkas) — dipakai §05 Metodologi
    ("Dari basis data ke dashboard"). Snapshot ini BERUBAH kalau objek DB
    berubah (tabel ditambah/dihapus, indeks baru) — makanya dihitung di sini,
    bukan ditulis literal di komponen React (F17a r1: '28 tabel · 40 indeks'
    basi setelah drop exposure_kabupaten, dan hitungan indeks ternyata sudah
    lama salah).
    """
    conn = sqlite3.connect(db_path)
    bu = conn.execute("SELECT COUNT(*) FROM badan_usaha").fetchone()[0]
    izin = conn.execute("SELECT COUNT(*) FROM perizinan").fetchone()[0]
    n_tabel = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    n_view = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    n_indeks = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    conn.close()
    db_size_mb = round(db_path.stat().st_size / (1024 * 1024))
    return {
        "badan_usaha": bu, "perizinan": izin,
        "n_tabel": n_tabel, "n_view": n_view, "n_indeks": n_indeks,
        "db_size_mb": db_size_mb,
    }


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--default-db", type=Path, default=root / "data" / "kalimantan.db")
    ap.add_argument("--full-db", type=Path, default=root / "data-full" / "kalimantan.db")
    ap.add_argument("--out", type=Path,
                    default=root / "webapp" / "src" / "generated" / "dashboard-stats.json")
    args = ap.parse_args()

    out = {
        "_comment": "AUTO-GENERATED oleh scripts/gen_dashboard_stats.py — JANGAN edit manual. "
                    "Jalankan ulang setelah refresh data.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "period": "2001-2025",
        "default": snapshot(args.default_db),         # minerba (batubara + logam)
        "registry": registry(args.default_db),        # badan_usaha / perizinan (utuh)
    }
    per = periode(args.default_db)                    # 3 periode kewenangan (+Pra-2009)
    if per:
        out["periode"] = per
    lap = lapisan(args.default_db)                     # atribusi sawit × klasifikasi izin
    if lap is not None:
        out["lapisan"] = lap
    if args.full_db.exists():
        out["full"] = snapshot(args.full_db)          # minerba + galian C

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    d = out["default"]
    print(f"Wrote {args.out}")
    print(f"  default(minerba): {d['wiup']} WIUP · {d['loss_ha']:,} ha loss · "
          f"{d['loss_pct_forest']}% · match {d['matched']}/{d['wiup']} ({d['match_pct']}%)")
    if "full" in out:
        print(f"  full: {out['full']['wiup']} WIUP · {out['full']['loss_ha']:,} ha loss")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
