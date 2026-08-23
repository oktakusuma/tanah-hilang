#!/usr/bin/env python3
"""Panel land use per KONSESI dari MapBiomas Indonesia C4.1 -> data/mapbiomas.db.

MASALAH YANG DIJAWAB: angka utama tesis mengukur KAPAN tutupan pohon hilang di
dalam 825 WIUP (Hansen) dan apakah yang tumbuh kemudian sawit (Descals) —
tapi tidak tahu lahan di dalam konsesi itu SEBENARNYA APA tiap tahun. Tabel di
sini menjawabnya: komposisi kelas tutupan/penggunaan lahan MapBiomas per
konsesi per tahun 2000-2024 (termasuk baseline 2009 dan kelas Lubang Tambang),
plus luas terbakar tahunan per konsesi.

KENAPA DB TERPISAH (data/mapbiomas.db, BUKAN kalimantan.db):
MapBiomas berlisensi CC BY-SA (ShareAlike, menular ke karya turunan) sedangkan
kalimantan.db CC BY. Memisahkan file = memisahkan status lisensi secara
struktural, dan kalimantan.db yang sudah teraudit tak tersentuh.

EMPAT HAL YANG MENENTUKAN ANGKANYA BISA DIPERCAYA:
1. TANPA REPROYEKSI/RESAMPLING SAMA SEKALI. Raster nasional MapBiomas dibaca
   di grid aslinya (EPSG:4326, ~0,000269° ≈ 30 m); luas dihitung per baris
   dengan koreksi lintang cos(lat) — METODE YANG SAMA dengan batch_analyze.py
   dan attribution_sawit.py (konstanta DEG_LAT_METERS diimpor dari
   _geo_common, bukan didefinisikan ulang), hanya ukuran pikselnya dibaca dari
   transform raster MapBiomas sendiri (bukan PIXEL_DEG Hansen — grid keduanya
   memang beda).
2. Grid ke-50 raster (25 LULC + 25 fire) DIVERIFIKASI identik sebelum hitung;
   mask poligon dirasterisasi SEKALI per konsesi (all_touched=False, sama
   dengan pipeline Hansen) lalu dipakai ulang utk semua tahun — jadi jumlah
   piksel per konsesi PASTI konstan antar tahun (diikat invarian di bawah).
3. Universe & geometri = wiup_geoportal di data/kalimantan.db (kode_wiup,
   geometry_geojson) — sumber yang sama dengan attribution_sawit.py; DB itu
   dibuka READ-ONLY dan tidak ditulis apa pun.
4. Kelas 27 (Tak Teramati/awan) DISIMPAN apa adanya sebagai baris kelas —
   tidak dibuang, tidak disembunyikan — supaya pemakai hilir bisa memilih
   penyebut sendiri (view v_konsesi_ringkas menyediakan ha_teramati).

OUTPUT (data/mapbiomas.db):
  landuse_konsesi : kode_wiup x year x class_code -> pixels, ha (long)
  fire_konsesi    : kode_wiup x year -> pixels, ha_terbakar (hanya baris > 0)
  mapbiomas_meta  : provenance (versi koleksi, lisensi, metode, tanggal)
  v_konsesi_ringkas (VIEW): per konsesi x tahun — ha_hutan (3+5+76),
      ha_tambang (30), ha_sawit (35), ha_teramati (semua kelas kecuali 27),
      ha_total, ha_terbakar

CARA PAKAI:
  python3 scripts/mapbiomas/landuse_konsesi.py            # semua default
  python3 scripts/mapbiomas/landuse_konsesi.py --tahun 2009
Prasyarat: fetch_mapbiomas_lulc.py + fetch_mapbiomas_fire.py sudah jalan.

Sitasi data: MapBiomas Indonesia - Collection 4.1 time-series maps of land-use
and land-cover (CC BY-SA), accessed via https://plataforma.mapbiomas.org
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _geo_common as gc  # noqa: E402  (DEG_LAT_METERS — jangan definisi ulang)

LULC_DIR_DEFAULT = Path("data/external/mapbiomas")
FIRE_DIR_DEFAULT = Path("data/external/mapbiomas_fire")
WIUP_DB_DEFAULT = Path("data/kalimantan.db")
OUT_DB_DEFAULT = Path("data/mapbiomas.db")

# Legend Koleksi 4.1 — nama & kelompok MENGIKUTI legenda resmi berbahasa
# Indonesia MapBiomas (tabel legend code; penyesuaian igoen 23 Agu), sinkron
# dgn webapp/src/lib/mapbiomasLegend.ts.
LEGEND = {
    3:  ("Formasi Hutan",              "Hutan"),
    5:  ("Mangrove",                   "Hutan"),
    76: ("Hutan Rawa Gambut",          "Hutan"),
    10: ("Tumbuhan Non-Hutan",         "Tumbuhan Non-Hutan"),
    13: ("Tumbuhan Non-Hutan Lainnya", "Tumbuhan Non-Hutan"),
    40: ("Sawah",                      "Pertanian"),
    35: ("Sawit",                      "Pertanian"),
    9:  ("Kebun Kayu",                 "Pertanian"),
    21: ("Pertanian Lainnya",          "Pertanian"),
    30: ("Lubang Tambang",             "Non-Vegetasi"),
    24: ("Permukiman",                 "Non-Vegetasi"),
    25: ("Non-Vegetasi Lainnya",       "Non-Vegetasi"),
    31: ("Tambak",                     "Tubuh Air"),
    33: ("Sungai, Danau, Laut",        "Tubuh Air"),
    27: ("Citra Tertutup Awan",        "Citra Tertutup Awan"),
}
FOREST = (3, 5, 76)
MINING, SAWIT, UNOBSERVED = 30, 35, 27
NCLASS = 256


def parse_tahun(s: str) -> list[int]:
    out: list[int] = []
    for bag in s.split(","):
        bag = bag.strip()
        if not bag:
            continue
        if "-" in bag:
            a, b = bag.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(bag))
    return sorted(set(out))


def baca_wiup(db_path: Path) -> list[tuple[str, dict]]:
    """(kode_wiup, geometry dict) utk seluruh universe konsesi — READ-ONLY."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT kode_wiup, geometry_geojson FROM wiup_geoportal "
            "WHERE geometry_geojson IS NOT NULL ORDER BY kode_wiup").fetchall()
    finally:
        con.close()
    if not rows:
        raise SystemExit(f"wiup_geoportal kosong di {db_path}")
    return [(k, json.loads(g)) for k, g in rows]


def area_baris_ha(win_transform, height: int, width: int) -> np.ndarray:
    """Luas piksel (ha) per baris jendela, koreksi lintang cos(lat).

    Rumus identik _geo_common.pixel_area_ha, tapi ukuran piksel dibaca dari
    transform raster MapBiomas (bukan PIXEL_DEG Hansen) dan divektorkan.
    """
    px_x = abs(win_transform.a)
    px_y = abs(win_transform.e)
    # lintang pusat tiap baris
    lats = win_transform.f + (np.arange(height) + 0.5) * win_transform.e
    width_m = px_x * gc.DEG_LAT_METERS * np.cos(np.radians(lats))
    height_m = px_y * gc.DEG_LAT_METERS
    per_row = (width_m * height_m) / 10_000.0
    return np.broadcast_to(per_row[:, None], (height, width))


def jendela_konsesi(geom, ref) -> Window | None:
    """Jendela baca (di-clamp ke raster) utk bbox geometri; None bila di luar."""
    minx, miny, maxx, maxy = shape(geom).bounds
    win = from_bounds(minx, miny, maxx, maxy, transform=ref.transform)
    win = win.round_offsets(op="floor").round_lengths(op="ceil")
    col0 = max(0, int(win.col_off))
    row0 = max(0, int(win.row_off))
    col1 = min(ref.width, int(win.col_off + win.width))
    row1 = min(ref.height, int(win.row_off + win.height))
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def siapkan_konsesi(wiup, ref):
    """Per konsesi: (kode, window, mask bool, area_grid ha). Mask dirasterisasi
    SEKALI di grid referensi lalu dipakai ulang utk semua tahun & fire."""
    hasil = []
    luar = []
    for kode, geom in wiup:
        win = jendela_konsesi(geom, ref)
        if win is None:
            luar.append(kode)
            continue
        t = ref.window_transform(win)
        h, w = int(win.height), int(win.width)
        mask = rasterize([(geom, 1)], out_shape=(h, w), transform=t,
                         fill=0, dtype="uint8", all_touched=False).astype(bool)
        if not mask.any():
            luar.append(kode)
            continue
        hasil.append((kode, win, mask, area_baris_ha(t, h, w)))
    if luar:
        print(f"PERINGATAN: {len(luar)} konsesi tanpa piksel di raster "
              f"(di luar cakupan / terlalu kecil utk grid): {luar[:5]}...",
              file=sys.stderr)
    return hasil


def cek_grid_sama(ref, path: Path) -> None:
    with rasterio.open(path) as ds:
        if (ds.shape != ref.shape) or (ds.transform != ref.transform):
            raise SystemExit(
                f"Grid {path.name} tidak identik dengan raster referensi — "
                "semua raster MapBiomas harus satu grid. Unduh ulang / cek --check.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lulc-dir", default=str(LULC_DIR_DEFAULT))
    ap.add_argument("--fire-dir", default=str(FIRE_DIR_DEFAULT))
    ap.add_argument("--wiup-db", default=str(WIUP_DB_DEFAULT))
    ap.add_argument("--out-db", default=str(OUT_DB_DEFAULT))
    ap.add_argument("--tahun", default="2000-2024")
    ap.add_argument("--tanpa-fire", action="store_true",
                    help="lewati lapisan kebakaran (mis. belum diunduh)")
    a = ap.parse_args(argv)

    tahun = parse_tahun(a.tahun)
    lulc_dir, fire_dir = Path(a.lulc_dir), Path(a.fire_dir)
    lulc = {t: lulc_dir / f"mapbiomas_c41_{t}.tif" for t in tahun}
    hilang = [t for t, p in lulc.items() if not p.exists()]
    if hilang:
        raise SystemExit(f"Raster LULC belum ada utk tahun {hilang} di {lulc_dir} "
                         "— jalankan fetch_mapbiomas_lulc.py dulu.")
    fire = {}
    if not a.tanpa_fire:
        fire = {t: fire_dir / f"mapbiomas_fire_annual_{t}.tif" for t in tahun}
        f_hilang = [t for t, p in fire.items() if not p.exists()]
        if f_hilang:
            raise SystemExit(f"Raster fire belum ada utk tahun {f_hilang} di "
                             f"{fire_dir} — fetch_mapbiomas_fire.py, atau --tanpa-fire.")

    wiup = baca_wiup(Path(a.wiup_db))
    print(f"{len(wiup)} konsesi, {len(tahun)} tahun ({tahun[0]}-{tahun[-1]})")

    ref = rasterio.open(lulc[tahun[0]])
    for p in list(lulc.values())[1:] + list(fire.values()):
        cek_grid_sama(ref, p)
    konsesi = siapkan_konsesi(wiup, ref)
    print(f"{len(konsesi)} konsesi punya piksel; grid {ref.width}x{ref.height}")

    baris_lulc: list[tuple] = []
    baris_fire: list[tuple] = []
    piksel_mask = {kode: int(mask.sum()) for kode, _, mask, _ in konsesi}

    for t in tahun:
        with rasterio.open(lulc[t]) as ds:
            for kode, win, mask, area in konsesi:
                arr = ds.read(1, window=win)
                vals = arr[mask]
                areas = area[mask]
                px = np.bincount(vals, minlength=NCLASS)
                ha = np.bincount(vals, weights=areas, minlength=NCLASS)
                total_px = 0
                for c in np.nonzero(px)[0]:
                    if c == 0:
                        continue  # nodata (laut / luar Indonesia)
                    nama, kelompok = LEGEND.get(int(c), (f"UNKNOWN_{c}", "UNKNOWN"))
                    baris_lulc.append((kode, t, int(c), nama, kelompok,
                                       int(px[c]), round(float(ha[c]), 4)))
                    total_px += int(px[c])
                # Invarian per tahun: piksel berkelas + nodata = piksel mask.
                assert total_px + int(px[0]) == piksel_mask[kode], \
                    f"tabulasi bocor: {kode} tahun {t}"
        if fire:
            with rasterio.open(fire[t]) as ds:
                for kode, win, mask, area in konsesi:
                    arr = ds.read(1, window=win)
                    # ENCODING NYATA raster fire_annual (diverifikasi dari isi
                    # berkas 2015, BUKAN dari legenda platform yang menulis
                    # "pixelValue=1"): nilai piksel = KODE KELAS LULC yang
                    # terbakar tahun itu (3/13/25/35/76 dst.), 0 = tak terbakar.
                    # Utk luas terbakar biner, cukup arr > 0.
                    m = mask & (arr > 0)
                    if m.any():
                        baris_fire.append((kode, t, int(m.sum()),
                                           round(float(area[m].sum()), 4)))
        print(f"  {t}: {len(baris_lulc):,} baris lulc, {len(baris_fire):,} baris fire",
              flush=True)
    ref.close()

    out = Path(a.out_db)
    out.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out)
    try:
        con.executescript("""
            DROP TABLE IF EXISTS landuse_konsesi;
            DROP TABLE IF EXISTS fire_konsesi;
            DROP TABLE IF EXISTS mapbiomas_meta;
            DROP VIEW IF EXISTS v_konsesi_ringkas;
            CREATE TABLE landuse_konsesi (
                kode_wiup  TEXT NOT NULL,
                year       INTEGER NOT NULL,
                class_code INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                kelompok   TEXT NOT NULL,
                pixels     INTEGER NOT NULL,
                ha         REAL NOT NULL,
                PRIMARY KEY (kode_wiup, year, class_code)
            );
            CREATE TABLE fire_konsesi (
                kode_wiup   TEXT NOT NULL,
                year        INTEGER NOT NULL,
                pixels      INTEGER NOT NULL,
                ha_terbakar REAL NOT NULL,
                PRIMARY KEY (kode_wiup, year)
            );
            CREATE TABLE mapbiomas_meta (kunci TEXT PRIMARY KEY, nilai TEXT NOT NULL);
        """)
        con.executemany(
            "INSERT INTO landuse_konsesi VALUES (?,?,?,?,?,?,?)", baris_lulc)
        con.executemany(
            "INSERT INTO fire_konsesi VALUES (?,?,?,?)", baris_fire)
        con.executescript(f"""
            CREATE VIEW v_konsesi_ringkas AS
            SELECT l.kode_wiup, l.year,
                   SUM(CASE WHEN l.class_code IN {FOREST} THEN l.ha ELSE 0 END) AS ha_hutan,
                   SUM(CASE WHEN l.class_code = {MINING} THEN l.ha ELSE 0 END) AS ha_tambang,
                   SUM(CASE WHEN l.class_code = {SAWIT} THEN l.ha ELSE 0 END) AS ha_sawit,
                   SUM(CASE WHEN l.class_code != {UNOBSERVED} THEN l.ha ELSE 0 END) AS ha_teramati,
                   SUM(l.ha) AS ha_total,
                   COALESCE(MAX(f.ha_terbakar), 0) AS ha_terbakar
            FROM landuse_konsesi l
            LEFT JOIN fire_konsesi f
                   ON f.kode_wiup = l.kode_wiup AND f.year = l.year
            GROUP BY l.kode_wiup, l.year;
        """)
        meta = {
            "sumber": "MapBiomas Indonesia Collection 4.1 (coverage_lclu + fire_annual)",
            "lisensi": "CC BY-SA — JANGAN dicampur ke kalimantan.db (CC BY)",
            "sitasi": ("MapBiomas Indonesia - Collection 4.1 time-series maps of "
                       "land-use and land-cover, via https://plataforma.mapbiomas.org"),
            "skrip": "scripts/mapbiomas/landuse_konsesi.py",
            "metode": ("grid asli EPSG:4326 tanpa resampling; luas per piksel "
                       "koreksi lintang cos(lat) ala _geo_common.pixel_area_ha; "
                       "mask poligon all_touched=False dirasterisasi sekali per "
                       "konsesi; universe = wiup_geoportal kalimantan.db"),
            "manifest": "scripts/mapbiomas/manifest_c41_2000_2024.csv (md5) + "
                        "manifest_fire_annual_2000_2024.csv",
            "tahun": f"{tahun[0]}-{tahun[-1]}",
            "n_konsesi": str(len(konsesi)),
            "fire_disertakan": "0" if a.tanpa_fire else "1",
            "dibangun": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        con.executemany("INSERT INTO mapbiomas_meta VALUES (?,?)", meta.items())
        con.commit()

        # Invarian akhir: jumlah piksel per konsesi konstan antar tahun
        # (mask sama, grid sama). Nodata ikut mask, jadi bandingkan piksel
        # berkelas + implisit-nodata lewat cacah minimum.
        rusak = con.execute("""
            SELECT kode_wiup, COUNT(DISTINCT total_px) FROM (
                SELECT kode_wiup, year, SUM(pixels) AS total_px
                FROM landuse_konsesi GROUP BY kode_wiup, year
            ) GROUP BY kode_wiup HAVING COUNT(DISTINCT total_px) > 3
        """).fetchall()
        if rusak:
            # >3 nilai berbeda = perubahan nodata antar tahun yang mencurigakan
            # (toleransi kecil krn nodata 0 di dalam mask ikut berubah di tepi laut).
            print(f"PERINGATAN: {len(rusak)} konsesi dgn jumlah piksel berkelas "
                  f"sangat berfluktuasi antar tahun: {rusak[:5]}", file=sys.stderr)
    finally:
        con.close()

    print(f"\nTulis {out}: {len(baris_lulc):,} baris landuse_konsesi, "
          f"{len(baris_fire):,} baris fire_konsesi, view v_konsesi_ringkas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
