"""Helper geospasial bersama untuk pipeline analisis raster.

Diekstrak dari raster_analyze.py agar build_tampalan_tables.py memakai logika
pilih-tile dan konversi piksel→hektar yang SAMA PERSIS. Duplikasi logika ini
pernah jadi sumber selisih luas antar tabel.

Fokus modul ini: raster LOKAL di data/raster/ (sudah diunduh download_hansen.py).
raster_analyze.py tetap punya alur /vsicurl-nya sendiri.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds

PIXEL_DEG = 0.00025          # resolusi tile Hansen (≈27,8 m di ekuator)
DEG_LAT_METERS = 111_320.0   # 1° lintang ≈ 111,32 km (WGS84)
HANSEN_VERSION = "GFC-2025-v1.13"
RASTER_DIR = Path("data/raster")


def pick_tile(min_lat: float, max_lat: float,
              min_lon: float, max_lon: float) -> list[str]:
    """Tile Hansen 10°×10° yang menutupi bbox. Nama tile = sudut KIRI-ATAS."""
    tiles = set()
    lat = math.floor(min_lat / 10) * 10
    while lat <= math.floor(max_lat / 10) * 10:
        lon = math.floor(min_lon / 10) * 10
        while lon <= math.floor(max_lon / 10) * 10:
            top = lat + 10
            ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
            ew = f"{abs(lon):03d}{'E' if lon >= 0 else 'W'}"
            tiles.add(f"{ns}_{ew}")
            lon += 10
        lat += 10
    return sorted(tiles)


def pixel_area_ha(lat: float) -> float:
    """Luas satu piksel Hansen pada lintang tertentu, dalam hektar."""
    width_m = PIXEL_DEG * DEG_LAT_METERS * math.cos(math.radians(lat))
    height_m = PIXEL_DEG * DEG_LAT_METERS
    return (width_m * height_m) / 10_000


def lossyear_path(tile: str, raster_dir: Path = RASTER_DIR) -> Path:
    """Jalur berkas lossyear lokal untuk satu tile."""
    return Path(raster_dir) / f"Hansen_{HANSEN_VERSION}_lossyear_{tile}.tif"


def treecover_path(tile: str, raster_dir: Path = RASTER_DIR) -> Path:
    """Jalur berkas treecover2000 lokal untuk satu tile (filter kanopi 30%)."""
    return Path(raster_dir) / f"Hansen_{HANSEN_VERSION}_treecover2000_{tile}.tif"


def baca_window(tile_path: Path, bounds: tuple):
    """Baca jendela raster yang menutupi bounds.

    Mengembalikan (data, transform, px_ha). Jendela dipadding 1 piksel agar
    tepi poligon tak terpotong; px_ha memakai lintang TENGAH bounds.
    """
    minx, miny, maxx, maxy = bounds
    with rasterio.open(tile_path) as src:
        win = from_bounds(minx, miny, maxx, maxy, src.transform)
        win = win.round_offsets().round_lengths()
        win = Window(
            col_off=max(int(win.col_off) - 1, 0),
            row_off=max(int(win.row_off) - 1, 0),
            width=int(win.width) + 2,
            height=int(win.height) + 2,
        )
        data = src.read(1, window=win)
        transform = src.window_transform(win)
    return data, transform, pixel_area_ha((miny + maxy) / 2)


def mask_dari(geoms: list[dict], out_shape: tuple, transform) -> np.ndarray:
    """Rasterize daftar geometri GeoJSON jadi mask boolean."""
    if not geoms:
        return np.zeros(out_shape, dtype=bool)
    burned = rasterize(
        [(g, 1) for g in geoms],
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype=np.uint8,
    )
    return burned.astype(bool)
