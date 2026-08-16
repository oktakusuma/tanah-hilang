"""
Batch raster analysis: tree cover loss per WIUP dengan 30% canopy threshold.

Input  : data/wiup/kalimantan_unique.geojson (filtered by --province)
Tiles  : data/raster/Hansen_GFC-2025-v1.13_{layer}_{tile}.tif (local files)
Output : data/analysis/batch_{province}_t{threshold}.csv (wide format)
         data/analysis/batch_{province}_t{threshold}_long.csv (long format)
         data/analysis/batch_{province}_t{threshold}.meta.json

Metodologi:
- Buka tile lossyear + treecover2000 (lokal, fast)
- Untuk setiap WIUP: read window, rasterize polygon ke mask
- Forest filter: pixel dianggap "forest" kalau treecover2000 >= THRESHOLD %
- Loss = pixel di dalam polygon DAN forest DAN punya nilai loss 1-25
- Area: per-row latitude-corrected (lebih akurat dari mean-lat)

Cross-tile WIUPs: dianalisis di setiap tile yang ter-overlap, hasilnya dijumlah.

Usage:
    python batch_analyze.py --province "KALIMANTAN TIMUR" --threshold 30
"""

import argparse
import csv
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds, Window
from shapely.geometry import shape, box

VERSION = "GFC-2025-v1.13"
PIXEL_DEG = 0.00025
DEG_LAT_METERS = 111_320.0
N_YEARS = 25  # 2001-2025


def pick_tiles(min_lat, max_lat, min_lon, max_lon) -> list[str]:
    """Tile names yang cover bbox."""
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


def row_pixel_area_ha(latitudes: np.ndarray) -> np.ndarray:
    """Per-row pixel area in hectares (latitude-corrected)."""
    # Width in meters varies with cosine of latitude
    width_m = PIXEL_DEG * DEG_LAT_METERS * np.cos(np.radians(latitudes))
    height_m = PIXEL_DEG * DEG_LAT_METERS
    return (width_m * height_m) / 10_000  # m² → ha


def analyze_wiup_in_tile(
    feature: dict,
    lossyear_src: rasterio.io.DatasetReader,
    treecover_src: rasterio.io.DatasetReader,
    threshold: int,
) -> dict | None:
    """Analyze one WIUP within one tile. Returns histogram + area stats."""
    geom = feature["geometry"]
    poly = shape(geom)
    minx, miny, maxx, maxy = poly.bounds

    # Intersect polygon with tile bounds (handle cross-tile)
    tile_bounds = lossyear_src.bounds
    tile_box = box(tile_bounds.left, tile_bounds.bottom,
                   tile_bounds.right, tile_bounds.top)
    if not poly.intersects(tile_box):
        return None
    clipped = poly.intersection(tile_box)
    if clipped.is_empty:
        return None

    cminx, cminy, cmaxx, cmaxy = clipped.bounds

    # Compute window
    try:
        win = from_bounds(cminx, cminy, cmaxx, cmaxy, lossyear_src.transform)
        win = win.round_offsets().round_lengths()
        win = Window(
            col_off=max(int(win.col_off) - 1, 0),
            row_off=max(int(win.row_off) - 1, 0),
            width=int(win.width) + 2,
            height=int(win.height) + 2,
        )
    except Exception as e:
        return {"error": f"window calc failed: {e}"}

    if win.width <= 0 or win.height <= 0:
        return None

    win_transform = lossyear_src.window_transform(win)
    loss = lossyear_src.read(1, window=win)
    tcov = treecover_src.read(1, window=win)

    # Rasterize CLIPPED polygon (so cross-tile parts outside this tile masked off)
    mask = rasterize(
        [(clipped.__geo_interface__, 1)],
        out_shape=loss.shape,
        transform=win_transform,
        fill=0,
        dtype=np.uint8,
    )

    # Forest filter: treecover2000 >= threshold AND inside polygon
    forest_mask = (tcov >= threshold) & (mask == 1)

    # Per-row latitude-corrected area
    row_indices = np.arange(loss.shape[0])
    # Center latitude of each row
    row_lats = win_transform.f + (row_indices + 0.5) * win_transform.e
    row_areas_ha = row_pixel_area_ha(row_lats)
    # Broadcast to 2D
    area_grid = np.broadcast_to(row_areas_ha[:, None], loss.shape)

    # Polygon area (any pixel inside polygon, regardless of forest)
    polygon_area_ha = float(area_grid[mask == 1].sum())

    # Forest area at year 2000 (within polygon)
    forest_area_2000_ha = float(area_grid[forest_mask].sum())

    # Loss per year: hectares lost in forest pixels with loss value y
    loss_per_year_ha = np.zeros(N_YEARS + 1)  # index 0 unused
    for y in range(1, N_YEARS + 1):
        loss_per_year_ha[y] = float(
            area_grid[forest_mask & (loss == y)].sum()
        )

    return {
        "polygon_area_ha": polygon_area_ha,
        "forest_area_2000_ha": forest_area_2000_ha,
        "loss_per_year_ha": loss_per_year_ha[1:].tolist(),
        "n_pixels_polygon": int((mask == 1).sum()),
        "n_pixels_forest": int(forest_mask.sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=Path("data/wiup/kalimantan_unique.geojson"))
    parser.add_argument("--province", type=str, required=True,
                        help="match against nama_prov field (substring)")
    parser.add_argument("--threshold", type=int, default=30,
                        help="canopy cover threshold (percent, default 30)")
    parser.add_argument("--rasterdir", type=Path, default=Path("data/raster"))
    parser.add_argument("--outdir", type=Path, default=Path("data/analysis"))
    args = parser.parse_args()

    # Load + filter
    d = json.loads(args.input.read_text())
    feats = [f for f in d["features"]
             if args.province.upper() in (f["properties"].get("nama_prov") or "").upper()]
    print(f"Filtered WIUPs: {len(feats)} (province match: {args.province!r})",
          file=sys.stderr)
    if not feats:
        print("No features after filter; abort.", file=sys.stderr)
        return 1

    # Group by tile
    by_tile: dict[str, list[dict]] = defaultdict(list)
    cross_tile_count = 0
    for f in feats:
        poly = shape(f["geometry"])
        tiles = pick_tiles(*[poly.bounds[i] for i in (1, 3, 0, 2)])
        if len(tiles) > 1:
            cross_tile_count += 1
        for t in tiles:
            by_tile[t].append(f)
    print(f"Tiles needed: {sorted(by_tile)}", file=sys.stderr)
    print(f"Cross-tile WIUPs: {cross_tile_count}", file=sys.stderr)
    for t, fs in sorted(by_tile.items()):
        print(f"  {t}: {len(fs)} WIUPs (some may be cross-tile)", file=sys.stderr)

    # Accumulator: aggregate cross-tile results by kode_wiup
    results: dict[str, dict] = {}

    t0 = time.time()
    for tile, tile_feats in sorted(by_tile.items()):
        loss_path = args.rasterdir / f"Hansen_{VERSION}_lossyear_{tile}.tif"
        tcov_path = args.rasterdir / f"Hansen_{VERSION}_treecover2000_{tile}.tif"
        if not loss_path.exists() or not tcov_path.exists():
            print(f"  ❌ Missing files for tile {tile}; skip", file=sys.stderr)
            continue

        print(f"\n=== Tile {tile} ({len(tile_feats)} WIUPs) ===", file=sys.stderr)
        with rasterio.open(loss_path) as ly, rasterio.open(tcov_path) as tc:
            for i, feat in enumerate(tile_feats, 1):
                wiup = feat["properties"].get("kode_wiup")
                if i % 50 == 0:
                    print(f"  ...{i}/{len(tile_feats)} done", file=sys.stderr)
                r = analyze_wiup_in_tile(feat, ly, tc, args.threshold)
                if r is None or "error" in r:
                    continue
                key = wiup
                if key in results:
                    # Aggregate cross-tile
                    prev = results[key]
                    prev["polygon_area_ha"] += r["polygon_area_ha"]
                    prev["forest_area_2000_ha"] += r["forest_area_2000_ha"]
                    prev["loss_per_year_ha"] = [
                        a + b for a, b in zip(
                            prev["loss_per_year_ha"], r["loss_per_year_ha"]
                        )
                    ]
                    prev["n_pixels_polygon"] += r["n_pixels_polygon"]
                    prev["n_pixels_forest"] += r["n_pixels_forest"]
                    prev["tiles"].append(tile)
                else:
                    r["feature"] = feat
                    r["tiles"] = [tile]
                    results[key] = r
    elapsed = time.time() - t0
    print(f"\n✅ Analysis done in {elapsed:.1f}s ({len(results)} WIUPs)",
          file=sys.stderr)

    # Save outputs
    args.outdir.mkdir(parents=True, exist_ok=True)
    prov_slug = args.province.replace(" ", "_").upper()

    # Wide CSV: 1 row per WIUP
    wide_path = args.outdir / f"batch_{prov_slug}_t{args.threshold}_wide.csv"
    with wide_path.open("w", newline="") as f:
        w = csv.writer(f)
        # Penamaan jendela EKSPLISIT (Fase B, 12 Agu 2026; disempurnakan 15 Agu
        # — jendela pembilang masuk nama persen): loss_2001_2025_ha (eks
        # total_loss_ha — loss Hansen mulai 2001; 2000 = baseline HUTAN),
        # loss_2001_2025_pct_hutan2000 (eks loss_pct_hutan2000/loss_pct_of_forest),
        # plus kolom jendela era Minerba (identitas dari deret per-tahun yang
        # sama). CSV arsip pra-rename tetap terbaca lewat rantai fallback
        # step_loss (build_combined_db) — arsip tak ditulis ulang.
        headers = ["kode_wiup", "nama_usaha", "komoditas", "kab", "sk_iup",
                   "luas_sk_ha", "polygon_area_ha", "forest_2000_ha",
                   "loss_2001_2025_ha", "loss_pct_poligon_2001_2025",
                   "loss_2001_2025_pct_hutan2000",
                   "loss_2001_2008_ha", "hutan_2009_ha", "loss_2009_2025_ha",
                   "loss_2009_2025_pct_hutan2009",
                   "tiles"]
        for y in range(2001, 2001 + N_YEARS):
            headers.append(f"loss_{y}_ha")
        w.writerow(headers)
        for kw, r in results.items():
            p = r["feature"]["properties"]
            total = sum(r["loss_per_year_ha"])
            pct_p = 100 * total / r["polygon_area_ha"] if r["polygon_area_ha"] else 0
            pct_f = 100 * total / r["forest_area_2000_ha"] if r["forest_area_2000_ha"] else 0
            l0108 = sum(r["loss_per_year_ha"][:8])           # 2001-2008
            l0925 = total - l0108                            # 2009-2025
            h2009 = r["forest_area_2000_ha"] - l0108
            pct09 = 100 * l0925 / h2009 if h2009 > 0 else None
            row = [
                kw, p.get("nama_usaha"), p.get("komoditas"), p.get("nama_kab"),
                p.get("sk_iup"), p.get("luas_sk"),
                round(r["polygon_area_ha"], 2),
                round(r["forest_area_2000_ha"], 2),
                round(total, 2),
                round(pct_p, 3),
                round(pct_f, 3),
                round(l0108, 2),
                round(h2009, 2),
                round(l0925, 2),
                round(pct09, 3) if pct09 is not None else "",
                "|".join(r["tiles"]),
            ]
            row.extend(round(x, 2) for x in r["loss_per_year_ha"])
            w.writerow(row)
    print(f"Saved WIDE → {wide_path}", file=sys.stderr)

    # Long CSV: 1 row per (WIUP, year)
    long_path = args.outdir / f"batch_{prov_slug}_t{args.threshold}_long.csv"
    with long_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kode_wiup", "nama_usaha", "komoditas", "year", "loss_ha"])
        for kw, r in results.items():
            p = r["feature"]["properties"]
            for i, ha in enumerate(r["loss_per_year_ha"]):
                if ha > 0:
                    w.writerow([kw, p.get("nama_usaha"), p.get("komoditas"),
                                2001 + i, round(ha, 3)])
    print(f"Saved LONG → {long_path}", file=sys.stderr)

    # Metadata
    meta_path = args.outdir / f"batch_{prov_slug}_t{args.threshold}.meta.json"
    meta_path.write_text(json.dumps({
        "province": args.province,
        "threshold_canopy_pct": args.threshold,
        "hansen_version": VERSION,
        "n_wiup_input": len(feats),
        "n_wiup_analyzed": len(results),
        "tiles_used": sorted(by_tile.keys()),
        "elapsed_seconds": round(elapsed, 1),
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2))
    print(f"Saved META → {meta_path}", file=sys.stderr)

    # Quick summary
    total_polygon = sum(r["polygon_area_ha"] for r in results.values())
    total_forest = sum(r["forest_area_2000_ha"] for r in results.values())
    total_loss = sum(sum(r["loss_per_year_ha"]) for r in results.values())
    print(f"\n{'='*55}")
    print(f"  SUMMARY — {args.province} (threshold {args.threshold}%)")
    print(f"{'='*55}")
    print(f"  Total WIUPs        : {len(results):>10,d}")
    print(f"  Sum polygon area   : {total_polygon:>10,.1f} ha")
    print(f"  Sum forest 2000    : {total_forest:>10,.1f} ha")
    print(f"  Sum loss 2001-2025 : {total_loss:>10,.1f} ha")
    print(f"  Loss % of forest   : {100*total_loss/total_forest:>10.2f}%")
    print(f"  Loss % of polygon  : {100*total_loss/total_polygon:>10.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
