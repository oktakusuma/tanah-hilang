#!/usr/bin/env python3
"""Komposit Landsat bebas awan per tahun (2009-2015) — menambal lubang citra
dasar peta yang kini masih memakai fallback Landsat WELD tahun 2000.

KENAPA JENDELA ±1 TAHUN (bukan satu tahun):
Diukur langsung lewat GEE 24 Agu di 5 lokasi tambang Kalimantan — jumlah
observasi bersih (setelah CFMASK) pada 5% piksel terburuk:
    2009 p5=3  2010 p5=1  2011 p5=0  2012 p5=1  2013 p5=3  2014 p5=2  2015 p5=3
Komposit SATU tahun 2011 di Sangatta menyisakan 8% piksel BOLONG; jendela
2010-2012 menutupnya jadi 100%. Karena itu SEMUA tahun memakai jendela ±1
tahun — seragam supaya mudah dijelaskan, dan labelnya jujur di peta
("2011 — komposit 2010-2012").

Sumber per tahun (band RGB dinormalkan ke R/G/B sebelum digabung):
    LANDSAT/LT05/C02/T1_L2  s.d. 2012-05 (tanpa band pankromatik)
    LANDSAT/LE07/C02/T1_L2  1999-2024 (SLC-off sejak 2003; celahnya bergeser
                            antar tanggal sehingga tertutup oleh komposit)
    LANDSAT/LC08/C02/T1_L2  sejak 2013-03
Faktor skala Collection-2 L2: DN * 0,0000275 - 0,2 (reflektans permukaan).

Autentikasi: service account, kunci di .secrets/gee-sa.json (gitignored).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import ee

KUNCI = Path(__file__).resolve().parents[2] / ".secrets" / "gee-sa.json"
PROJECT = "sound-country-297014"
VIS = {"min": 0.0, "max": 0.26, "gamma": 1.1}

# Persentil peredam (bukan median) — Kalimantan berawan sepanjang tahun dan
# CFMASK meloloskan awan tipis/kabut, yang lolos ke median sebagai bercak putih.
# Diuji langsung 24 Agu di petak Sangatta tahun 2013:
#     median  -> bercak awan putih jelas, rerata kecerahan 40,0
#     p25     -> bersih tapi gelap (32,4) & detail pit ikut redup
#     p35     -> bersih, terang (35,2), detail pit tetap tajam  <- dipakai
# Persentil rendah menang karena awan selalu LEBIH TERANG dari permukaan,
# jadi mengambil nilai di bawah median otomatis membuang piksel berawan.
PERSENTIL = 35

# Penutup daratan. Di laut komposit Landsat TETAP punya data — air memantulkan
# sangat sedikit, jadi hasilnya nyaris HITAM, bukan kosong. Tanpa penutup,
# lapisan kita membekap laut dengan kotak hitam beserta tepi petak scene yang
# miring (terlihat jelas saat uji 24 Agu).
#
# Dipakai band `datamask` Hansen GFC v1.13 — dataset yang SAMA dengan sumber
# angka kehilangan hutan tesis ini, jadi batas darat-lautnya konsisten dengan
# seluruh analisis. Nilainya: 1 = daratan, 2 = perairan tetap, 0 = tak ada data.
# Diverifikasi 24 Agu: Laut Sulawesi = 0, Selat Makassar & Laut Jawa = 2,
# Sangatta & pedalaman & tepi Mahakam = 1.
#
# PERCOBAAN YANG GAGAL (jangan diulang): JRC Global Surface Water `occurrence`
# >= 80%. Di titik uji dekat pantai memang membaca 99-100%, tapi cakupannya
# TIDAK sampai laut lepas — hasilnya tile Selat Makassar cuma 22% transparan
# dan masih menyisakan 12,6% piksel hitam pekat.
HANSEN_GFC = "UMD/hansen/global_forest_change_2025_v1_13"


def masuk() -> None:
    ee.Initialize(ee.ServiceAccountCredentials(None, str(KUNCI)), project=PROJECT)


def _siap(col: ee.ImageCollection, r: str, g: str, b: str) -> ee.ImageCollection:
    """Samakan nama band ke R/G/B, buang awan/bayangan, skalakan ke reflektans."""
    def per_img(img):
        qa = img.select("QA_PIXEL")
        ok = (qa.bitwiseAnd(1 << 1).eq(0)        # awan yang dilebarkan
              .And(qa.bitwiseAnd(1 << 3).eq(0))  # awan
              .And(qa.bitwiseAnd(1 << 4).eq(0)))  # bayangan awan
        return (img.select([r, g, b], ["R", "G", "B"])
                .multiply(0.0000275).add(-0.2)
                .updateMask(ok))
    return col.map(per_img)


# Tangga ambang tutupan awan per scene. Mulai longgar (80%) supaya sebanyak
# mungkin observasi ikut menambal lubang; turun HANYA kalau GEE menolak dengan
# "User memory limit exceeded" — jumlah scene yang masuk reducer median itulah
# yang menentukan pemakaian memori. Terbukti 24 Agu: 2014/2015 (tiga tahun
# penuh Landsat 8) butuh 60%, tahun lain cukup 80%.
TANGGA_AWAN = (80, 60, 40)


def komposit(tahun: int, wilayah: ee.Geometry, lebar: int = 1,
             awan_maks: int = 80) -> ee.Image:
    """Median komposit utk `tahun`, memakai jendela [tahun-lebar, tahun+lebar].

    `wilayah` WAJIB: tanpa filterBounds, median dihitung atas seluruh scene
    global di rentang tanggal itu dan GEE menolak dgn "User memory limit
    exceeded" (terbukti 24 Agu — 2011 lolos, 2009/2010/2012-2015 gagal).
    """
    d1 = ee.Date.fromYMD(tahun - lebar, 1, 1)
    d2 = ee.Date.fromYMD(tahun + lebar, 12, 31)

    def sumber(aset: str, r: str, g: str, b: str) -> ee.ImageCollection:
        col = (ee.ImageCollection(aset)
               .filterDate(d1, d2)
               .filterBounds(wilayah)
               .filter(ee.Filter.lt("CLOUD_COVER", awan_maks)))
        return _siap(col, r, g, b)

    # Pilih sensor sesuai ketersediaan; makin sedikit scene masuk reducer,
    # makin kecil peluang kena batas memori GEE.
    #   L5 berakhir 2012-05, L8 mulai 2013-03.
    #   L7 (SLC-off, bergaris sejak 2003) dibuang HANYA kalau seluruh jendela
    #   sudah berada di era L8. Syarat lama ("buang begitu L8 muncul") terbukti
    #   salah 24 Agu: jendela 2012 (2011-2013) jadi terlalu tipis — L5 berhenti
    #   Mei 2012, L8 baru Maret 2013 — sehingga persentil menyisakan bercak
    #   awan dan 1,23% lubang. Dengan L7 ikut, lubangnya nol.
    pakai_l5 = tahun - lebar <= 2012
    pakai_l7 = tahun - lebar < 2013
    pakai_l8 = tahun + lebar >= 2013
    kol = []
    if pakai_l5:
        kol.append(sumber("LANDSAT/LT05/C02/T1_L2", "SR_B3", "SR_B2", "SR_B1"))
    if pakai_l7:
        kol.append(sumber("LANDSAT/LE07/C02/T1_L2", "SR_B3", "SR_B2", "SR_B1"))
    if pakai_l8:
        kol.append(sumber("LANDSAT/LC08/C02/T1_L2", "SR_B4", "SR_B3", "SR_B2"))
    gabung = kol[0]
    for c in kol[1:]:
        gabung = gabung.merge(c)
    citra = (gabung.reduce(ee.Reducer.percentile([PERSENTIL]))
             .rename(["R", "G", "B"]))
    # unmask(0): di luar cakupan Hansen band-nya kosong; tanpa unmask, piksel
    # itu jadi "tak diketahui" dan ikut lolos, bukan tertutup.
    darat = ee.Image(HANSEN_GFC).select("datamask").unmask(0).eq(1)
    return citra.updateMask(darat)


def unduh(tahun: int, bbox: list[float], keluar: Path, skala: int = 30,
          lebar: int = 1) -> tuple[Path, int]:
    """Tarik komposit RGB 8-bit sebagai GeoTIFF langsung ke server (tanpa Drive/GCS).

    Mengembalikan (berkas, ambang_awan_yang_dipakai). Tulis ke .tmp lalu
    rename supaya unduhan yang putus tak pernah tampak sebagai hasil jadi.
    """
    import urllib.error
    import urllib.request

    wilayah = ee.Geometry.Rectangle(bbox, None, False)
    keluar.parent.mkdir(parents=True, exist_ok=True)
    tmp = keluar.with_suffix(keluar.suffix + ".tmp")
    for i, awan in enumerate(TANGGA_AWAN):
        img = komposit(tahun, wilayah, lebar, awan).visualize(**VIS).clip(wilayah)
        url = img.getDownloadURL({
            "region": wilayah, "scale": skala, "crs": "EPSG:4326",
            "format": "GEO_TIFF", "filePerBand": False,
        })
        try:
            urllib.request.urlretrieve(url, tmp)
        except urllib.error.HTTPError as e:
            pesan = e.read().decode(errors="replace")
            # Hanya batas memori yang boleh diturunkan ambangnya; error lain fatal.
            if "memory limit" not in pesan or i == len(TANGGA_AWAN) - 1:
                tmp.unlink(missing_ok=True)
                raise SystemExit(f"GEE menolak (awan<{awan}%): {pesan[:300]}")
            print(f"  batas memori pada awan<{awan}% — coba lebih ketat")
            continue
        tmp.rename(keluar)
        return keluar, awan
    raise SystemExit("tak tercapai")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tahun", type=int, required=True)
    ap.add_argument("--bbox", default="117.30,0.25,117.80,0.75", help="lon_min,lat_min,lon_max,lat_max")
    ap.add_argument("--skala", type=int, default=30)
    ap.add_argument("--lebar", type=int, default=1, help="setengah-lebar jendela tahun")
    ap.add_argument("--keluar", default=None)
    a = ap.parse_args()

    masuk()
    bbox = [float(v) for v in a.bbox.split(",")]
    keluar = Path(a.keluar or f"data/external/gee/landsat_{a.tahun}_{a.skala}m.tif")
    print(f"tahun {a.tahun} (jendela {a.tahun - a.lebar}-{a.tahun + a.lebar}), "
          f"bbox {bbox}, skala {a.skala} m -> {keluar}")
    p, awan = unduh(a.tahun, bbox, keluar, a.skala, a.lebar)
    print(f"selesai: {p} ({p.stat().st_size / 1e6:.1f} MB, awan<{awan}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
