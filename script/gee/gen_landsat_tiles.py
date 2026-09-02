#!/usr/bin/env python3
"""Bangun tile XYZ citra dasar sezaman untuk peta webapp.

DUA SUMBER, satu mesin tile:
  --sumber landsat   2009-2015, Landsat 5/7/8 30 m  (komposit_landsat.py)
  --sumber sentinel  2016-2018, Sentinel-2 10 m     (komposit_sentinel.py)
Berkas ini tetap bernama gen_landsat_tiles.py karena sudah dirujuk README,
KATALOG-DATA, dan panduan QGIS; isinya generik.

MENGAPA ADA: citra dasar Esri hanya merekam 2021-2025 dan Sentinel-2 baru mulai
2016, jadi menggeser slider tahun ke 2009-2015 dulu tetap menampilkan citra masa
kini — konsesi tampak sudah terbuka padahal saat itu masih hutan. Lapisan ini
menambal lubang tersebut dengan komposit Landsat sezaman.

CAKUPAN BERTINGKAT (keputusan igoen 24 Agu):
  z6-z11  seluruh Kalimantan  — peta terasa sezaman ke mana pun digeser
  z12     hanya sekitar konsesi (bbox WIUP + penyangga ~2 km) — detail penuh
          tepat di wilayah yang diperiksa
Sebabnya z12 sendirian menelan 17.640 tile/tahun untuk seluruh pulau, vs 1.950
tile untuk konsesi saja; membatasi zoom terdalam menghemat ~13 jam pembangunan
tanpa mengorbankan satu pun konsesi. Di luar cakupan (dan di z13+ luar konsesi),
citra dasar biasa (Esri/Sentinel-2) tetap terlihat karena lapisan ini digambar
DI ATASNYA, bukan menggantikannya.

BATAS z12: Landsat 30 m ~ resolusi tile z12 (~38 m/piksel di khatulistiwa).
Menghasilkan z13+ hanya memperbesar piksel yang sama, jadi berhenti di z12.

Tile diambil langsung dari layanan peta GEE (getMapId -> URL XYZ), bukan lewat
GeoTIFF + tiling lokal: petaknya persis sama dengan petak Leaflet sehingga tak
ada resampling/pergeseran, dan tak perlu ruang disk untuk raster antara.

Jalankan:  .venv/bin/python scripts/gee/gen_landsat_tiles.py --tahun 2009-2015
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ee

sys.path.insert(0, str(Path(__file__).resolve().parent))
import komposit_landsat as MLANDSAT  # noqa: E402
import komposit_sentinel as MSENTINEL  # noqa: E402
from komposit_landsat import masuk  # noqa: E402

SUMBER = {"landsat": MLANDSAT, "sentinel": MSENTINEL}

AKAR = Path(__file__).resolve().parents[2]
DB = AKAR / "data" / "kalimantan.db"
KELUAR = AKAR / "data" / "tiles" / "landsat"
# Kedalaman zoom mengikuti resolusi sumber: tile z12 ~ 38 m/piksel di
# khatulistiwa, tiap tingkat membagi dua. Landsat 30 m -> native z12;
# Sentinel-2 10 m -> native z14.
#
# Zoom terdalam SENGAJA dibatasi di bawah native untuk cakupan pulau, karena
# tiap tingkat melipatempatkan jumlah tile: z12 se-Kalimantan butuh 17.640 tile
# per tahun, z13 butuh 70.000. Yang dikorbankan cuma ketajaman di luar konsesi,
# tempat tak ada yang diperiksa.
#
# Sentinel-2 BELUM sampai z14 (native-nya) karena ongkos EECU: z14 konsesi
# menambah 26.184 tile/tahun, dan pada 24 Agu jatah bulanan sudah terpakai
# ~42%. Rencananya ditambah setelah jatah reset 1 September.
ZOOM_SKEMA = {
    "landsat":  {"pulau": range(6, 12), "konsesi": (12,)},   # native z12
    "sentinel": {"pulau": range(6, 13), "konsesi": (13,)},   # native z14
}
KALIMANTAN = (108.5, -4.5, 119.5, 7.7)   # bbox pulau
PENYANGGA = 0.02          # derajat, ~2,2 km
TANGGA_AWAN = (80, 60, 40)
# Jumlah unduhan paralel. Hampir seluruh waktu tiap permintaan dihabiskan
# MENUNGGU GEE menghitung komposit, bukan memakai CPU di sini — jadi jumlah
# pekerja boleh jauh melebihi jumlah inti. Batas sebenarnya ada di kuota
# "read requests per minute" GEE (6.000); pada 24 pekerja kita masih di
# kisaran 800/menit, ~13% kuota. Menaikkan pekerja TIDAK menambah biaya
# EECU — jumlah tile yang dihitung tetap sama, hanya selesai lebih cepat.
PEKERJA = 24


def petak(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Koordinat tile Web Mercator (XYZ) untuk satu titik."""
    n = 2 ** z
    lat = max(-85.05, min(85.05, lat))
    return (int((lon + 180.0) / 360.0 * n),
            int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n))


def _grid(kotak: list[tuple[float, float, float, float]], z: int,
          penyangga: float) -> set[tuple[int, int]]:
    s: set[tuple[int, int]] = set()
    for lo1, la1, lo2, la2 in kotak:
        x0, y0 = petak(lo1 - penyangga, la2 + penyangga, z)
        x1, y1 = petak(lo2 + penyangga, la1 - penyangga, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                s.add((x, y))
    return s


def daftar_petak(skema: dict) -> dict[int, set[tuple[int, int]]]:
    """Himpunan tile per zoom: pulau penuh di zoom dangkal, konsesi di terdalam."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    kotak = con.execute(
        "SELECT bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat "
        "FROM wiup_geoportal WHERE bbox_min_lon IS NOT NULL").fetchall()
    con.close()
    hasil: dict[int, set[tuple[int, int]]] = {}
    for z in skema["pulau"]:
        hasil[z] = _grid([KALIMANTAN], z, 0.0)
    for z in skema["konsesi"]:
        hasil[z] = _grid(kotak, z, PENYANGGA)
    return hasil


def wilayah_kalimantan() -> ee.Geometry:
    """Kotak pembatas pulau — dipakai untuk filterBounds koleksi sumber."""
    return ee.Geometry.Rectangle(list(KALIMANTAN), None, False)


def peta_tahun(tahun: int, lebar: int, mod) -> str:
    """URL template XYZ untuk komposit `tahun`; turunkan ambang awan bila GEE
    menolak karena batas memori (perilaku sama dgn komposit_landsat.unduh)."""
    wil = wilayah_kalimantan()
    galat = ""
    for awan in mod.TANGGA_AWAN:
        img = mod.komposit(tahun, wil, lebar, awan).visualize(**mod.VIS)
        try:
            mid = ee.data.getMapId({"image": img})
        except Exception as e:  # noqa: BLE001 — pesan GEE tak berkelas khusus
            galat = str(e)
            if "memory" not in galat.lower():
                raise
            print(f"  batas memori pada awan<{awan}% — coba lebih ketat", flush=True)
            continue
        return mid["tile_fetcher"].url_format
    raise SystemExit(f"getMapId gagal utk {tahun}: {galat[:300]}")


def ambil(url: str, tujuan: Path) -> str:
    """Unduh satu tile. Balikan: 'baru' | 'lewat' (kosong/transparan) | 'ada'."""
    if tujuan.exists():
        return "ada"
    for coba in range(5):
        try:
            data = urllib.request.urlopen(url, timeout=180).read()
        except urllib.error.HTTPError as e:
            # 4xx selain 429 = permintaan salah, mengulang tak menolong.
            if e.code != 429 and 400 <= e.code < 500:
                raise SystemExit(f"HTTP {e.code} utk {url[:120]}")
            time.sleep(2 ** coba)
            continue
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** coba)
            continue
        # Tile sepenuhnya transparan (di luar jangkauan citra) tak ditulis —
        # server membalas 404 dan Leaflet cukup menampilkan citra dasar di
        # bawahnya. Ambang 1 KB: PNG transparan polos jauh di bawah itu.
        if len(data) < 1024:
            return "lewat"
        tujuan.parent.mkdir(parents=True, exist_ok=True)
        tmp = tujuan.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(tujuan)
        return "baru"
    return "lewat"


def kerjakan_tahun(tahun: int, lebar: int, petak_z: dict[int, set],
                   pekerja: int = PEKERJA, mod=MLANDSAT) -> None:
    tmpl = peta_tahun(tahun, lebar, mod)
    tugas = [(z, x, y) for z in sorted(petak_z) for (x, y) in sorted(petak_z[z])]
    total = len(tugas)
    hitung = {"baru": 0, "lewat": 0, "ada": 0}
    mulai = time.time()

    def satu(t):
        z, x, y = t
        return ambil(tmpl.format(z=z, x=x, y=y),
                     KELUAR / str(tahun) / str(z) / str(x) / f"{y}.png")

    with ThreadPoolExecutor(max_workers=pekerja) as ex:
        for i, hasil in enumerate(ex.map(satu, tugas), 1):
            hitung[hasil] += 1
            if i % 200 == 0 or i == total:
                lewat = time.time() - mulai
                sisa = lewat / i * (total - i)
                print(f"  {tahun}: {i}/{total} baru={hitung['baru']} "
                      f"kosong={hitung['lewat']} ada={hitung['ada']} "
                      f"sisa~{sisa/60:.0f} mnt", flush=True)
    print(f"{tahun} selesai dalam {(time.time()-mulai)/60:.1f} mnt: {hitung}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tahun", default="2009-2015", help="mis. 2009-2015 atau 2011")
    ap.add_argument("--lebar", type=int, default=1)
    ap.add_argument("--pekerja", type=int, default=PEKERJA,
                    help=f"unduhan paralel (default {PEKERJA})")
    ap.add_argument("--sumber", choices=sorted(SUMBER), default="landsat")
    a = ap.parse_args()
    if "-" in a.tahun:
        t1, t2 = (int(v) for v in a.tahun.split("-"))
        tahun = list(range(t1, t2 + 1))
    else:
        tahun = [int(a.tahun)]

    masuk()
    mod = SUMBER[a.sumber]
    petak_z = daftar_petak(ZOOM_SKEMA[a.sumber])
    for z in sorted(petak_z):
        print(f"z{z}: {len(petak_z[z]):,} tile")
    print(f"sumber {a.sumber}; total per tahun: "
          f"{sum(len(s) for s in petak_z.values()):,} tile", flush=True)
    for t in tahun:
        kerjakan_tahun(t, a.lebar, petak_z, a.pekerja, mod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
