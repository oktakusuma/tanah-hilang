#!/usr/bin/env python3
"""Komposit Sentinel-2 bebas awan per tahun (2016-2018) — menambal mutu citra
dasar pada tiga tahun yang selama ini paling jelek di peta.

MASALAH YANG DIPECAHKAN. Untuk 2016-2025 peta memakai mosaik EOX s2cloudless.
Mosaik 2019 ke atas bagus, tapi 2016-2018 terasa jelek (keluhan igoen 24 Agu) —
sebabnya jumlah citra: Sentinel-2B baru mengudara Maret 2017, jadi mosaik
2016-2017 disusun dari satu satelit saja dan menyisakan kabut. Lebih parah,
layer `s2cloudless-2017` KOSONG di Kalimantan sehingga 2017 selama ini
menumpang mosaik 2016 — dua tahun berbeda menampilkan gambar yang sama.

KENAPA BISA LEBIH BAIK DARI EOX. Dua hal yang tak dipakai EOX:
  1. Cloud Score+ (GOOGLE/CLOUD_SCORE_PLUS) — penutup awan generasi baru,
     jauh lebih peka pada awan tipis daripada QA60/s2cloudless klasik.
  2. Jendela +-1 tahun, sehingga 2017 memakai 2016-2018 (~136 scene) alih-alih
     31 scene setahun.

CATATAN TINGKAT DATA. Di Kalimantan, Sentinel-2 tingkat permukaan (L2A) praktis
TIDAK ADA untuk tahun-tahun ini — diverifikasi 24 Agu di petak Sangatta:
L2A = 0 scene (2016), 0 (2017), 5 (2018). Jadi terpaksa memakai L1C (puncak
atmosfer). Konsekuensinya citra berkabut keputihan, dan itu ditangani dengan
TITIK HITAM 0,05 pada peregangan tampilan (lihat VIS).

    !! Ini PEREGANGAN TAMPILAN, bukan koreksi atmosfer sungguhan. Cukup untuk
       citra latar; TIDAK boleh dipakai sebagai masukan hitungan apa pun.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import ee

from komposit_landsat import HANSEN_GFC, PERSENTIL, masuk

# Titik hitam 0,05 membuang lantai hamburan atmosfer yang selalu ada di L1C.
# Diuji 24 Agu di petak Sangatta tahun 2017 (kecerahan rata-rata daratan):
#     titik hitam 0,00 -> 97,5  pucat keputihan, hutan nyaris tak berwarna
#     titik hitam 0,05 -> 48,1  kabut hilang, hijau wajar, pit tajam  <- dipakai
#     titik hitam 0,08 -> 18,0  terlalu gelap, detail bayangan tenggelam
VIS = {"min": 0.05, "max": 0.30, "gamma": 1.1}

# Ambang Cloud Score+ (band cs_cdf): >= 0,60 dianggap cerah. Nilai anjuran
# dokumentasi dataset untuk komposit bebas awan.
CS_AMBANG = 0.60

# Tangga ambang awan metadata scene. Sama perannya dengan tangga di
# komposit_landsat: jendela tiga tahun Sentinel-2 jauh lebih banyak scene
# daripada Landsat, dan tanpa penyaringan GEE menolak "User memory limit
# exceeded" (terbukti 24 Agu: 2017 gagal tanpa saringan, lolos di awan<60%).
TANGGA_AWAN = (60, 40, 25)

TAHUN_MIN, TAHUN_MAKS = 2016, 2018


def komposit(tahun: int, wilayah: ee.Geometry, lebar: int = 1,
             awan_maks: int = 60) -> ee.Image:
    """Komposit persentil Sentinel-2 utk `tahun`, jendela [tahun-lebar, +lebar]."""
    d1 = ee.Date.fromYMD(tahun - lebar, 1, 1)
    d2 = ee.Date.fromYMD(tahun + lebar, 12, 31)
    csp = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

    def bersih(img):
        return (img.select(["B4", "B3", "B2"], ["R", "G", "B"]).multiply(1e-4)
                .updateMask(img.select("cs_cdf").gte(CS_AMBANG)))

    col = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
           .filterDate(d1, d2)
           .filterBounds(wilayah)
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", awan_maks))
           .linkCollection(csp, ["cs_cdf"])
           .map(bersih))

    citra = (col.reduce(ee.Reducer.percentile([PERSENTIL]))
             .rename(["R", "G", "B"]))
    # Penutup daratan yang sama dengan komposit Landsat — lihat alasannya di
    # komposit_landsat.py (band datamask Hansen v1.13, konsisten dgn analisis).
    darat = ee.Image(HANSEN_GFC).select("datamask").unmask(0).eq(1)
    return citra.updateMask(darat)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tahun", type=int, required=True)
    ap.add_argument("--bbox", default="117.30,0.25,117.80,0.75")
    ap.add_argument("--skala", type=int, default=10)
    ap.add_argument("--lebar", type=int, default=1)
    ap.add_argument("--keluar", default=None)
    a = ap.parse_args()

    masuk()
    bbox = [float(v) for v in a.bbox.split(",")]
    wil = ee.Geometry.Rectangle(bbox, None, False)
    keluar = Path(a.keluar or f"data/external/gee/s2_{a.tahun}_{a.skala}m.tif")
    keluar.parent.mkdir(parents=True, exist_ok=True)

    import urllib.error
    import urllib.request
    for awan in TANGGA_AWAN:
        img = komposit(a.tahun, wil, a.lebar, awan).visualize(**VIS).clip(wil)
        url = img.getDownloadURL({"region": wil, "scale": a.skala,
                                  "crs": "EPSG:4326", "format": "GEO_TIFF",
                                  "filePerBand": False})
        try:
            urllib.request.urlretrieve(url, keluar)
        except urllib.error.HTTPError as e:
            pesan = e.read().decode(errors="replace")
            if "memory" not in pesan or awan == TANGGA_AWAN[-1]:
                raise SystemExit(f"GEE menolak (awan<{awan}%): {pesan[:300]}")
            print(f"  batas memori pada awan<{awan}% — coba lebih ketat")
            continue
        print(f"selesai: {keluar} ({keluar.stat().st_size / 1e6:.1f} MB, awan<{awan}%)")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
