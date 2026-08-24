#!/usr/bin/env python3
"""
Bikin layer "tahun kejadian" dari MapBiomas C4.1 — analog Hansen `lossyear`,
supaya bisa langsung masuk pipeline tiling XYZ yang sudah ada di Tanah Hilang.

Output (semua uint8, grid identik dengan input, 0 = tidak pernah / di luar AOI):

  mining_first.tif        tahun PERTAMA piksel jadi Lubang Tambang (kode 30)
  mining_last.tif         tahun TERAKHIR piksel masih Lubang Tambang
  forestloss_first.tif    tahun PERTAMA piksel berubah hutan -> bukan hutan
  mining_after_forest.tif tahun piksel jadi tambang, HANYA jika sebelumnya hutan

Encoding tahun: nilai = tahun - 1987  (1 = 1988 ... 37 = 2024)
CATATAN: base 1987 sengaja BEDA dari tile Descals (tahun - 1988) karena seri ini
mulai 1988 dan 0 sudah dipakai sebagai "tidak pernah" — decoder klien harus
memakai base masing-masing, jangan campur.

Untuk drop-in ke ramp Hansen yang sudah ada (lossyear = tahun - 2000), pakai
--hansen-encoding: nilai = tahun - 2000, dan semua kejadian <= 2000 jadi 0 —
termasuk layer status (mining_*): piksel yang SUDAH tambang sejak <= 2000 ikut
jadi 0, bukan direkam ulang sebagai 2001. Pelacakan internal selalu memakai
encoding penuh; konversi Hansen dilakukan saat menulis output.

Dua semantik yang melekat pada datanya (bukan bug, tulis di limitasi):
- nilai di tahun pertama seri berarti "sudah begitu saat pertama teramati",
  bukan "kejadiannya tahun itu";
- kelas 27 (Tak Teramati/awan) diperlakukan sebagai tak-ada-observasi: tidak
  memicu "hutan hilang", dan status hutan tahun teramati terakhir dibawa terus
  sampai observasi berikutnya.

Jalankan setelah 01_clip_kalimantan.*:
    python3 03_firstyear_layers.py --src clip_wgs84
"""
import argparse
import re
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

MINING = 30
FOREST = (3, 5, 76)
UNOBSERVED = 27  # awan/tak teramati — bukan observasi, bukan "bukan hutan"
BASE_YEAR = 1987


def ke_hansen(full: np.ndarray) -> np.ndarray:
    """Konversi encoding penuh (tahun-1987) ke encoding Hansen (tahun-2000).

    Kejadian/status yang tercatat <= 2000 tidak bisa diwakili ramp Hansen,
    jadi di-nol-kan — untuk KEEMPAT layer, termasuk yang berbasis status.
    """
    geser = 2000 - BASE_YEAR  # 13
    out = np.zeros_like(full)
    m = full > geser  # tahun >= 2001
    out[m] = full[m] - geser
    return out


def open_years(src_dir: Path, pattern: str):
    files = sorted(src_dir.glob(pattern))
    if not files:
        raise SystemExit(f"Tidak ada raster di {src_dir}/{pattern}")
    years = []
    for f in files:
        m = re.search(r"(19|20)\d{2}", f.name)
        if not m:
            raise SystemExit(f"Tidak bisa membaca tahun dari nama file: {f.name}")
        years.append((int(m.group(0)), f))
    years.sort()
    return years


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="clip_wgs84")
    ap.add_argument("--pattern", default="kalimantan_lulc_*.tif")
    ap.add_argument("--out", default="layers_firstyear")
    ap.add_argument("--chunk", type=int, default=512, help="baris per potongan")
    ap.add_argument("--hansen-encoding", action="store_true",
                    help="nilai = tahun-2000 (kompatibel ramp Hansen lossyear)")
    a = ap.parse_args()

    src_dir, out_dir = Path(a.src), Path(a.out)
    out_dir.mkdir(exist_ok=True)
    years = open_years(src_dir, a.pattern)
    print(f"{len(years)} tahun: {years[0][0]}-{years[-1][0]}")

    dss = [(y, rasterio.open(f)) for y, f in years]
    ref = dss[0][1]
    for y, ds in dss[1:]:
        if (ds.shape != ref.shape) or (ds.transform != ref.transform):
            raise SystemExit(
                f"Grid tahun {y} tidak sejajar dengan {years[0][0]}. "
                "Pastikan semua tahun dipotong dengan cutline & grid yang sama.")

    prof = dict(driver="GTiff", dtype="uint8", nodata=0, count=1,
                height=ref.height, width=ref.width, crs=ref.crs,
                transform=ref.transform, compress="deflate", predictor=2,
                tiled=True, bigtiff="IF_SAFER")

    names = ["mining_first", "mining_last", "forestloss_first", "mining_after_forest"]
    outs = {n: rasterio.open(out_dir / f"{n}.tif", "w", **prof) for n in names}

    for row0 in range(0, ref.height, a.chunk):
        h = min(a.chunk, ref.height - row0)
        win = Window(0, row0, ref.width, h)
        shape = (h, ref.width)

        mining_first = np.zeros(shape, dtype=np.uint8)
        mining_last = np.zeros(shape, dtype=np.uint8)
        loss_first = np.zeros(shape, dtype=np.uint8)
        mine_after_forest = np.zeros(shape, dtype=np.uint8)

        prev_forest = None
        ever_forest = np.zeros(shape, dtype=bool)

        for y, ds in dss:
            arr = ds.read(1, window=win)
            # Pelacakan SELALU pakai encoding penuh (tahun - 1987); konversi ke
            # encoding Hansen dilakukan sekali saat menulis output, supaya status
            # pra-2001 hilang (0), bukan menumpuk palsu sebagai "kejadian 2001".
            code = y - BASE_YEAR
            val = np.uint8(code) if 0 < code < 256 else np.uint8(0)

            observed = (arr != 0) & (arr != UNOBSERVED)
            is_mining = arr == MINING
            is_forest = np.isin(arr, FOREST)

            if val:
                m = is_mining & (mining_first == 0)
                mining_first[m] = val
                mining_last[is_mining] = val
                if prev_forest is not None:
                    # Hanya observasi sungguhan yang boleh memicu "hutan hilang";
                    # tahun berawan (27) bukan bukti hilang.
                    lost = prev_forest & ~is_forest & observed & (loss_first == 0)
                    loss_first[lost] = val
                m2 = is_mining & ever_forest & (mine_after_forest == 0)
                mine_after_forest[m2] = val

            ever_forest |= is_forest
            if prev_forest is None:
                prev_forest = is_forest.copy()
            else:
                # Status hutan tahun teramati terakhir dibawa terus melewati
                # tahun-tahun berawan.
                prev_forest = np.where(observed, is_forest, prev_forest)

        for n, data in zip(names, [mining_first, mining_last, loss_first, mine_after_forest]):
            if a.hansen_encoding:
                data = ke_hansen(data)
            outs[n].write(data, 1, window=win)

    for o in outs.values():
        o.close()
    for _, ds in dss:
        ds.close()

    enc = "tahun-2000 (Hansen-like)" if a.hansen_encoding else f"tahun-{BASE_YEAR}"
    print(f"Selesai. Encoding: {enc}. Output di {out_dir}/")
    print("Tile ke XYZ PNG dengan pipeline yang sama seperti layer Descals.")


if __name__ == "__main__":
    main()
