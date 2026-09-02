#!/usr/bin/env python3
"""Langkah 0 — cek prasyarat pipeline tanah-hilang.db. GAGAL KERAS bila ada yang kurang.

Pipeline lama melewati langkah inti secara diam-diam bila input absen (exit 0). Di sini tidak:
semua input inti wajib ada sebelum satu tabel pun dibangun.

    python pipeline/00_prasyarat.py            # cek saja
    python pipeline/00_prasyarat.py --json     # keluaran mesin
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib.db import AKAR  # noqa: E402

TAHUN_MB = range(2001, 2025)
HANSEN_TILE = ["00N_100E", "00N_110E", "10N_100E", "10N_110E"]

WAJIB = {
    "wiup_geojson": AKAR / "data/wiup/kalimantan_unique.geojson",
    "registry_minerbaone": AKAR / "data/minerba-kalimantan.db",
    "kepadatan_csv": AKAR / "data/kepadatan_penduduk.csv",
    "batch_hansen_csv": AKAR / "data/analysis/batch_KALIMANTAN_t30_wide.csv",
    "geoportal_manifest": AKAR / "data/geoportal/MANIFEST.csv",
    "mapbiomas_manifest": AKAR / "scripts/mapbiomas/manifest_c41_2000_2024.csv",
}
GEOPORTAL = [AKAR / f"data/geoportal/{n}.geojson" for n in ("ippkh_eksplorasi", "ippkh_operasi", "overlay_hutan")]
HANSEN = [AKAR / f"data/raster/Hansen_GFC-2025-v1.13_{jenis}_{t}.tif"
          for jenis in ("lossyear", "treecover2000") for t in HANSEN_TILE]
MAPBIOMAS = [AKAR / f"data/external/mapbiomas/mapbiomas_c41_{y}.tif" for y in TAHUN_MB]


def md5(path: Path, potong: int = 0) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tanpa-md5", action="store_true", help="lewati verifikasi MD5 raster MapBiomas (lambat)")
    ap.add_argument("--tanpa-mapbiomas", action="store_true",
                    help="izinkan raster MapBiomas absen (hanya untuk menjalankan langkah 01-03 & 06-07)")
    a = ap.parse_args()

    kurang: list[str] = []
    for nama, p in WAJIB.items():
        if not p.exists():
            kurang.append(f"{nama}: {p}")
    for p in GEOPORTAL + HANSEN:
        if not p.exists():
            kurang.append(str(p.relative_to(AKAR)))
    mb_kurang = [str(p.relative_to(AKAR)) for p in MAPBIOMAS if not p.exists()]
    if mb_kurang and not a.tanpa_mapbiomas:
        kurang.extend(mb_kurang)

    md5_salah: list[str] = []
    if not a.tanpa_md5 and not mb_kurang and WAJIB["mapbiomas_manifest"].exists():
        with open(WAJIB["mapbiomas_manifest"]) as f:
            baris = list(csv.DictReader(f))
        kolom_md5 = next((k for k in baris[0].keys() if "md5" in k.lower()), None) if baris else None
        kolom_thn = next((k for k in baris[0].keys() if "tahun" in k.lower() or "year" in k.lower()), None) if baris else None
        if kolom_md5 and kolom_thn:
            peta = {int(r[kolom_thn]): r[kolom_md5].strip().lower() for r in baris if r.get(kolom_thn)}
            for y in TAHUN_MB:
                p = AKAR / f"data/external/mapbiomas/mapbiomas_c41_{y}.tif"
                if y in peta and md5(p) != peta[y]:
                    md5_salah.append(p.name)

    hasil = {"ok": not kurang and not md5_salah, "kurang": kurang, "md5_salah": md5_salah,
             "mapbiomas_dilewati": bool(mb_kurang and a.tanpa_mapbiomas)}
    if a.json:
        print(json.dumps(hasil, indent=2, ensure_ascii=False))
    else:
        if kurang:
            print("Prasyarat KURANG:\n  - " + "\n  - ".join(kurang), file=sys.stderr)
        if md5_salah:
            print("MD5 raster MapBiomas TIDAK cocok manifest:\n  - " + "\n  - ".join(md5_salah), file=sys.stderr)
        if hasil["ok"]:
            print("Prasyarat lengkap." + (" (MapBiomas dilewati atas permintaan)" if hasil["mapbiomas_dilewati"] else ""))
    return 0 if hasil["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
