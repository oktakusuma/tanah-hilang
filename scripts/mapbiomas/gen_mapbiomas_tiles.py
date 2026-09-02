#!/usr/bin/env python3
# STATUS  : DI LUAR PIPELINE — tile XYZ kelas MapBiomas untuk latar web; bukan sumber angka
# CATATAN : tile data WAJIB lossless (kanal R menyimpan kode kelas)
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""
Generator tile XYZ untuk tutupan lahan MapBiomas Indonesia Koleksi 4.1.
Saudara kandung gen_descals_tiles.py — konvensi jalur, ukuran kanvas, dan
kontrak encoding-nya sengaja dibuat sama supaya pewarna sisi klien yang sudah
ada bisa dipakai ulang.

KONTRAK ENCODING (beda dari Descals — di sana R = tahun; di sini R = KELAS)
    Kanal R = kode kelas MapBiomas (3,5,9,10,13,21,24,25,27,30,31,33,35,40,76)
    Kanal G = B = 0
    Kanal A = 255 pada piksel terklasifikasi, 0 selainnya
Warna TIDAK dibakar ke PNG. Klien yang memetakan kode -> warna, sehingga
palet bisa diubah dan tiap kelas bisa dinyalakan/dimatikan tanpa render ulang.

Karena satu pohon tile per tahun, jalur keluarannya bertingkat tahun:
    data/tiles/mapbiomas/{tahun}/{z}/{x}/{y}.png

Catatan zoom: kanvas 512 px, jadi z11 ~ 38 m/px di khatulistiwa — sudah lebih
halus dari piksel MapBiomas 30 m. z12 hanya memperbesar, tidak menambah
informasi. Karena itu ZMAX_DEFAULT = 11 (Descals boleh 12; datanya sama-sama
30 m, tapi di sana tile-nya jarang sehingga murah).

Sumber dibaca dari GeoTIFF hasil 01_clip_kalimantan.* (clip_wgs84/) atau
langsung dari COG publik lewat /vsicurl + manifest_c41_national.csv.

Contoh:
    python3 gen_mapbiomas_tiles.py --src data/external/mapbiomas --pattern 'mapbiomas_c41_*.tif' --tahun 2001-2024
    python3 gen_mapbiomas_tiles.py --remote --tahun 2001-2024 --zmax 10
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds as merc_transform_from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

TILE_SIZE = 512
ZMIN_DEFAULT = 6
ZMAX_DEFAULT = 11
BBOX_DEFAULT = (108.3, -4.6, 119.8, 4.8)
OUT_DIR_DEFAULT = Path("data/tiles/mapbiomas")
MANIFEST_DEFAULT = Path(__file__).with_name("manifest_c41_national.csv")
GCS_BASE = "https://storage.googleapis.com/mapbiomas-downloads/public/indonesia/maps"

KODE_KELAS = (3, 5, 9, 10, 13, 21, 24, 25, 27, 30, 31, 33, 35, 40, 76)

R_EARTH = 6378137.0
MERC_MAX = math.pi * R_EARTH


# ---------------------------------------------------------------- PNG (zlib saja)
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def tulis_png_rgba(path: Path, r: np.ndarray, a: np.ndarray) -> None:
    """Tulis PNG RGBA 8-bit: R = kode kelas, G = B = 0, A = mask."""
    h, w = r.shape
    z = np.zeros_like(r)
    px = np.dstack([r, z, z, a]).astype(np.uint8)
    raw = b"".join(b"\x00" + px[i].tobytes() for i in range(h))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b""))


# ---------------------------------------------------------------- geometri tile
def tile_bounds_merc(z: int, x: int, y: int):
    n = 2 ** z
    span = 2 * MERC_MAX / n
    return (-MERC_MAX + x * span, MERC_MAX - (y + 1) * span,
            -MERC_MAX + (x + 1) * span, MERC_MAX - y * span)


def tile_range(bbox, z: int):
    w, s, e, n_ = bbox
    maxi = 2 ** z - 1
    klem = lambda v: max(0, min(v, maxi))  # jaga bbox pengguna tetap di dalam dunia
    xt = lambda lon: klem(int((lon + 180.0) / 360.0 * 2 ** z))
    def yt(lat):
        lat = max(min(lat, 85.0511), -85.0511)
        r = math.radians(lat)
        return klem(int((1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * 2 ** z))
    return range(xt(w), xt(e) + 1), range(yt(n_), yt(s) + 1)


# ---------------------------------------------------------------- sumber
def sumber_lokal(src_dir: Path, pattern: str):
    out = {}
    for f in sorted(src_dir.glob(pattern)):
        m = re.search(r"(19|20)\d{2}", f.name)
        if m:
            out[int(m.group(0))] = str(f)
    return out


def sumber_remote(manifest: Path):
    out = {}
    for row in csv.DictReader(open(manifest)):
        y, u = row["year"], row["uuid"]
        out[int(y)] = f"/vsicurl/{GCS_BASE}/{u}/{y}_coverage_lclu_4-1-1_{u}.tif"
    return out


def parse_tahun(s: str):
    hasil = []
    for bagian in s.split(","):
        bagian = bagian.strip()
        if "-" in bagian:
            a, b = bagian.split("-")
            hasil.extend(range(int(a), int(b) + 1))
        elif bagian:
            hasil.append(int(bagian))
    return sorted(set(hasil))


# ---------------------------------------------------------------- inti
def render_tahun(tahun: int, src_path: str, out_dir: Path, bbox, zmin, zmax,
                 sah: np.ndarray, dry: bool) -> tuple[int, int]:
    ditulis = kosong = 0
    buang_total = 0
    with rasterio.open(src_path) as src:
        # Culling dihitung pada bounds sumber yang DITRANSFORM ke 4326 — bukan
        # src.bounds mentah — supaya benar juga bila sumbernya bukan EPSG:4326
        # (mis. keliru menunjuk clip_equalarea/).
        sb = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        for z in range(zmin, zmax + 1):
            d0, k0 = ditulis, kosong
            xs, ys = tile_range(bbox, z)
            for x in xs:
                for y in ys:
                    left, bottom, right, top = tile_bounds_merc(z, x, y)
                    w4326 = transform_bounds("EPSG:3857", "EPSG:4326",
                                             left, bottom, right, top, densify_pts=2)
                    if (w4326[2] < sb[0] or w4326[0] > sb[2]
                            or w4326[3] < sb[1] or w4326[1] > sb[3]):
                        kosong += 1
                        continue

                    dst = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint8)
                    reproject(
                        source=rasterio.band(src, 1), destination=dst,
                        src_transform=src.transform, src_crs=src.crs,
                        dst_transform=merc_transform_from_bounds(
                            left, bottom, right, top, TILE_SIZE, TILE_SIZE),
                        dst_crs="EPSG:3857",
                        src_nodata=0, dst_nodata=0,
                        resampling=Resampling.nearest)   # kategorikal: WAJIB nearest

                    luar = ~sah[dst] & (dst > 0)          # nilai di luar legenda
                    buang_total += int(luar.sum())
                    dst[luar] = 0
                    a = np.where(dst > 0, 255, 0).astype(np.uint8)
                    if not a.any():
                        kosong += 1
                        continue
                    if not dry:
                        tulis_png_rgba(out_dir / str(tahun) / str(z) / str(x) / f"{y}.png",
                                       dst, a)
                    ditulis += 1
            print(f"  {tahun} z{z}: {ditulis - d0} ditulis / {kosong - k0} dilewati",
                  flush=True)
    if buang_total:
        print(f"  PERINGATAN {tahun}: {buang_total:,} piksel bernilai di luar legenda "
              "dibuang (kemungkinan raster korup atau bukan produk coverage_lclu)",
              file=sys.stderr, flush=True)
    return ditulis, kosong


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="clip_wgs84", help="folder GeoTIFF hasil langkah 1")
    ap.add_argument("--pattern", default="kalimantan_lulc_*.tif")
    ap.add_argument("--remote", action="store_true",
                    help="baca COG publik lewat /vsicurl (tanpa unduh dulu)")
    ap.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    ap.add_argument("--tahun", default="2001-2024")
    ap.add_argument("--out", default=str(OUT_DIR_DEFAULT))
    ap.add_argument("--zmin", type=int, default=ZMIN_DEFAULT)
    ap.add_argument("--zmax", type=int, default=ZMAX_DEFAULT)
    ap.add_argument("--bbox", default=",".join(str(v) for v in BBOX_DEFAULT))
    ap.add_argument("--dry-run", action="store_true", help="hitung saja, tak menulis PNG")
    a = ap.parse_args(argv)

    bbox = tuple(float(v) for v in a.bbox.split(","))
    out_dir = Path(a.out)
    sumber = sumber_remote(Path(a.manifest)) if a.remote else sumber_lokal(Path(a.src), a.pattern)
    if not sumber:
        sys.exit("Tidak ada raster sumber. Jalankan 01_clip_kalimantan.* atau pakai --remote.")

    sah = np.zeros(256, dtype=bool)
    for k in KODE_KELAS:
        sah[k] = True

    tahun_diminta = parse_tahun(a.tahun)
    hilang = [t for t in tahun_diminta if t not in sumber]
    if hilang:
        print(f"Peringatan: tahun tanpa sumber, dilewati: {hilang}", file=sys.stderr)

    total_t = total_k = 0
    for t in tahun_diminta:
        if t not in sumber:
            continue
        print(f"=== {t} ===", flush=True)
        d, k = render_tahun(t, sumber[t], out_dir, bbox, a.zmin, a.zmax, sah, a.dry_run)
        total_t += d
        total_k += k

    print(f"\nSelesai. {total_t} tile ditulis, {total_k} dilewati (kosong/di luar jangkauan).")
    if a.dry_run:
        print("(dry-run: tak ada berkas yang ditulis)")


if __name__ == "__main__":
    main()
