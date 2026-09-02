"""Pustaka bersama W2 (MapBiomas) untuk `04_mapbiomas.py` dan `05_transisi.py`.

Isi: legenda resmi Koleksi 4.1 (nama, kelompok, warna), definisi kategori gabungan buatan
sendiri, pembacaan geometri konsesi dari DB target, rasterisasi topeng poligon SEKALI per
konsesi, dan luas piksel terkoreksi lintang. Logika di-port dari
`scripts/mapbiomas/landuse_konsesi.py` (pipeline arsip) — bukan ditulis ulang — supaya
angkanya identik piksel demi piksel dengan `data/arsip/mapbiomas.db`.

Empat hal yang menentukan angkanya bisa dipercaya (dibawa utuh dari pipeline arsip):
1. TANPA reproyeksi/resampling: raster nasional dibaca di grid aslinya (EPSG:4326, ~30 m);
   luas per baris dikoreksi cos(lintang) dengan konstanta `DEG_LAT_METERS` yang SAMA dengan
   pipeline Hansen (`scripts/_geo_common.py`), ukuran piksel dibaca dari transform raster.
2. Grid ke-24 raster diverifikasi identik sebelum menghitung.
3. Topeng poligon dirasterisasi sekali per konsesi (`all_touched=False`, sama dgn Hansen)
   lalu dipakai ulang untuk semua tahun → jumlah piksel per konsesi pasti konstan.
4. Geometri dibaca dari tabel `konsesi` DB TARGET (bukan `kalimantan.db`), dan sidik jarinya
   (`bangun.mapbiomas.hash_geometri`) disimpan supaya tabel MapBiomas tak bisa basi diam-diam.
"""
from __future__ import annotations

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

from .db import AKAR, gagal

# Konstanta lintang WAJIB dari _geo_common (jangan definisi ulang — duplikasi pernah jadi
# sumber selisih luas antar tabel di pipeline arsip).
sys.path.insert(0, str(AKAR / "scripts"))
import _geo_common as gc  # noqa: E402

TAHUN_MIN, TAHUN_MAX = 2001, 2024              # jendela tesis; raster 2000 SENGAJA tak dipakai
TAHUN = list(range(TAHUN_MIN, TAHUN_MAX + 1))
DIR_RASTER = AKAR / "data/external/mapbiomas"
MANIFEST = AKAR / "scripts/mapbiomas/manifest_c41_2000_2024.csv"
URL_MAPBIOMAS = "https://landy.mapbiomas.id"
NKELAS = 256                                   # raster int8: kode kelas 0..127

KELAS_NODATA, KELAS_AWAN, KELAS_TAMBANG, KELAS_SAWIT = 0, 27, 30, 35
KELAS_HUTAN = (3, 5, 76)

# Legenda resmi Koleksi 4.1 (berbahasa Indonesia). Nama & kelompok = legenda resmi seperti
# dipakai pipeline arsip (`landuse_konsesi.LEGEND`, penyesuaian igoen 23 Agu 2026); hex =
# `webapp/src/lib/mapbiomasLegend.ts::MAPBIOMAS_KELAS`. Kelas 10 & 27 tak pernah muncul di
# dalam konsesi Kalimantan, tapi tetap disimpan karena ada di legenda (10 = kode level-1
# Tumbuhan Non-Hutan; 27 = citra tertutup awan). Diuji sinkron dgn kedua sumber di
# pipeline/tests/test_w2.py.
LEGENDA: list[tuple[int, str, str, str]] = [
    (3,  "Formasi Hutan",              "Hutan",               "#1f8d49"),
    (5,  "Mangrove",                   "Hutan",               "#04381d"),
    (76, "Hutan Rawa Gambut",          "Hutan",               "#2f7360"),
    (10, "Tumbuhan Non-Hutan",         "Tumbuhan Non-Hutan",  "#d6bc74"),
    (13, "Tumbuhan Non-Hutan Lainnya", "Tumbuhan Non-Hutan",  "#d89f5c"),
    (40, "Sawah",                      "Pertanian",           "#c71585"),
    (35, "Sawit",                      "Pertanian",           "#9065d0"),
    (9,  "Kebun Kayu",                 "Pertanian",           "#7a5900"),
    (21, "Pertanian Lainnya",          "Pertanian",           "#ffefc3"),
    (30, "Lubang Tambang",             "Non-Vegetasi",        "#9c0027"),
    (24, "Permukiman",                 "Non-Vegetasi",        "#d4271e"),
    (25, "Non-Vegetasi Lainnya",       "Non-Vegetasi",        "#db4d4f"),
    (31, "Tambak",                     "Tubuh Air",           "#091077"),
    (33, "Sungai, Danau, Laut",        "Tubuh Air",           "#2532e4"),
    (27, "Citra Tertutup Awan",        "Citra Tertutup Awan", "#ffffff"),
]
KODE_LEGENDA = {k for k, _, _, _ in LEGENDA}

# Kategori GABUNGAN buatan sendiri — BUKAN kelas resmi MapBiomas. Wajib diberi keterangan
# di setiap pemakaian (kaveat §13 landasan). Disimpan di tabel `mapbiomas_gabungan`.
_KET_HUTAN = ("Kategori buatan sendiri, BUKAN kelas resmi MapBiomas: gabungan tiga kelas hutan "
              "alam (Formasi Hutan 3 + Mangrove 5 + Hutan Rawa Gambut 76). Beda definisi dari "
              "'tutupan pohon' Hansen (kanopi >=30%) — jangan disandingkan sebagai angka setara.")
_KET_TANI = ("Kategori buatan sendiri, BUKAN kelas resmi MapBiomas: pertanian selain sawit "
             "(Kebun Kayu 9 + Pertanian Lainnya 21 + Sawah 40). Sawit (35) dipisah karena "
             "menjadi pokok bahasan tersendiri (RQ c).")
_KET_AIR = ("Kategori buatan sendiri, BUKAN kelas resmi MapBiomas: seluruh tubuh air "
            "(Tambak 31 + Sungai, Danau, Laut 33). Dipakai utk hipotesis 'tubuh air sebagai "
            "tanda tangan buka-tutup tambang' (belum diuji).")
GABUNGAN: list[tuple[str, int, str]] = [
    ("Hutan", 3, _KET_HUTAN), ("Hutan", 5, _KET_HUTAN), ("Hutan", 76, _KET_HUTAN),
    ("Pertanian non-sawit", 9, _KET_TANI), ("Pertanian non-sawit", 21, _KET_TANI),
    ("Pertanian non-sawit", 40, _KET_TANI),
    ("Tubuh air", 31, _KET_AIR), ("Tubuh air", 33, _KET_AIR),
]

SUMBER_RINGKAS = ("MapBiomas Indonesia Koleksi 4.1 (CC BY-SA) — raster nasional coverage_lclu "
                  f"{TAHUN_MIN}-{TAHUN_MAX} (data/external/mapbiomas, manifest MD5 "
                  "scripts/mapbiomas/manifest_c41_2000_2024.csv); geometri = tabel konsesi DB ini")


def path_raster(tahun: int) -> Path:
    return DIR_RASTER / f"mapbiomas_c41_{tahun}.tif"


def wajib_raster() -> dict[int, Path]:
    """Gagal keras bila satu pun raster 2001–2024 absen."""
    berkas = {t: path_raster(t) for t in TAHUN}
    kurang = [p.name for p in berkas.values() if not p.exists()]
    if kurang:
        gagal(f"raster MapBiomas absen di {DIR_RASTER}: {', '.join(kurang)} — "
              "unduh lewat scripts/mapbiomas/fetch_mapbiomas_lulc.py")
    return berkas


def tanggal_akses() -> dt.date:
    """Tanggal unduh raster = mtime berkas tertua yang benar-benar dipakai (bukan ditulis
    tangan, supaya sitasi tak basi saat pipeline dijalankan ulang)."""
    tertua = min(path_raster(t).stat().st_mtime for t in TAHUN)
    return dt.date.fromtimestamp(tertua)


def sitasi() -> str:
    """Format sitasi persis yang diminta https://landy.mapbiomas.id/id/termsofuse."""
    return ("MapBiomas Indonesia - Koleksi 4.1 peta waktu penggunaan lahan dan tutupan lahan, "
            f"diakses pada {tanggal_akses().strftime('%d/%m/%Y')} melalui: {URL_MAPBIOMAS}")


def cek_himpunan(con: sqlite3.Connection, himpunan: str) -> None:
    """Bila 01_identitas sudah mencatat `bangun.himpunan`, argumen --himpunan wajib sama."""
    r = con.execute("SELECT nilai FROM bangun WHERE kunci='himpunan'").fetchone() \
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='bangun'").fetchone() else None
    if r and r[0] != himpunan:
        gagal(f"--himpunan {himpunan} tidak cocok dengan bangun.himpunan={r[0]} di DB target")


def baca_konsesi(con: sqlite3.Connection) -> list[tuple[str, dict, int | None]]:
    """(kode_wiup, geometri dict, tahun_izin) dari tabel `konsesi` DB TARGET."""
    rows = con.execute(
        "SELECT kode_wiup, geometri_geojson, tahun_izin FROM konsesi "
        "WHERE geometri_geojson IS NOT NULL ORDER BY kode_wiup").fetchall()
    if not rows:
        gagal("tabel konsesi kosong / tanpa geometri di DB target")
    return [(k, json.loads(g), (int(t) if t is not None else None)) for k, g, t in rows]


def area_baris_ha(win_transform, height: int, width: int) -> np.ndarray:
    """Luas piksel (ha) per baris jendela, koreksi lintang cos(lat) — rumus identik
    `_geo_common.pixel_area_ha`, ukuran piksel dari transform raster MapBiomas."""
    px_x = abs(win_transform.a)
    px_y = abs(win_transform.e)
    lats = win_transform.f + (np.arange(height) + 0.5) * win_transform.e   # lintang pusat baris
    width_m = px_x * gc.DEG_LAT_METERS * np.cos(np.radians(lats))
    height_m = px_y * gc.DEG_LAT_METERS
    per_row = (width_m * height_m) / 10_000.0
    return np.broadcast_to(per_row[:, None], (height, width))


def jendela_konsesi(geom, ref) -> Window | None:
    """Jendela baca (di-clamp ke raster) untuk bbox geometri; None bila di luar raster."""
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


def siapkan_konsesi(konsesi, ref):
    """Per konsesi: (kode, window, mask bool, grid luas ha). Topeng dirasterisasi SEKALI di
    grid referensi lalu dipakai ulang untuk semua tahun. Mengembalikan (hasil, kode_luar)."""
    hasil, luar = [], []
    for kode, geom, *_ in konsesi:
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
        print(f"PERINGATAN: {len(luar)} konsesi tanpa piksel di raster (di luar cakupan / "
              f"terlalu kecil untuk grid 30 m): {luar[:5]}{'…' if len(luar) > 5 else ''}",
              file=sys.stderr)
    return hasil, luar


def cek_grid_sama(ref, path: Path) -> None:
    with rasterio.open(path) as ds:
        if (ds.shape != ref.shape) or (ds.transform != ref.transform):
            gagal(f"grid {path.name} tidak identik dengan raster referensi — semua raster "
                  "MapBiomas harus satu grid; unduh ulang & cek manifest MD5")


def buka_raster_tercek(berkas: dict[int, Path]):
    """Buka raster referensi (tahun pertama) setelah memastikan semua grid identik."""
    ref = rasterio.open(berkas[TAHUN[0]])
    for t in TAHUN[1:]:
        cek_grid_sama(ref, berkas[t])
    return ref


def hash_geometri_cocok(con: sqlite3.Connection) -> tuple[str, str]:
    """(konsesi.hash_geometri, mapbiomas.hash_geometri) dari tabel bangun; gagal bila absen/beda."""
    from .db import baca_bangun, hash_geometri
    hk = baca_bangun(con, "konsesi.hash_geometri")
    hm = baca_bangun(con, "mapbiomas.hash_geometri")
    if not hk:
        gagal("bangun.konsesi.hash_geometri belum ada — jalankan 01_identitas.py dulu")
    if not hm:
        gagal("bangun.mapbiomas.hash_geometri belum ada — jalankan 04_mapbiomas.py dulu")
    if hk != hm:
        gagal("hash geometri MapBiomas != konsesi — tabel konsesi berubah sesudah 04_mapbiomas; "
              "jalankan ulang 04_mapbiomas.py")
    if hash_geometri(con) != hk:
        gagal("bangun.konsesi.hash_geometri tidak sama dengan isi tabel konsesi saat ini — "
              "jalankan ulang 01_identitas.py lalu 04_mapbiomas.py")
    return hk, hm
