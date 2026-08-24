#!/usr/bin/env python3
"""
Bangun panel kabupaten x tahun x kelas (hektar) dari raster MapBiomas Indonesia
Koleksi 4.1 yang sudah dipotong ke proyeksi sama-luas oleh 01_clip_kalimantan.sh.

Output: panel_kalimantan_lulc.csv  (long format)
        panel_kalimantan_wide.csv  (wide, satu kolom per kelas + variabel turunan)

Prasyarat:  pip install rasterio geopandas pandas numpy
Jalankan :  python3 02_panel_zonal.py
"""
from pathlib import Path
import re
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize

# ----------------- KONFIGURASI -----------------
RASTER_DIR = Path("clip_equalarea")           # output (b) dari skrip shell
PATTERN    = "kalimantan_lulc_*_cea.tif"
BOUNDARY   = "batas/kalimantan_kabupaten.gpkg"
ID_FIELD   = "kode_bps"                       # kolom kode kabupaten di file batasmu
OUT_LONG   = "panel_kalimantan_lulc.csv"
OUT_WIDE   = "panel_kalimantan_wide.csv"
CHUNK_ROWS = 512                              # turunkan kalau MemoryError, naikkan kalau RAM lega
# -----------------------------------------------

# Legend Koleksi 4.1 (diverifikasi dari
# landy.mapbiomas.id/assets/legendcode/Integration and Layers MB Indonesia - Col 4 - EN .pdf)
LEGEND = {
    3:  ("Forest Formation",            "Forest"),
    5:  ("Mangrove",                    "Forest"),
    76: ("Peat Swamp Forest",           "Forest"),
    10: ("Non-Forest Natural Vegetation","Non-Forest Natural Formation"),
    13: ("Non-Forest Natural Vegetation","Non-Forest Natural Formation"),
    40: ("Rice Paddy",                  "Agriculture"),
    35: ("Oil Palm",                    "Agriculture"),
    9:  ("Pulpwood Plantation",         "Agriculture"),
    21: ("Other Agriculture",           "Agriculture"),
    30: ("Mining Pit",                  "Non-Vegetated Area"),
    24: ("Urban Area",                  "Non-Vegetated Area"),
    25: ("Other Non-Vegetation",        "Non-Vegetated Area"),
    31: ("Aquaculture",                 "Water Body"),
    33: ("River, Lake, Ocean",          "Water Body"),
    27: ("Not Observed",                "Not Observed"),
}
FOREST_CODES = [3, 5, 76]
MINING_CODE  = 30

NCLASS = 256  # kode kelas muat di uint8


def load_zones(path, id_field, crs):
    gdf = gpd.read_file(path).to_crs(crs)
    if id_field not in gdf.columns:
        raise SystemExit(f"Kolom '{id_field}' tidak ada. Kolom tersedia: {list(gdf.columns)}")
    gdf = gdf.reset_index(drop=True)
    gdf["_zid"] = np.arange(1, len(gdf) + 1, dtype=np.int32)  # 0 dicadangkan untuk 'luar'
    return gdf


def tabulate(raster_path, gdf):
    """Kembalikan dict[(zid, class)] = jumlah piksel, diproses per potongan baris."""
    with rasterio.open(raster_path) as src:
        shapes = list(zip(gdf.geometry, gdf["_zid"]))
        counts = {}
        for row0 in range(0, src.height, CHUNK_ROWS):
            h = min(CHUNK_ROWS, src.height - row0)
            win = rasterio.windows.Window(0, row0, src.width, h)
            arr = src.read(1, window=win)
            if not arr.any():
                continue
            zones = rasterize(
                shapes,
                out_shape=(h, src.width),
                transform=src.window_transform(win),
                fill=0,
                dtype="int32",
                all_touched=False,          # piksel dihitung sekali; hindari double count
            )
            m = (zones > 0) & (arr > 0)
            if not m.any():
                continue
            key = zones[m].astype(np.int64) * NCLASS + arr[m].astype(np.int64)
            bc = np.bincount(key)
            nz = np.nonzero(bc)[0]
            for k in nz:
                counts[(int(k // NCLASS), int(k % NCLASS))] = counts.get(
                    (int(k // NCLASS), int(k % NCLASS)), 0) + int(bc[k])
        px_ha = abs(src.transform.a * src.transform.e) / 10000.0
    return counts, px_ha


def main():
    rasters = sorted(RASTER_DIR.glob(PATTERN))
    if not rasters:
        raise SystemExit(f"Tidak ada raster di {RASTER_DIR}/{PATTERN}. Jalankan 01_clip_kalimantan.sh dulu.")

    with rasterio.open(rasters[0]) as src:
        crs = src.crs
    gdf = load_zones(BOUNDARY, ID_FIELD, crs)
    zid2code = dict(zip(gdf["_zid"], gdf[ID_FIELD]))

    records = []
    for rp in rasters:
        year = int(re.search(r"(\d{4})", rp.name).group(1))
        counts, px_ha = tabulate(rp, gdf)
        print(f"{year}: {len(counts)} kombinasi zona-kelas, {px_ha:.4f} ha/piksel")
        for (zid, cls), n in counts.items():
            name, level1 = LEGEND.get(cls, (f"UNKNOWN_{cls}", "UNKNOWN"))
            records.append({
                "kode": zid2code[zid], "year": year, "class_code": cls,
                "class_name": name, "level1": level1,
                "pixels": n, "ha": n * px_ha,
            })

    long_df = pd.DataFrame.from_records(records).sort_values(["kode", "year", "class_code"])
    long_df.to_csv(OUT_LONG, index=False)

    wide = long_df.pivot_table(index=["kode", "year"], columns="class_code",
                               values="ha", aggfunc="sum", fill_value=0.0)
    wide.columns = [f"ha_{c}" for c in wide.columns]
    wide = wide.reset_index()
    wide["ha_forest"] = sum(wide.get(f"ha_{c}", 0) for c in FOREST_CODES)
    wide["ha_mining"] = wide.get(f"ha_{MINING_CODE}", 0)
    tot = long_df.groupby(["kode", "year"], as_index=False)["ha"].sum().rename(columns={"ha": "ha_total"})
    wide = wide.merge(tot, on=["kode", "year"], how="left")
    # Penyebut share = DARATAN TERAMATI: kelas 27 (awan/tak teramati — luasnya
    # berfluktuasi antar tahun tanpa perubahan lahan riil) dan badan air (31, 33)
    # dikeluarkan, supaya share_forest tidak bergerak hanya karena tutupan awan.
    # ha_total (semua kelas > 0) tetap disimpan sebagai referensi.
    wide["ha_darat_teramati"] = (wide["ha_total"] - wide.get("ha_27", 0)
                                 - wide.get("ha_31", 0) - wide.get("ha_33", 0))
    penyebut = wide["ha_darat_teramati"].replace(0, np.nan)
    wide["share_forest"] = wide["ha_forest"] / penyebut
    wide["share_mining"] = wide["ha_mining"] / penyebut
    wide = wide.sort_values(["kode", "year"])
    wide["d_ha_forest"] = wide.groupby("kode")["ha_forest"].diff()
    wide["d_ha_mining"] = wide.groupby("kode")["ha_mining"].diff()
    wide.to_csv(OUT_WIDE, index=False)

    print(f"\nTulis {OUT_LONG} ({len(long_df)} baris) dan {OUT_WIDE} ({len(wide)} baris).")


if __name__ == "__main__":
    main()
