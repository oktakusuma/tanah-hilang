#!/usr/bin/env python3
"""Generator tile XYZ tahun-tanam sawit (Descals dkk. 2024) — z6..12.

MASALAH YANG DIJAWAB: peta interaktif web butuh lapisan "piksel sawit"
(tahun tanam per piksel) sebagai overlay di atas peta konsesi/Hansen, tapi
95 GeoTIFF sumber (EPSG:4326, ~22 MB/ubin) terlalu besar & format salah utk
disajikan langsung ke browser. Skrip ini merender ulang jadi tile PNG XYZ
standar (skema sama dgn tile Hansen di `scripts/prepare_hansen_tiles.py` /
web-mercator slippy map) yang ringan di-serve statis.

ENCODING (kontrak dgn klien peta web — JANGAN ubah tanpa update konsumen):
  - Kanal R = tahun_tanam - 1988  → 1989..2022 jadi 1..34 (muat di uint8). Ini
    KAPASITAS encoding, bukan jangkauan data: dataset Descals v1.2 sendiri
    hanya berisi tahun tanam 1990-2021 (Zenodo 10.5281/zenodo.13379129).
  - Kanal G = B = 0 (tak dipakai; disediakan agar PNG tetap RGBA standar).
  - Kanal A = 255 pada piksel sawit, 0 selainnya (termasuk area tanpa data).
  - Tile tanpa piksel sawit sama sekali TIDAK ditulis (klien anggap 404 = kosong).
  - Resampling NEAREST wajib — nilai piksel kategorik (tahun), interpolasi
    akan mengarang tahun yang tak pernah ada di data aslinya (lihat alasan
    yang sama di `attribution_sawit.resample_descals_ke_grid`).

Cara pakai:
    python3 scripts/gen_descals_tiles.py
    python3 scripts/gen_descals_tiles.py --zmin 6 --zmax 10 --bbox 110,-2,115,2

Cara dapat ulang (reproduksi utk sidang): jalankan tanpa argumen setelah
`data/external/descals/tiles/*.tif` tersedia (lihat `scripts/fetch_descals.py`).
Deterministik — tile yang sama menghasilkan byte PNG yang sama persis.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds as merc_transform_from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attribution_sawit import DESCALS_DIR  # noqa: E402

TILE_SIZE = 512
ZMIN_DEFAULT = 6
ZMAX_DEFAULT = 12
BBOX_DEFAULT = (108.3, -4.6, 119.8, 4.8)   # Kalimantan (barat, selatan, timur, utara)
OUT_DIR_DEFAULT = Path("data/tiles/descals")
TAHUN_DASAR = 1988   # R = tahun_tanam - TAHUN_DASAR (1989->1, 2022->34) — kapasitas encoding,
                      # bukan jangkauan data (dataset Descals v1.2 sendiri hanya 1990-2021)
TAHUN_MIN = 1989      # ambang bawah yg masih muat di encoding (data asli mulai 1990, jadi ada 1 th margin)
TAHUN_MAX = TAHUN_DASAR + 255   # batas atas yg masih muat di R uint8 (R maks 255)


# ── Tile math (slippy standar; indeks-256 tapi kanvas kita 512px) ─────────────

def _lon(t: float, n: int) -> float:
    return t / n * 360.0 - 180.0


def _lat(t: float, n: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * t / n))))


def tile_bounds_merc(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Bbox EPSG:3857 utk tile XYZ (indeks 256-standar; kanvas kita 512px)."""
    n = 2 ** z
    return transform_bounds("EPSG:4326", "EPSG:3857",
                             _lon(x, n), _lat(y + 1, n), _lon(x + 1, n), _lat(y, n))


def tile_bounds_4326(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Bbox EPSG:4326 (west, south, east, north) utk tile XYZ — filter sumber cepat."""
    n = 2 ** z
    return _lon(x, n), _lat(y + 1, n), _lon(x + 1, n), _lat(y, n)


def xy_range_tiles(z: int, bbox: tuple[float, float, float, float]
                    ) -> tuple[range, range]:
    """Rentang x,y tile yang bersinggungan dgn bbox (west,south,east,north).

    Menghitung langsung dari bbox (bukan menyisir seluruh globe lalu
    membuang) — ini yang membuat subtree z/x/y di luar Kalimantan tak pernah
    disentuh sama sekali.
    """
    w, s, e, n_lat = bbox
    n = 2 ** z

    def lon_to_x(lon: float) -> int:
        return int((lon + 180.0) / 360.0 * n)

    def lat_to_y(lat: float) -> int:
        lat = max(min(lat, 85.0511), -85.0511)
        lat_rad = math.radians(lat)
        return int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi)
                    / 2 * n)

    x0 = max(lon_to_x(w), 0)
    x1 = min(lon_to_x(e), n - 1)
    y0 = max(lat_to_y(n_lat), 0)   # utara -> y terkecil
    y1 = min(lat_to_y(s), n - 1)
    return range(x0, x1 + 1), range(y0, y1 + 1)


# ── PNG tanpa dependensi (zlib + struct saja) ─────────────────────────────────

def tulis_png_rgba(path: Path, r: np.ndarray, a: np.ndarray) -> None:
    """PNG RGBA dari dua array uint8 (512x512) — tanpa Pillow."""
    h, w = r.shape
    raw = np.zeros((h, w, 4), np.uint8)
    raw[..., 0] = r
    raw[..., 3] = a
    lines = b"".join(b"\x00" + raw[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                      + chunk(b"IDAT", zlib.compress(lines, 9)) + chunk(b"IEND", b""))


def baca_png_kanal(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Decode PNG RGBA (ditulis `tulis_png_rgba`) balik ke array R dan A.

    Helper KHUSUS UJI — hanya paham PNG tanpa-interlace, filter "None" tiap
    baris, sama persis dgn yang `tulis_png_rgba` hasilkan. Bukan decoder PNG
    umum.
    """
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"bukan berkas PNG: {path}")
    pos = 8
    idat = b""
    w = h = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk_data = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", chunk_data[:8])
        elif tag == b"IDAT":
            idat += chunk_data
        elif tag == b"IEND":
            break
        pos += 8 + length + 4
    if w is None:
        raise ValueError(f"IHDR tak ditemukan: {path}")
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    r = np.zeros((h, w), np.uint8)
    a = np.zeros((h, w), np.uint8)
    for i in range(h):
        line = raw[i * stride:(i + 1) * stride]
        if line[0] != 0:
            raise ValueError("baca_png_kanal hanya paham filter 'None' (0)")
        row = np.frombuffer(line[1:], np.uint8).reshape(w, 4)
        r[i] = row[:, 0]
        a[i] = row[:, 3]
    return r, a


# ── Sumber Descals: buka tiap ubin sekali, filter bbox murah ─────────────────

class SumberDescals:
    """Cache handle GeoTIFF Descals — dibuka sekali, dipakai ulang lintas tile.

    Bbox tiap ubin dibaca di awal (murah, cuma header) supaya
    `bersinggungan()` bisa memfilter tanpa membuka ulang berkas. Handle GDAL
    dibuka lazy (hanya ubin yang benar-benar tersentuh) dan reproject
    membaca window seperlunya lewat GDAL warp — array penuh (~22 MB/ubin)
    TIDAK dimuat ke memori sekaligus.
    """

    def __init__(self, descals_dir: Path):
        self.entries: list[tuple[Path, tuple[float, float, float, float]]] = []
        for p in sorted(Path(descals_dir).glob("*.tif")):
            with rasterio.open(p) as src:
                b = src.bounds
                self.entries.append((p, (b.left, b.bottom, b.right, b.top)))
        self._handles: dict[Path, rasterio.io.DatasetReader] = {}

    def bersinggungan(self, bbox4326: tuple[float, float, float, float]) -> list[Path]:
        minx, miny, maxx, maxy = bbox4326
        return [p for p, (l, b, r, t) in self.entries
                if not (r <= minx or l >= maxx or t <= miny or b >= maxy)]

    def buka(self, path: Path) -> rasterio.io.DatasetReader:
        if path not in self._handles:
            self._handles[path] = rasterio.open(path)
        return self._handles[path]

    def tutup_semua(self) -> None:
        for h in self._handles.values():
            h.close()
        self._handles.clear()


# ── Render per tile ────────────────────────────────────────────────────────

def render_tile(z: int, x: int, y: int, sumber: SumberDescals, out_dir: Path
                 ) -> int | None:
    """Render satu tile z/x/y; None kalau tak ada piksel sawit (tak ditulis)."""
    kandidat = sumber.bersinggungan(tile_bounds_4326(z, x, y))
    if not kandidat:
        return None

    minx, miny, maxx, maxy = tile_bounds_merc(z, x, y)
    dst_transform = merc_transform_from_bounds(minx, miny, maxx, maxy,
                                                TILE_SIZE, TILE_SIZE)
    dst = np.zeros((TILE_SIZE, TILE_SIZE), np.uint16)
    for p in kandidat:
        src = sumber.buka(p)
        tmp = np.zeros((TILE_SIZE, TILE_SIZE), np.uint16)
        reproject(
            source=rasterio.band(src, 1),
            destination=tmp,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:3857",
            resampling=Resampling.nearest,
        )
        # `maximum` (bukan "file terakhir menang") — konsisten dgn
        # attribution_sawit.resample_descals_ke_grid utk masalah identik:
        # 0 berarti "bukan sawit ATAU di luar cakupan ubin ini", jadi nilai
        # taknol dari ubin mana pun yg menang, tak bergantung urutan glob.
        dst = np.maximum(dst, tmp)

    if dst.max() == 0:
        return None

    tak_valid = (dst > 0) & ((dst < TAHUN_MIN) | (dst > TAHUN_MAX))
    n_invalid = int(tak_valid.sum())
    if n_invalid:
        print(f"[gen_descals_tiles] peringatan: {n_invalid} piksel tile z{z}/{x}/{y} "
              f"bertahun di luar rentang valid [{TAHUN_MIN},{TAHUN_MAX}] — "
              "di-mask jadi bukan-sawit (dianggap data korup, bukan diformat ulang)",
              file=sys.stderr)
        dst = np.where(tak_valid, 0, dst)
        if dst.max() == 0:
            return None

    r = np.where(dst > 0, dst - TAHUN_DASAR, 0).astype(np.uint8)
    a = np.where(dst > 0, 255, 0).astype(np.uint8)
    out_path = out_dir / str(z) / str(x) / f"{y}.png"
    tulis_png_rgba(out_path, r, a)
    return out_path.stat().st_size


# ── CLI ────────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--descals-dir", default=str(DESCALS_DIR))
    ap.add_argument("--out", default=str(OUT_DIR_DEFAULT))
    ap.add_argument("--bbox", default=",".join(str(v) for v in BBOX_DEFAULT),
                     help="west,south,east,north (EPSG:4326)")
    ap.add_argument("--zmin", type=int, default=ZMIN_DEFAULT)
    ap.add_argument("--zmax", type=int, default=ZMAX_DEFAULT)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> tuple[int, int]:
    args = parse_args(argv)
    bbox = tuple(float(v) for v in args.bbox.split(","))
    if len(bbox) != 4:
        raise SystemExit("--bbox harus 4 angka: west,south,east,north")

    sumber = SumberDescals(Path(args.descals_dir))
    out_dir = Path(args.out)
    n_tertulis = 0
    total_bytes = 0
    try:
        for z in range(args.zmin, args.zmax + 1):
            xs, ys = xy_range_tiles(z, bbox)
            for x in xs:
                for y in ys:
                    n = render_tile(z, x, y, sumber, out_dir)
                    if n is not None:
                        n_tertulis += 1
                        total_bytes += n
    finally:
        sumber.tutup_semua()

    print(f"gen_descals_tiles: {n_tertulis} tile ditulis, "
          f"{total_bytes:,} byte total ({out_dir})")
    return n_tertulis, total_bytes


if __name__ == "__main__":
    main()
