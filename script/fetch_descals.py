#!/usr/bin/env python3
"""Unduh + saring peta TAHUN TANAM sawit Descals dkk. (2024) ke ubin Kalimantan.

KENAPA: tesis mengukur kehilangan tutupan pohon di dalam batas konsesi tambang. Sebagian
kehilangan itu mungkin sebenarnya konversi ke sawit. Poligon konsesi sawit GFW tak layak
pakai (hanya 10% barisnya punya tahun, dan tahunnya berhenti 2007), jadi penggantinya
adalah peta tahun-tanam Descals: peristiwa FISIK (sawit benar-benar berdiri di piksel itu),
bukan peristiwa hukum (izin terbit).

SUMBER
    Descals, A., Gaveau, D.L.A., Wich, S., Szantoi, Z., dan Meijaard, E. (2024).
    "Global mapping of oil palm planting year from 1990 to 2021."
    Earth System Science Data 16:5111-5129. doi:10.5194/essd-16-5111-2024
    Data: Zenodo doi:10.5281/zenodo.13379129 (v1.2)
    Lisensi: CC-BY-4.0 — atribusi saja, TANPA klausul ShareAlike. (Berbeda dari Maus dkk.
    yang CC-BY-SA-4.0 dan menular ke data turunan.)

KETIDAKPASTIAN YANG WAJIB DIBAWA SAAT MEMAKAI (paper Tabel akurasi):
    RMSE tahun tanam keseluruhan   2,65 tahun  (R2=0,86; galat rata-rata -0,24 th)
    RMSE perkebunan INDUSTRI       2,02 tahun
    RMSE perkebunan RAKYAT         4,89 tahun  <- praktis tak berguna utk atribusi temporal
    Akurasi sebaran industri       producer's 91,0 +/- 2,5% · user's 91,8 +/- 1,2%
Konsekuensinya: pencocokan "loss tahun T <-> tanam tahun T" TERLALU KETAT. Pakai jendela
+/-3 tahun, dan perlakukan perkebunan rakyat sebagai lapisan ruang saja.

PERINGATAN GRID — JANGAN DILEWATI:
    Hansen lossyear : 0,00025 derajat/piksel            (~27,8 m)
    Descals YoP     : 0,00026949458523585647 derajat    (~30 m)
CRS keduanya EPSG:4326 sehingga tak perlu reproyeksi, TAPI pikselnya TIDAK berimpit.
Membaca jendela Descals memakai transform Hansen tanpa penyelarasan akan MENGGESER data.
Setiap pembacaan wajib di-resample ke grid Hansen dengan NEAREST-NEIGHBOUR — nilainya
tahun tanam (kategorik), bukan besaran kontinu yang boleh dirata-rata.

ENCODING NILAI: 0 = bukan sawit; 1989-2022 = tahun tanam langsung (bukan offset).
Rentang efektif yang dinyatakan paper 1990-2021; ekor 2022 sangat tipis.

INPUT   : diunduh dari Zenodo (146,5 MB), checksum MD5 diverifikasi
OUTPUT  : data/external/descals/tiles/*.tif   (95 ubin yang menyentuh Kalimantan, ~45 MB)
          data/external/descals/tile_index.json  (nama berkas + bounds tiap ubin)

Pemakaian:
    python scripts/fetch_descals.py                 # unduh + ekstrak + saring
    python scripts/fetch_descals.py --tetap-arsip   # jangan hapus zip setelah ekstrak
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import rasterio

ZENODO_REKAM = "13379129"
BERKAS = "GlobalOilPalm_OP-YoP.zip"
UNDUH_URL = f"https://zenodo.org/api/records/{ZENODO_REKAM}/files/{BERKAS}/content"
MD5_HARAP = "7c7404d8ffd8d290c36c191c118b5ad1"
OUTDIR = Path("data/external/descals")

# Kotak Kalimantan (sedikit dilebihkan agar ubin tepi ikut terambil).
KAL_BBOX = (108.5, -4.5, 119.5, 4.6)   # lon_min, lat_min, lon_max, lat_max


def md5_berkas(path: Path, blok: int = 1 << 20) -> str:
    """MD5 berkas, dibaca per blok supaya tak memuat 146 MB ke memori."""
    h = hashlib.md5()
    with path.open("rb") as f:
        while chunk := f.read(blok):
            h.update(chunk)
    return h.hexdigest()


def unduh(url: str, tujuan: Path, opener=urllib.request.urlopen) -> None:
    with opener(url, timeout=900) as r, tujuan.open("wb") as f:
        shutil.copyfileobj(r, f)


def menyentuh(bounds, bbox=KAL_BBOX) -> bool:
    """True bila bounds raster beririsan dgn kotak Kalimantan."""
    lon_min, lat_min, lon_max, lat_max = bbox
    return (bounds.left < lon_max and bounds.right > lon_min
            and bounds.bottom < lat_max and bounds.top > lat_min)


def saring_ubin(tiledir: Path, bbox=KAL_BBOX) -> list[dict]:
    """Buang ubin di luar kotak; kembalikan indeks ubin yang disimpan."""
    idx: list[dict] = []
    for p in sorted(tiledir.glob("*.tif")):
        with rasterio.open(p) as s:
            b = s.bounds
            simpan = menyentuh(b, bbox)
        if simpan:
            idx.append({"file": p.name, "left": b.left, "bottom": b.bottom,
                        "right": b.right, "top": b.top})
        else:
            p.unlink()
    return idx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", type=Path, default=OUTDIR)
    ap.add_argument("--tetap-arsip", action="store_true",
                    help="jangan hapus zip setelah diekstrak")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    zippath = args.outdir / BERKAS
    tiledir = args.outdir / "tiles"

    if zippath.exists() and md5_berkas(zippath) == MD5_HARAP:
        print(f"  arsip sudah ada & checksum cocok — lewati unduhan", file=sys.stderr)
    else:
        print(f"  mengunduh {BERKAS} (146,5 MB)…", file=sys.stderr)
        unduh(UNDUH_URL, zippath)
        got = md5_berkas(zippath)
        if got != MD5_HARAP:
            print(f"  GAGAL: checksum tak cocok\n    harap {MD5_HARAP}\n    dapat {got}",
                  file=sys.stderr)
            return 1
        print(f"  checksum cocok: {got}", file=sys.stderr)

    tiledir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zippath) as z:
        n = len(z.namelist())
        print(f"  mengekstrak {n} ubin…", file=sys.stderr)
        z.extractall(tiledir)

    idx = saring_ubin(tiledir)
    print(f"  disimpan {len(idx)} ubin yang menyentuh Kalimantan "
          f"(dari {n}); sisanya dihapus", file=sys.stderr)

    (args.outdir / "tile_index.json").write_text(json.dumps(idx, indent=1))
    print(f"  indeks → {args.outdir / 'tile_index.json'}", file=sys.stderr)

    if not args.tetap_arsip:
        zippath.unlink()
        print(f"  arsip dihapus (unduh ulang dgn skrip ini bila perlu)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
