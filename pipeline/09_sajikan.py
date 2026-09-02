#!/usr/bin/env python3
"""Langkah 09 — sajikan keluaran non-DB dari tanah-hilang.db (SKEMA.md §9).

(a) `data/wiup/kalimantan_with_loss.geojson` — himpunan minerba, satu Feature per konsesi:
    properti = semua kolom `v_konsesi` (tanpa geometri/bbox) + `hilang_YYYY_ha` 2001–2024
    dari `hansen_tahunan`; geometri dari `konsesi.geometri_geojson`. Konsumen: panduan QGIS
    (kolom `kode_wiup`, `nama_usaha`, `tahun_izin`, `hilang_2001_2024_ha`).
(b) `webapp/src/generated/dashboard-stats.json` — skema baru (tanpa kunci arsip):
    generated_at, jendela, minerba{…}, lengkap{…}|null, keyakinan{…}, umur{…}, ippkh{…},
    sankey_2001_2024{…}, registri{…}. Semua angka dibaca dari DB (lib/w4_sajikan.py) — fungsi
    yang sama dipakai 10_verifikasi.py untuk membuktikan JSON = DB.

    python pipeline/09_sajikan.py --db data/tanah-hilang.db --db-lengkap data/tanah-hilang-lengkap.db
    python pipeline/09_sajikan.py --db X --db-lengkap Y --geojson data/.bangun/uji.geojson --stats data/.bangun/uji.json

Gagal keras bila tabel hulu absen. `--db-lengkap` yang berkasnya tak ada → kunci `lengkap: null`
(dilaporkan di stdout), bukan gagal — DB kedua memang opsional bagi app.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib.db import AKAR, buka, sekarang, tandai_selesai, wajib_tabel  # noqa: E402
from pipeline.lib.w1_util import pastikan_v_konsesi  # noqa: E402  — satu sumber definisi v_konsesi (W1)
from pipeline.lib.w4_sajikan import (JENDELA, TABEL_HULU_LENGKAP, TABEL_HULU_MINERBA, blok_lengkap,  # noqa: E402
                                     fitur_geojson, stats_minerba)

GEOJSON_DEFAULT = AKAR / "data/wiup/kalimantan_with_loss.geojson"
STATS_DEFAULT = AKAR / "webapp/src/generated/dashboard-stats.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path, help="DB himpunan minerba (dipakai app)")
    ap.add_argument("--db-lengkap", type=Path, default=None,
                    help="DB himpunan lengkap (+galian C); bila berkas tak ada → lengkap: null")
    ap.add_argument("--geojson", type=Path, default=GEOJSON_DEFAULT)
    ap.add_argument("--stats", type=Path, default=STATS_DEFAULT)
    a = ap.parse_args()

    if not a.db.exists():
        print(f"GAGAL: DB tidak ada: {a.db}", file=sys.stderr)
        return 2
    con = buka(a.db)
    wajib_tabel(con, *TABEL_HULU_MINERBA)
    if pastikan_v_konsesi(con, "pipeline/09_sajikan.py"):
        print("  v_konsesi dibuat ulang + meta (w1_util.pastikan_v_konsesi)")
    con.commit()
    wajib_tabel(con, "v_konsesi")

    # (a) geojson QGIS
    feats = fitur_geojson(con)
    a.geojson.parent.mkdir(parents=True, exist_ok=True)
    a.geojson.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                    separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"  {len(feats)} fitur → {a.geojson} ({a.geojson.stat().st_size / 1024 / 1024:.2f} MB)")

    # (b) dashboard-stats.json
    out: dict = {
        "_comment": "AUTO-GENERATED oleh pipeline/09_sajikan.py — JANGAN edit manual; semua angka dari tanah-hilang.db.",
        "generated_at": sekarang(),
        "jendela": JENDELA,
    }
    out.update(stats_minerba(con))
    lengkap = None
    if a.db_lengkap is not None and a.db_lengkap.exists():
        con_l = buka(a.db_lengkap)
        wajib_tabel(con_l, *TABEL_HULU_LENGKAP)
        if pastikan_v_konsesi(con_l, "pipeline/09_sajikan.py"):   # SKEMA §8: v_konsesi ada di KEDUA DB
            print("  v_konsesi dibuat ulang + meta di DB lengkap")
        con_l.commit()
        lengkap = blok_lengkap(con_l)
        tandai_selesai(con_l, "09_sajikan", stats=str(a.stats))
        con_l.close()
    else:
        print(f"  DB lengkap tidak ada ({a.db_lengkap}) → lengkap: null")
    # urutan kunci mengikuti SKEMA §9
    urut = ["_comment", "generated_at", "jendela", "minerba", "lengkap", "keyakinan", "umur", "ippkh",
            "sankey_2001_2024", "registri"]
    out["lengkap"] = lengkap
    out = {k: out[k] for k in urut}
    a.stats.parent.mkdir(parents=True, exist_ok=True)
    a.stats.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    m = out["minerba"]
    print(f"  stats → {a.stats}: minerba {m['n_konsesi']} konsesi · {m['hilang_2001_2024_ha']:,} ha "
          f"({m['pct_hutan_2000']}% hutan 2000)" + (f" · lengkap {lengkap['n_konsesi']} konsesi · "
          f"{lengkap['hilang_2001_2024_ha']:,} ha" if lengkap else ""))

    tandai_selesai(con, "09_sajikan", geojson=str(a.geojson), stats=str(a.stats), n_fitur=len(feats))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
