"""Regenerasi data/wiup/kalimantan_with_loss.geojson LANGSUNG dari kalimantan.db.

Riwayat: dulu skrip ini hanya MENYALIN info match ke file ekspor lama (824
fitur, vintage scrape lama — 38 konsesi baru tak ada, 37 konsesi basi masih
tersisa). Kini membangun ulang SELURUH FeatureCollection dari DB (825):
geometri wiup_geoportal + loss total & per-tahun + temporal + match/badan
usaha — sehingga file selalu sinkron dgn basis data yang dibaca web app.

Konsumen: panduan QGIS (poligon temporal via iup_year).
(Ekspor arsip web/data.js dihapus Agu 2026 — folder web/ berisi demo statis
pra-React sudah tak ada, jadi tak ada lagi konsumennya.)
Jalankan SETELAH filter_minerba/match_harder (process.sh langkah 10).

    python3 scripts/sync_geojson_from_db.py
"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Kolom wiup_geoportal yang TIDAK ikut jadi properti (geometri & metadata tile).
GEO_SKIP = {"geometry_type", "geometry_geojson", "bbox_min_lon", "bbox_min_lat",
            "bbox_max_lon", "bbox_max_lat", "tiles"}


def build_features(conn):
    """Bangun daftar Feature GeoJSON (satu per konsesi) dari kalimantan.db."""
    conn.row_factory = sqlite3.Row
    loss = {r["kode_wiup"]: dict(r) for r in conn.execute("SELECT * FROM wiup_loss")}
    temp = {r["kode_wiup"]: dict(r) for r in conn.execute("SELECT * FROM wiup_temporal")}
    match = {r["kode_wiup"]: dict(r) for r in conn.execute(
        """SELECT m.kode_wiup, m.db_match, m.match_strategy, m.id_badan_usaha,
                  m.minerbaone_url,
                  b.nib, b.alamat, b.jenis_badan_usaha, b.nama_badan_usaha
           FROM wiup_match m
           LEFT JOIN badan_usaha b ON b.id_badan_usaha = m.id_badan_usaha""")}
    yearly = {}
    for kode, y, ha in conn.execute("SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly"):
        yearly.setdefault(kode, {})[y] = ha or 0

    feats = []
    for r in conn.execute("SELECT * FROM wiup_geoportal ORDER BY kode_wiup"):
        d = dict(r)
        kode = d["kode_wiup"]
        geom = json.loads(d["geometry_geojson"])
        p = {k: v for k, v in d.items() if k not in GEO_SKIP}
        # loss agregat
        l = loss.get(kode, {})
        # Nama jendela eksplisit (Fase B): properti geojson mengikuti kolom DB
        # baru — konsumen QGIS/arsip membaca nama yang sama dgn kamus kolom.
        for k in ("polygon_area_ha", "forest_2000_ha", "loss_2001_2025_ha",
                  "loss_pct_poligon_2001_2025", "loss_2001_2025_pct_hutan2000",
                  "loss_2001_2008_ha", "hutan_2009_ha", "loss_2009_2025_ha",
                  "loss_2009_2025_pct_hutan2009"):
            p[k] = l.get(k)
        # loss per tahun 2001-2025 (0 bila tak ada baris)
        yl = yearly.get(kode, {})
        for y in range(2001, 2026):
            p[f"loss_{y}_ha"] = round(yl.get(y, 0), 2)
        # temporal pra/pasca izin
        t = temp.get(kode, {})
        p["rate_2001_sampai_tahun_izin_ha_per_year"] = t.get("rate_2001_sampai_tahun_izin_ha_per_year")
        p["rate_tahun_izin_sampai_2025_ha_per_year"] = t.get("rate_tahun_izin_sampai_2025_ha_per_year")
        p["temporal_verdict"] = t.get("verdict")
        # match MinerbaOne + badan usaha
        m = match.get(kode, {})
        p["db_match"] = m.get("db_match")
        p["match_strategy"] = m.get("match_strategy")
        p["id_badan_usaha"] = m.get("id_badan_usaha") or ""
        p["minerbaone_url"] = m.get("minerbaone_url") or ""
        p["nib"] = m.get("nib") or ""
        p["alamat"] = m.get("alamat") or ""
        p["jenis_badan_usaha"] = m.get("jenis_badan_usaha") or ""
        p["nama_badan_usaha"] = m.get("nama_badan_usaha") or ""
        feats.append({"type": "Feature", "geometry": geom, "properties": p})
    return feats


def main():
    db_path = Path("data/kalimantan.db")
    out_path = Path("data/wiup/kalimantan_with_loss.geojson")

    conn = sqlite3.connect(db_path)
    feats = build_features(conn)
    conn.close()
    gj = {"type": "FeatureCollection", "features": feats}

    out_path.write_text(json.dumps(gj, separators=(",", ":"), ensure_ascii=False))
    print(f"  {len(feats)} fitur → {out_path} "
          f"({out_path.stat().st_size / 1024 / 1024:.2f} MB)", file=sys.stderr)

    strats = Counter((f["properties"].get("match_strategy") or "(unmatched)") for f in feats)
    print("  Distribusi match_strategy:", file=sys.stderr)
    for s, c in strats.most_common():
        print(f"    {s:<20} {c:>4}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
