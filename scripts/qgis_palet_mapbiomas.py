#!/usr/bin/env python3
"""Buat berkas palet warna MapBiomas C4.1 untuk QGIS dari tabel `mapbiomas_kelas`.

    python3 scripts/qgis_palet_mapbiomas.py

Keluaran (disajikan web sebagai unduhan Panduan QGIS langkah 11):
    webapp/public/qgis/mapbiomas_c41_palet.txt          — bawaan peta web (tanpa 33 & 27)
    webapp/public/qgis/mapbiomas_c41_palet_lengkap.txt  — semua kelas

Format = yang dibaca QGIS "Load Color Map from File…" pada renderer Paletted/Unique
values (QgsPalettedRasterRenderer::classDataFromString, QGIS 3.44): satu baris per
kelas `nilai R G B alfa label`, pemisah spasi/koma; baris berawalan '#' diabaikan.
Koma di label ikut dipecah QGIS lalu disambung spasi, jadi label ditulis tanpa koma.
Sumber warna & nama = DB (sumber tunggal legenda, sama dgn peta web).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
DB = AKAR / "data" / "tanah-hilang.db"
KELUAR = AKAR / "webapp" / "public" / "qgis"
MATI_BAWAAN = {33, 27}  # Sungai/Danau/Laut & Citra Tertutup Awan — bawaan mati di peta web


def baris(kelas: int, nama: str, hexwarna: str) -> str:
    h = hexwarna.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"{kelas} {r} {g} {b} 255 {kelas} {nama.replace(',', '')}"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    kelas = con.execute("SELECT kelas, nama, hex FROM mapbiomas_kelas ORDER BY kelas").fetchall()
    con.close()
    KELUAR.mkdir(parents=True, exist_ok=True)
    kepala = [
        "# Palet MapBiomas Indonesia Koleksi 4.1 (CC BY-SA) — warna & nama dari tabel mapbiomas_kelas",
        "# Dibuat oleh scripts/qgis_palet_mapbiomas.py. Format: nilai R G B alfa label",
    ]
    for nama_berkas, saring in (("mapbiomas_c41_palet.txt", True), ("mapbiomas_c41_palet_lengkap.txt", False)):
        isi = [baris(k, n, h) for k, n, h in kelas if not (saring and k in MATI_BAWAAN)]
        catatan = ["# Tanpa kelas 33 & 27 (bawaan mati di peta web)."] if saring else ["# Semua kelas."]
        (KELUAR / nama_berkas).write_text("\n".join(kepala + catatan + isi) + "\n", encoding="utf-8")
        print(f"{KELUAR / nama_berkas}: {len(isi)} kelas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
