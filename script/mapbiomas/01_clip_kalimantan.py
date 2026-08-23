#!/usr/bin/env python3
"""
Alternatif 01_clip_kalimantan.sh untuk yang tidak punya GDAL CLI.
Hanya butuh: pip install rasterio geopandas pandas

Membaca COG MapBiomas langsung dari cloud lewat /vsicurl (HTTP Range),
memotong ke batas Kalimantan, dan menulis dua versi:
  clip_wgs84/      -> untuk overlay / peta
  clip_equalarea/  -> untuk hitung hektar (piksel persis 30 x 30 m)
"""
from pathlib import Path
import csv
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling

BASE = "https://storage.googleapis.com/mapbiomas-downloads/public/indonesia/maps"
MANIFEST = str(Path(__file__).with_name("manifest_c41_national.csv"))
BOUNDARY = "batas/kalimantan_kabupaten.gpkg"
OUT_WGS = Path("clip_wgs84"); OUT_EA = Path("clip_equalarea")
EQAREA = "+proj=cea +lat_ts=0 +lon_0=115 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"

GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_MULTIPLEX="YES",
                VSI_CACHE="TRUE", VSI_CACHE_SIZE="100000000")

PROFILE = dict(driver="GTiff", dtype="uint8", nodata=0, count=1,
               compress="deflate", predictor=2, tiled=True, bigtiff="IF_SAFER")


def main():
    OUT_WGS.mkdir(exist_ok=True); OUT_EA.mkdir(exist_ok=True)
    gdf = gpd.read_file(BOUNDARY).to_crs("EPSG:4326")
    shapes = [g.__geo_interface__ for g in gdf.geometry]

    rows = list(csv.DictReader(open(MANIFEST)))
    with rasterio.Env(**GDAL_ENV):
        for r in rows:
            year, uuid = r["year"], r["uuid"]
            dst_wgs = OUT_WGS / f"kalimantan_lulc_{year}.tif"
            dst_ea = OUT_EA / f"kalimantan_lulc_{year}_cea.tif"
            if dst_ea.exists() and dst_wgs.exists():
                print(f"skip {year}"); continue
            url = f"/vsicurl/{BASE}/{uuid}/{year}_coverage_lclu_4-1-1_{uuid}.tif"
            print(f"=== {year} (export {r['export_vintage']}) ===", flush=True)

            with rasterio.open(url) as src:
                arr, tr = rio_mask(src, shapes, crop=True, filled=True, nodata=0)
                src_crs = src.crs
            arr = arr.astype("uint8")

            # Tulis ke nama sementara lalu rename setelah sukses, supaya proses
            # yang terbunuh di tengah tidak meninggalkan file parsial yang run
            # berikutnya di-skip sebagai "sudah ada".
            p = dict(PROFILE, height=arr.shape[1], width=arr.shape[2],
                     transform=tr, crs=src_crs)
            tmp_wgs = dst_wgs.with_suffix(".tif.tmp")
            with rasterio.open(tmp_wgs, "w", **p) as d:
                d.write(arr[0], 1)
            tmp_wgs.replace(dst_wgs)

            dtr, dw, dh = calculate_default_transform(
                src_crs, EQAREA, arr.shape[2], arr.shape[1],
                *rasterio.transform.array_bounds(arr.shape[1], arr.shape[2], tr),
                resolution=(30, 30))
            out = np.zeros((dh, dw), dtype="uint8")
            reproject(source=arr[0], destination=out,
                      src_transform=tr, src_crs=src_crs,
                      dst_transform=dtr, dst_crs=EQAREA,
                      src_nodata=0, dst_nodata=0,
                      resampling=Resampling.nearest)
            p2 = dict(PROFILE, height=dh, width=dw, transform=dtr, crs=EQAREA)
            tmp_ea = dst_ea.with_suffix(".tif.tmp")
            with rasterio.open(tmp_ea, "w", **p2) as d:
                d.write(out, 1)
            tmp_ea.replace(dst_ea)
            print(f"    {dw} x {dh} px @30m -> {dst_ea}")


if __name__ == "__main__":
    main()
