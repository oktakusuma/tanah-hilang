#!/usr/bin/env python3
"""Atribusi kehilangan tutupan pohon di dalam konsesi ke konversi kelapa sawit.

MASALAH YANG DIJAWAB: angka utama tesis (1.603.251 ha tutupan pohon hilang di
dalam 825 konsesi minerba) memakai batas WIUP sebagai batas analisis. Batas
izin itu batas ADMINISTRATIF, bukan bukti aktivitas tambang — sebagian
kehilangan di dalamnya bisa jadi sebenarnya konversi ke kelapa sawit, bukan
pembukaan tambang. Uji ini memakai peta tahun tanam sawit Descals dkk. (2024)
utk memisahkan: bila piksel kehilangan tutupan pohon pada tahun T DAN piksel
yang sama menjadi sawit di sekitar tahun T, kehilangan itu lebih masuk akal
diatribusikan ke sawit ketimbang ke tambang.

LIMA HAL YANG MENENTUKAN APAKAH ANGKA INI BISA DIPERCAYA (jangan disunat):

1. Grid Descals (0,00026949458523585647°) TIDAK berimpit dgn grid Hansen
   (0,00025°). Keduanya EPSG:4326 jadi tak perlu reproyeksi CRS, TAPI piksel
   tak sejajar. Descals di-resample ke grid piksel Hansen memakai
   `rasterio.warp.reproject` dgn `Resampling.nearest` (lihat
   `resample_descals_ke_grid`) — WAJIB nearest krn nilainya tahun tanam
   (kategorik), meng-interpolasi/rata-rata akan mengarang tahun yg tak pernah
   ada.
2. Filter kanopi 30% (`treecover2000 >= THRESHOLD`) — SAMA PERSIS dgn
   batch_analyze.py (skrip yg menghasilkan wiup_loss.total_loss_ha), supaya
   angka di sini sebanding dgn 1.603.251 ha.
3. Encoding Descals: 0 = bukan sawit; 1989-2022 = tahun tanam LANGSUNG (bukan
   offset spt lossyear Hansen).
4. Tiga varian jendela pencocokan disimpan sbg tiga kolom (bukan satu angka
   tunggal) supaya uji sensitivitas gratis tanpa jalan ulang raster:
     loss_sawit_tol2th_ha    : YoP >= tahun_loss - 2   (UTAMA/patokan, BATAS ATAS — toleransi
                               mundur 2 th, toleran thd efek tepi)
     loss_sawit_tahunsama_ha : YoP >= tahun_loss       (TANPA toleransi mundur, tengah; YoP tak
                               boleh MENDAHULUI loss)
     loss_sawit_jeda5th_ha   : tahun_loss <= YoP <= tahun_loss + 5  (PALING KETAT/BATAS BAWAH —
                               jendela 0-5 th; angka minimum sejati krn jeda5th ⊆ tahunsama ⊆ tol2th)
   Alasan toleransi: RMSE tahun tanam Descals 2,02 th (perkebunan industri)
   / 4,89 th (rakyat) — lihat Descals dkk. 2024. Persen/pangsa dihitung di view,
   bukan disimpan sbg kolom di sini.
5. Descals berhenti di tahun 2021. Kehilangan 2022-2025 TIDAK BISA diperiksa
   sama sekali thd sawit — disimpan terpisah di `loss_2022_2025_ha` sbg "tak
   terperiksa", BUKAN digabung diam-diam ke penyebut pangsa.

PENANGANAN KONSESI LINTAS-TILE HANSEN: 16 dari 825 konsesi membentang >1 tile
Hansen 10°x10°. Pola clip-poligon-ke-tile-lalu-jumlah dipakai di sini IDENTIK
dgn `batch_analyze.analyze_wiup_in_tile` — skrip yg sama yg menghasilkan
wiup_loss.total_loss_ha — bukan pendekatan baru. Untuk tiap tile yg disentuh
konsesi: poligon di-clip ke bbox tile itu (mencegah hitung ganda di piksel
yg sama), lalu baca-window+rasterize+akumulasi HANYA dari bagian yg ter-clip;
kontribusi dari tiap tile dijumlah per kode_wiup oleh pemanggil. Prototipe di
scratchpad melewati (skip) konsesi begini seluruhnya — skrip ini TIDAK.

SUMBER DATA:
  Hansen lossyear+treecover2000  : data/raster/Hansen_GFC-2025-v1.13_*.tif
  Descals tahun tanam sawit      : data/external/descals/tiles/*.tif +
                                    tile_index.json (95 ubin)
  Universe 825 konsesi + geometri: wiup_geoportal di data/kalimantan.db
                                    (geometry_geojson di tabel ini terverifikasi
                                    identik dgn data/wiup/kalimantan_unique.geojson
                                    utk 825 kode yg sama — dipakai langsung dari
                                    DB agar tak bergantung pada berkas eksternal
                                    kedua)

OUTPUT: tabel `atribusi_sawit` (1 baris per kode_wiup, 825 baris) di
data/kalimantan.db. Plus `atribusi_sawit_yearly` (kode_wiup, year,
loss_sawit_tol2th_ha; sparse, HANYA varian tol2th/UTAMA, tahun 2001-2021) —
pecahan per-tahun dari loss_sawit_tol2th_ha, dasar rumus "loss bersih dari
sawit" per (periode,tahun) di build_periode_tables.py (varian *_bersih, Task
F1). Konsistensi SUM(atribusi_sawit_yearly per konsesi) vs window
loss_sawit_tol2th_ha DICEK (ambang 0,5 ha, lihat cek_konsistensi_tahunan())
SEBELUM kedua tabel ditulis — galat membatalkan seluruh run (rc=1).

CATATAN PROVENANSI: skrip ini TIDAK menulis ke analysis_meta / column_meta —
build_periode_tables.py adalah pemilik tunggal kedua tabel itu (ia DROP dan
membangunnya ulang). Baris provenansi utk atribusi_sawit didaftarkan terpisah
di sana.

Pakai:
    .venv/bin/python3 scripts/attribution_sawit.py
    .venv/bin/python3 scripts/attribution_sawit.py --db data/kalimantan.db
    .venv/bin/python3 scripts/attribution_sawit.py --limit 50   # uji cepat
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np
import rasterio
import rasterio.transform
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, shape

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _geo_common as gc  # noqa: E402

CRS_4326 = CRS.from_epsg(4326)

THRESHOLD = 30            # treecover2000 >= 30% — SAMA PERSIS dgn batch_analyze.py
N_YEARS = 25               # lossyear kode 1..25 = tahun 2001..2025
TAHUN_MAKS_DESCALS = 21    # Descals berhenti 2021 → lossyear kode <=21 bisa diperiksa
TOLERANSI_M2 = 2           # varian loss_sawit_tol2th_ha: YoP >= tahun_loss - TOLERANSI_M2
JENDELA_0_5_AKHIR = 5      # varian loss_sawit_jeda5th_ha: tahun_loss <= YoP <= tahun_loss + ini

DESCALS_DIR = pathlib.Path("data/external/descals/tiles")
DESCALS_INDEX = pathlib.Path("data/external/descals/tile_index.json")


# ── Logika murni (bisa diuji tanpa raster) ────────────────────────────────────

def klasifikasi_sawit(yop: np.ndarray, tahun_loss: np.ndarray) -> dict[str, np.ndarray]:
    """Tiga varian aturan pencocokan piksel sawit x loss (spec item 4).

    yop        : tahun tanam Descals per piksel (0 = bukan sawit; array int)
    tahun_loss : tahun kalender kehilangan Hansen per piksel (mis. 2007 utk
                 lossyear kode 7), array int SAMA BENTUK dgn yop.

    `ada = yop > 0` sengaja eksplisit walau secara aljabar hampir selalu tak
    berpengaruh (tahun_loss >= 2001 shg YoP=0 sudah gagal tiap uji >=), supaya
    niatnya ("0 bukan sawit") tak bergantung diam-diam pada rentang tahun.
    """
    ada = yop > 0
    return {
        "m2": ada & (yop >= tahun_loss - TOLERANSI_M2),
        "0": ada & (yop >= tahun_loss),
        "0_5": ada & (yop >= tahun_loss) & (yop <= tahun_loss + JENDELA_0_5_AKHIR),
    }


def tile_descals_bersinggungan(idx: list[dict], bounds: tuple[float, float, float, float]
                                ) -> list[dict]:
    """Ubin Descals dari `idx` yg bbox-nya bersinggungan dgn `bounds`."""
    minx, miny, maxx, maxy = bounds
    return [t for t in idx
            if not (t["right"] <= minx or t["left"] >= maxx
                    or t["top"] <= miny or t["bottom"] >= maxy)]


def resample_descals_ke_grid(sumber: list[tuple[np.ndarray, "rasterio.Affine"]],
                              out_shape: tuple[int, int], out_transform
                              ) -> np.ndarray:
    """Resample satu/lebih ubin Descals ke grid piksel Hansen (spec item 1).

    `sumber`: daftar (array, transform) ubin Descals yg relevan (bisa >1 kalau
    konsesi berada di perbatasan dua ubin Descals). NEAREST wajib krn nilai
    piksel = tahun tanam (kategorik) — rata-rata piksel tetangga akan
    mengarang tahun tanam yg tak pernah ada di data aslinya.

    Beberapa ubin digabung dgn `maximum` (bukan overwrite/rata-rata): 0 berarti
    "bukan sawit ATAU di luar cakupan ubin ini", jadi nilai taknol dari ubin
    mana pun menang — konsisten dgn semantik "0 = bukan sawit" yg dipakai di
    seluruh berkas ini.
    """
    dst = np.zeros(out_shape, dtype=np.uint16)
    for arr, transform in sumber:
        tmp = np.zeros(out_shape, dtype=np.uint16)
        reproject(
            source=arr,
            destination=tmp,
            src_transform=transform,
            src_crs=CRS_4326,
            dst_transform=out_transform,
            dst_crs=CRS_4326,
            resampling=Resampling.nearest,
        )
        dst = np.maximum(dst, tmp)
    return dst


def row_area_grid_ha(row_lats: np.ndarray, width: int) -> np.ndarray:
    """Luas piksel per baris (koreksi lintang), dibroadcast ke lebar jendela.

    Konstanta (PIXEL_DEG, DEG_LAT_METERS) diimpor dari _geo_common — BUKAN
    didefinisikan ulang — supaya angka luas di sini SAMA PERSIS dgn
    batch_analyze.py/wiup_loss.total_loss_ha, hanya divektorkan per baris
    (fungsi asal di _geo_common skalar-saja).
    """
    width_m = gc.PIXEL_DEG * gc.DEG_LAT_METERS * np.cos(np.radians(row_lats))
    height_m = gc.PIXEL_DEG * gc.DEG_LAT_METERS
    per_row = (width_m * height_m) / 10_000
    return np.broadcast_to(per_row[:, None], (len(row_lats), width))


def tol2th_area_by_year(area_grid: np.ndarray, tahun_loss: np.ndarray,
                         mask: np.ndarray) -> dict[int, float]:
    """Jumlah luas (ha) piksel ber-`mask` (biasanya sel_0121 & varian tol2th),
    dikelompokkan per tahun kalender loss — dasar tabel `atribusi_sawit_yearly`.

    Dipisah dari `proses_konsesi_di_tile` (murni, tanpa I/O) supaya bisa diuji
    dgn array kecil buatan tanpa raster asli. Dibatasi tahun 2001-TAHUN_MAKS_DESCALS
    (2021) krn dipanggil HANYA atas piksel yg sudah difilter sel_0121 di
    pemanggil — kalau dipanggil di luar rentang itu, hasil di luar jendela
    2001-2021 akan diam-diam dibuang oleh `minlength`/indexing di bawah
    (BUKAN skenario yg dipakai pemanggil saat ini, tapi didokumentasikan).
    """
    if not mask.any():
        return {}
    yrs = tahun_loss[mask]
    areas = area_grid[mask]
    offset = yrs - 2001
    sums = np.bincount(offset, weights=areas, minlength=TAHUN_MAKS_DESCALS)
    return {2001 + i: float(s) for i, s in enumerate(sums) if s}


def ambang_tahunan(_v: float = 0.0) -> float:
    """Ambang selisih SUM(atribusi_sawit_yearly) vs window loss_sawit_tol2th_ha
    (spec F1: 0,5 ha) — longgar thd pembulatan akumulasi 21 tahun x banyak
    piksel per tahun, TAPI cukup ketat utk menangkap salah alokasi tahun."""
    return 0.5


def cek_konsistensi_tahunan(hasil: dict[str, dict]) -> list[str]:
    """hasil: dict kode_wiup -> baris (butuh kunci 'sawit_m2_ha' [window
    loss_sawit_tol2th_ha] dan 'tol2th_by_year' [dict tahun->ha]). Bandingkan
    SUM(tol2th_by_year.values()) vs sawit_m2_ha per konsesi -> daftar pesan
    galat (kosong jika semua konsisten). Dipanggil main() SEBELUM menulis
    atribusi_sawit/atribusi_sawit_yearly — galat harus mencegah kedua tabel
    ditulis (invarian: SUM(yearly) = window loss_sawit_tol2th_ha)."""
    galat = []
    for kode, b in hasil.items():
        window = b.get("sawit_m2_ha", 0.0) or 0.0
        tahunan = sum(b.get("tol2th_by_year", {}).values())
        d = window - tahunan
        if abs(d) > ambang_tahunan(window):
            galat.append(
                f"{kode}: beda {d:+,.4f} ha (window={window:,.4f} vs Σtahunan={tahunan:,.4f})")
    return galat


def hitung_pangsa(loss_sawit_ha: float, loss_total_ha: float) -> float | None:
    """Pangsa sawit dari total loss yg bisa diperiksa; None kalau penyebut 0
    (bukan 0.0 — konsesi tanpa loss 2001-2021 TAK PUNYA pangsa, beda dgn
    konsesi yg loss-nya nol persen sawit)."""
    if loss_total_ha <= 0:
        return None
    return round(loss_sawit_ha / loss_total_ha, 6)


# ── I/O raster ────────────────────────────────────────────────────────────────

class DescalsCache:
    """Cache ubin Descals di memori (lazy) — hanya ubin yg benar2 disentuh
    konsesi yg dimuat, bukan seluruh 95 ubin x 22 MB sekaligus."""

    def __init__(self, idx: list[dict], tile_dir: pathlib.Path):
        self.idx = idx
        self.tile_dir = pathlib.Path(tile_dir)
        self._arr: dict[str, tuple[np.ndarray, object]] = {}

    def ambil(self, bounds: tuple[float, float, float, float]
              ) -> list[tuple[np.ndarray, object]]:
        out = []
        for t in tile_descals_bersinggungan(self.idx, bounds):
            if t["file"] not in self._arr:
                with rasterio.open(self.tile_dir / t["file"]) as s:
                    self._arr[t["file"]] = (s.read(1), s.transform)
            out.append(self._arr[t["file"]])
        return out


def proses_konsesi_di_tile(poly, ls: rasterio.io.DatasetReader,
                            ts: rasterio.io.DatasetReader,
                            descals: DescalsCache) -> dict | None:
    """Hitung kontribusi satu konsesi di SATU tile Hansen.

    Dipanggil sekali per tile yg disentuh konsesi (>1x utk 16 konsesi
    lintas-tile); pemanggil MENJUMLAHKAN hasil antar tile per kode_wiup.
    Mengembalikan None kalau konsesi ternyata tak menyentuh tile ini sama
    sekali (bbox bisa menyentuh tapi geometri sesungguhnya tidak).

    Pola clip-ke-tile-lalu-jumlah IDENTIK dgn
    batch_analyze.analyze_wiup_in_tile (skrip yg menghasilkan
    wiup_loss.total_loss_ha) — cross-tile ditangani dgn cara yg SAMA PERSIS
    dgn pipeline utama, bukan pendekatan baru yg belum teruji.
    """
    tb = ls.bounds
    tile_box = box(tb.left, tb.bottom, tb.right, tb.top)
    if not poly.intersects(tile_box):
        return None
    clipped = poly.intersection(tile_box)
    if clipped.is_empty:
        return None

    cminx, cminy, cmaxx, cmaxy = clipped.bounds
    win = from_bounds(cminx, cminy, cmaxx, cmaxy, ls.transform)
    win = win.round_offsets().round_lengths()
    win = Window(
        col_off=max(int(win.col_off) - 1, 0),
        row_off=max(int(win.row_off) - 1, 0),
        width=int(win.width) + 2,
        height=int(win.height) + 2,
    )
    if win.width <= 0 or win.height <= 0:
        return None

    wt = ls.window_transform(win)
    loss = ls.read(1, window=win)
    tcov = ts.read(1, window=win)
    assert tcov.shape == loss.shape, (
        f"jendela treecover {tcov.shape} != lossyear {loss.shape} — "
        f"baca_window lossyear & treecover harus pixel-aligned")

    mask_geom = rasterize([(clipped.__geo_interface__, 1)], out_shape=loss.shape,
                          transform=wt, fill=0, dtype=np.uint8) == 1
    mask_hutan = tcov >= THRESHOLD

    row_lats = wt.f + (np.arange(loss.shape[0]) + 0.5) * wt.e
    area_grid = row_area_grid_ha(row_lats, loss.shape[1])

    ada_loss = mask_geom & mask_hutan & (loss >= 1) & (loss <= N_YEARS)
    sel_0121 = ada_loss & (loss <= TAHUN_MAKS_DESCALS)   # loss 2001-2021: bisa diperiksa
    sel_2225 = ada_loss & (loss > TAHUN_MAKS_DESCALS)    # loss 2022-2025: TAK bisa diperiksa

    hasil = {
        "loss_2001_2021_ha": float(area_grid[sel_0121].sum()),
        "loss_2022_2025_ha": float(area_grid[sel_2225].sum()),
        "sawit_m2_ha": 0.0,
        "sawit_0_ha": 0.0,
        "sawit_0_5_ha": 0.0,
        "tol2th_by_year": {},
    }
    if sel_0121.any():
        bounds_win = rasterio.transform.array_bounds(loss.shape[0], loss.shape[1], wt)
        sumber = descals.ambil(bounds_win)
        yop = resample_descals_ke_grid(sumber, loss.shape, wt)
        tahun_loss = 2000 + loss.astype(np.int32)
        masks = klasifikasi_sawit(yop, tahun_loss)
        mask_m2 = sel_0121 & masks["m2"]
        hasil["sawit_m2_ha"] = float(area_grid[mask_m2].sum())
        hasil["sawit_0_ha"] = float(area_grid[sel_0121 & masks["0"]].sum())
        hasil["sawit_0_5_ha"] = float(area_grid[sel_0121 & masks["0_5"]].sum())
        # Akumulasi per (tahun, konsesi) HANYA utk varian tol2th (dasar tabel
        # atribusi_sawit_yearly / rumus "loss bersih" F1) — varian 0/0_5 tak
        # perlu pecahan tahunan (tak dipakai build_periode_tables_bersih).
        hasil["tol2th_by_year"] = tol2th_area_by_year(area_grid, tahun_loss, mask_m2)
    return hasil


SCHEMA = """
CREATE TABLE atribusi_sawit (
  kode_wiup                TEXT PRIMARY KEY REFERENCES wiup_geoportal(kode_wiup),
  loss_2001_2021_ha        REAL,
  loss_sawit_tol2th_ha     REAL,
  loss_sawit_jeda5th_ha    REAL,
  loss_sawit_tahunsama_ha  REAL,
  loss_2022_2025_ha        REAL,
  n_tile_hansen            INTEGER
)
"""

# Pecahan PER TAHUN dari loss_sawit_tol2th_ha (HANYA varian tol2th/UTAMA —
# dasar rumus "loss bersih" F1: wiup_loss_yearly.loss_ha − ini, tahun<=2021).
# Sparse (spt wiup_loss_yearly): baris hanya utk (kode_wiup,year) dgn nilai>0,
# tahun tanpa loss-sawit tersirat 0 lewat COALESCE di pemakainya.
SCHEMA_YEARLY = """
CREATE TABLE atribusi_sawit_yearly (
  kode_wiup             TEXT REFERENCES wiup_geoportal(kode_wiup),
  year                  INTEGER,
  loss_sawit_tol2th_ha  REAL,
  PRIMARY KEY (kode_wiup, year)
)
"""


def ambang(v):
    """Ambang selisih: longgar thd efek tepi rasterisasi, ketat thd salah jendela.
    Terukur v1: 2/825 baris meleset >0,01 ha, maks 1,4 ha."""
    return max(5.0, 0.005 * abs(v or 0.0))


def cek_jendela(con, hasil):
    """hasil: dict kode_wiup -> baris atribusi. Bandingkan dgn wiup_loss."""
    total = {k: v for k, v in con.execute("SELECT kode_wiup, total_loss_ha FROM wiup_loss")}
    galat, n_beda, jml = [], 0, 0.0
    for kode, b in hasil.items():
        t = total.get(kode)
        if t is None or b["loss_2001_2021_ha"] is None:
            continue
        d = t - (b["loss_2001_2021_ha"] + (b["loss_2022_2025_ha"] or 0.0))
        if abs(d) > 0.01:
            n_beda += 1; jml += d
        if abs(d) > ambang(t):
            galat.append(f"{kode}: beda {d:,.2f} ha")
    if n_beda:
        print(f"[atribusi] efek tepi: {n_beda} baris beda >0,01 ha, total {jml:+,.2f} ha",
              file=sys.stderr)
    return galat   # main(): bila galat -> print contoh, return 1, JANGAN tulis tabel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="data/kalimantan.db")
    ap.add_argument("--raster-dir", default="data/raster")
    ap.add_argument("--descals-dir", default=str(DESCALS_DIR))
    ap.add_argument("--descals-index", default=str(DESCALS_INDEX))
    ap.add_argument("--limit", type=int, default=None,
                     help="proses hanya N konsesi pertama (uji cepat, bukan utk laporan akhir)")
    args = ap.parse_args(argv)

    raster_dir = pathlib.Path(args.raster_dir)
    descals_dir = pathlib.Path(args.descals_dir)
    descals_index_path = pathlib.Path(args.descals_index)
    if not any(raster_dir.glob("Hansen_*lossyear*.tif")):
        print(f"GAGAL: raster Hansen tak ada di {raster_dir}", file=sys.stderr)
        return 1
    if not descals_index_path.exists():
        print(f"GAGAL: index Descals tak ada di {descals_index_path}", file=sys.stderr)
        return 1
    idx = json.loads(descals_index_path.read_text())

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT kode_wiup, bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat, "
        "geometry_geojson FROM wiup_geoportal ORDER BY kode_wiup").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"[atribusi-sawit] {len(rows)} konsesi minerba dimuat dari wiup_geoportal",
          file=sys.stderr)

    parsed = {}
    by_tile: dict[str, list[str]] = defaultdict(list)
    n_tile_map: dict[str, int] = {}
    for r in rows:
        kode = r["kode_wiup"]
        parsed[kode] = shape(json.loads(r["geometry_geojson"]))
        b = (r["bbox_min_lon"], r["bbox_min_lat"], r["bbox_max_lon"], r["bbox_max_lat"])
        tiles = gc.pick_tile(b[1], b[3], b[0], b[2])
        n_tile_map[kode] = len(tiles)
        for t in tiles:
            by_tile[t].append(kode)

    n_lintas = sum(1 for n in n_tile_map.values() if n > 1)
    print(f"[atribusi-sawit] {n_lintas} konsesi lintas-tile Hansen "
          f"(ditangani penuh via clip-per-tile, bukan dilewati)", file=sys.stderr)

    descals = DescalsCache(idx, descals_dir)
    acc: dict[str, dict] = defaultdict(
        lambda: {"loss_2001_2021_ha": 0.0, "loss_2022_2025_ha": 0.0,
                 "sawit_m2_ha": 0.0, "sawit_0_ha": 0.0, "sawit_0_5_ha": 0.0,
                 "tol2th_by_year": {}})
    gagal: list[tuple[str, str]] = []

    t0 = time.time()
    total_diproses = 0
    for tile, kodes in sorted(by_tile.items()):
        lp = gc.lossyear_path(tile, raster_dir)
        tp = gc.treecover_path(tile, raster_dir)
        if not lp.exists() or not tp.exists():
            print(f"  WARN: raster tile {tile} tak ada — {len(kodes)} konsesi "
                  f"terdampak dilewati utk tile ini", file=sys.stderr)
            continue
        print(f"  [{tile}] {len(kodes)} konsesi", file=sys.stderr)
        with rasterio.open(lp) as ls, rasterio.open(tp) as ts:
            for i, kode in enumerate(kodes, 1):
                try:
                    hasil = proses_konsesi_di_tile(parsed[kode], ls, ts, descals)
                except Exception as e:  # noqa: BLE001 — dicatat, bukan menghentikan seluruh run
                    gagal.append((kode, f"{type(e).__name__}: {e}"))
                    continue
                if hasil is None:
                    continue
                a = acc[kode]
                for k, v in hasil.items():
                    if k == "tol2th_by_year":
                        # Merge dict antar-tile (16 konsesi lintas-tile bisa
                        # menyumbang tahun yg sama dari tile berbeda) --
                        # jumlahkan per tahun, JANGAN timpa.
                        for y, ha in v.items():
                            a[k][y] = a[k].get(y, 0.0) + ha
                    else:
                        a[k] += v
                total_diproses += 1
                if total_diproses % 200 == 0:
                    print(f"    …{total_diproses} konsesi-tile ({time.time()-t0:.0f}s)",
                          file=sys.stderr)

    elapsed = time.time() - t0
    print(f"[atribusi-sawit] pemindaian selesai dlm {elapsed:.0f}s", file=sys.stderr)
    if gagal:
        print(f"  GAGAL: {len(gagal)} (konsesi × tile) error saat diproses:",
              file=sys.stderr)
        for kode, err in gagal[:20]:
            print(f"    {kode}: {err}", file=sys.stderr)

    # ── Cek jendela: 2001-2021 + 2022-2025 harus ~= wiup_loss.total_loss_ha,
    # dipindah dari konsesi_ringkas.py v1 supaya galat ketahuan di hulu,
    # sebelum tabel (dan turunannya) ditulis ─────────────────────────────────
    kosong = {"loss_2001_2021_ha": 0.0, "loss_2022_2025_ha": 0.0,
              "sawit_m2_ha": 0.0, "sawit_0_ha": 0.0, "sawit_0_5_ha": 0.0,
              "tol2th_by_year": {}}
    hasil_final = {kode: acc.get(kode, kosong) for kode in parsed}
    galat = cek_jendela(con, hasil_final)
    if galat:
        print(f"GAGAL: {len(galat)} baris tak konsisten dgn wiup_loss, contoh:",
              file=sys.stderr)
        for g in galat[:5]:
            print(f"    {g}", file=sys.stderr)
        con.close()
        return 1

    # ── Cek konsistensi tahunan: SUM(atribusi_sawit_yearly) per konsesi HARUS
    # cocok dgn window loss_sawit_tol2th_ha (varian tol2th) sebelum KEDUA
    # tabel (atribusi_sawit + atribusi_sawit_yearly) ditulis (spec F1) ────────
    galat_tahunan = cek_konsistensi_tahunan(hasil_final)
    if galat_tahunan:
        print(f"GAGAL: {len(galat_tahunan)} baris tak konsisten SUM(tahunan) vs window tol2th, contoh:",
              file=sys.stderr)
        for g in galat_tahunan[:5]:
            print(f"    {g}", file=sys.stderr)
        con.close()
        return 1

    # ── Tulis tabel ───────────────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS atribusi_sawit")
    con.execute(SCHEMA)
    con.execute("DROP TABLE IF EXISTS atribusi_sawit_yearly")
    con.execute(SCHEMA_YEARLY)

    n_ditulis = 0
    n_yearly = 0
    for kode in parsed:
        a = hasil_final[kode]
        l21 = a["loss_2001_2021_ha"]
        con.execute(
            "INSERT INTO atribusi_sawit VALUES (?,?,?,?,?,?,?)",
            (kode, round(l21, 4),
             round(a["sawit_m2_ha"], 4), round(a["sawit_0_5_ha"], 4),
             round(a["sawit_0_ha"], 4),
             round(a["loss_2022_2025_ha"], 4), n_tile_map[kode]))
        n_ditulis += 1
        for y in sorted(a.get("tol2th_by_year", {})):
            val = round(a["tol2th_by_year"][y], 4)
            if val == 0.0:
                continue   # sparse, konsisten dgn wiup_loss_yearly (0 disimpan tersirat)
            con.execute("INSERT INTO atribusi_sawit_yearly VALUES (?,?,?)", (kode, y, val))
            n_yearly += 1
    con.commit()
    print(f"[atribusi-sawit] atribusi_sawit: {n_ditulis} baris ditulis", file=sys.stderr)
    print(f"[atribusi-sawit] atribusi_sawit_yearly: {n_yearly} baris ditulis", file=sys.stderr)

    # ── Ringkasan (dicetak apa adanya, dipakai utk laporan) ───────────────────
    t_loss, t_tol2th, t_jeda5th, t_tahunsama, t_2225 = con.execute(
        "SELECT SUM(loss_2001_2021_ha), SUM(loss_sawit_tol2th_ha), SUM(loss_sawit_jeda5th_ha), "
        "SUM(loss_sawit_tahunsama_ha), SUM(loss_2022_2025_ha) FROM atribusi_sawit").fetchone()
    t_loss = t_loss or 0.0
    print("\n─── RINGKASAN atribusi_sawit (se-Kalimantan) ───")
    print(f"baris ditulis            : {n_ditulis}")
    print(f"total loss_2001_2021_ha  : {t_loss:,.0f}")
    if t_loss > 0:
        print(f"total loss_sawit_tol2th_ha     : {t_tol2th:,.0f}  ({100*t_tol2th/t_loss:.1f}%)")
        print(f"total loss_sawit_tahunsama_ha  : {t_tahunsama:,.0f}  "
              f"({100*t_tahunsama/t_loss:.1f}%)")
        print(f"total loss_sawit_jeda5th_ha    : {t_jeda5th:,.0f}  ({100*t_jeda5th/t_loss:.1f}%)")
    print(f"total loss_2022_2025_ha (tak terperiksa thd sawit): {t_2225:,.0f}")

    print("\nper komoditas (varian loss_sawit_tol2th_ha = patokan):")
    per_kom = con.execute("""
        SELECT g.komoditas, SUM(a.loss_2001_2021_ha) loss, SUM(a.loss_sawit_tol2th_ha) sawit
        FROM atribusi_sawit a JOIN wiup_geoportal g USING(kode_wiup)
        GROUP BY g.komoditas ORDER BY loss DESC
    """).fetchall()
    for kom, loss, sawit in per_kom:
        pct = 100 * sawit / loss if loss else 0.0
        print(f"  {(kom or '(kosong)'):18} loss={loss:12,.0f} ha  "
              f"sawit={sawit:11,.0f} ha  {pct:5.1f}%")

    print(f"\nwaktu jalan pemindaian raster: {elapsed:.0f}s")
    if gagal:
        print(f"konsesi×tile gagal diproses: {len(gagal)} (lihat log stderr di atas)")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
