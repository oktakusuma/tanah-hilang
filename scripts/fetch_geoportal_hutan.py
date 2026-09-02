#!/usr/bin/env python3
# STATUS  : PRASYARAT AKTIF — mengunduh data/geoportal/*.geojson + MANIFEST.csv (dipakai 01_identitas & 06_kawasan_hutan)
# CATATAN : di luar bangun.sh (~51 MB); MANIFEST.csv sudah disertakan di bundel supaya MD5 unduhan bisa diaudit
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""Unduh tiga layer kehutanan Geoportal ESDM (gis1) untuk Kalimantan.

KENAPA. Analisis guna lahan butuh dua hal yang belum ada di `kalimantan.db`:
(a) apakah konsesi memegang IPPKH — izin resmi memakai kawasan hutan; dan
(b) status hukum lahan di dalam konsesi (APL / HP / HPK / HL / HK) sebagai
penyebut yang tepat, bukan sekadar luas poligon.

Ketiganya publik, tanpa token, di server gis1 yang sama dengan sumber WIUP.

  ippkh_eksplorasi   layer 0 — 58 poligon nasional. TANPA kolom nama; hanya
                     bisa dipakai spasial.
  ippkh_operasi      layer 1 — 1.208 nasional / 397 Kalimantan. Beratribut
                     nama_ppkh, no_ppkh, tgl_ppkh, luas_ppkh, jenis_ppkh.
  overlay_hutan      Overlay_WIUP_vs_Kawasan_Hutan layer 0 — 3.962 poligon
                     irisan WIUP x kawasan hutan di Kalimantan. Punya
                     `kode_wiup`, jadi menyambung LANGSUNG ke 825 konsesi
                     tanpa pencocokan spasial. Juga membawa `tgl_berlak` /
                     `tgl_akhir` yang di `wiup_geoportal` kita semuanya NULL.

KAVEAT YANG WAJIB IKUT. Layer IPPKH hanya memuat izin berstatus **Aktif**
(diverifikasi 31 Agu 2026: 397 dari 397). Ia potret izin berlaku, BUKAN
register sejarah — IPPKH yang sudah habis tidak muncul. Karena itu angka
apa pun darinya adalah BATAS BAWAH, dan tak boleh dibaca sebagai bukti
bahwa konsesi tanpa IPPKH membuka hutan tanpa izin.

Keluaran: data/geoportal/<nama>.geojson (EPSG:4326).

GEOJSON MENTAHNYA **GITIGNORED** (~51 MB) — turunan murni yang bisa diunduh
ulang kapan saja lewat skrip ini, mengikuti aturan yang sama dengan
data/tiles/ dan data/external/. Yang DI-COMMIT adalah (a) bentuk analitiknya
di data/kalimantan.db (konsesi_ippkh, konsesi_kawasan_hutan,
ippkh_konsesi_irisan, wiup_tanggal_pulih) dan (b) manifes
data/geoportal/MANIFEST.csv berisi jumlah fitur + MD5 + tanggal unduh +
kueri persisnya, supaya reproduksinya tetap bisa diaudit.

    python3 scripts/fetch_geoportal_hutan.py             # semua layer
    python3 scripts/fetch_geoportal_hutan.py --layer overlay_hutan
    python3 scripts/fetch_geoportal_hutan.py --limit 50  # cicip
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import time
import datetime as dt
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

BASE = "https://geoportal.esdm.go.id/gis1/rest/services"

# kode_prov BPS untuk lima provinsi Kalimantan
KODE_PROV_KALIMANTAN = (61, 62, 63, 64, 65)

LAYER = {
    "ippkh_eksplorasi": {
        "url": f"{BASE}/Izin_Pinjam_Pakai_Kawasan_Hutan/MapServer/0/query",
        "where": "kode_prov IN (61,62,63,64,65)",
        "catatan": "IPPKH tahap eksplorasi; TANPA kolom nama pemegang",
    },
    "ippkh_operasi": {
        "url": f"{BASE}/Izin_Pinjam_Pakai_Kawasan_Hutan/MapServer/1/query",
        "where": "kode_prov IN (61,62,63,64,65)",
        "catatan": "IPPKH operasi produksi & non-tambang; beratribut lengkap",
    },
    "overlay_hutan": {
        "url": f"{BASE}/Overlay_WIUP_vs_Kawasan_Hutan/MapServer/0/query",
        "where": "pulau='KALIMANTAN'",
        "catatan": "Irisan WIUP x kawasan hutan; punya kode_wiup",
    },
}

UKURAN_HALAMAN = 500          # < maxRecordCount (2000); geometri poligon berat
JEDA_MIN, JEDA_MAX = 1.5, 3.0
MAKS_ULANG = 4
MUNDUR_AWAL = 2.0
AGEN = "Thesis-GIS-Research/1.0 (Universitas; contact: gunawan@cynopsis.co)"


def ambil(url: str, params: dict) -> dict:
    """GET ber-retry dengan mundur eksponensial. Melempar bila habis jatah."""
    penuh = f"{url}?{urlencode(params)}"
    mundur = MUNDUR_AWAL
    for percobaan in range(1, MAKS_ULANG + 1):
        try:
            req = urllib.request.Request(penuh, headers={"User-Agent": AGEN})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if "error" in data:
                raise RuntimeError(f"Galat server ArcGIS: {data['error']}")
            return data
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, json.JSONDecodeError) as e:
            if percobaan == MAKS_ULANG:
                raise RuntimeError(f"Gagal setelah {MAKS_ULANG} percobaan: {e}") from e
            print(f"    percobaan {percobaan} gagal ({e}); ulang dalam {mundur:.0f} dtk",
                  file=sys.stderr)
            time.sleep(mundur)
            mundur *= 2
    raise AssertionError("tak tercapai")


def unduh_layer(nama: str, spek: dict, batas: int | None) -> dict:
    """Tarik seluruh layer secara berhalaman → FeatureCollection GeoJSON."""
    print(f"\n[{nama}] {spek['catatan']}")
    jumlah = ambil(spek["url"], {
        "where": spek["where"], "returnCountOnly": "true", "f": "json"})["count"]
    target = min(jumlah, batas) if batas else jumlah
    print(f"    {jumlah} fitur di server; akan diambil {target}")

    fitur: list[dict] = []
    offset = 0
    while len(fitur) < target:
        n = min(UKURAN_HALAMAN, target - len(fitur))
        data = ambil(spek["url"], {
            "where": spek["where"],
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "resultOffset": offset,
            "resultRecordCount": n,
            "f": "geojson",
        })
        batch = data.get("features", [])
        if not batch:
            print(f"    halaman kosong di offset {offset}; berhenti lebih awal")
            break
        fitur.extend(batch)
        offset += len(batch)
        print(f"    {len(fitur)}/{target}")
        if len(fitur) < target:
            time.sleep(random.uniform(JEDA_MIN, JEDA_MAX))

    return {"type": "FeatureCollection",
            "crs": {"type": "name",
                    "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": fitur}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layer", choices=sorted(LAYER), action="append",
                    help="hanya layer ini (boleh diulang); baku: semua")
    ap.add_argument("--limit", type=int, help="ambil maksimal N fitur (untuk cicip)")
    ap.add_argument("--keluaran", default="data/geoportal", type=Path)
    args = ap.parse_args(argv)

    args.keluaran.mkdir(parents=True, exist_ok=True)
    dipilih = args.layer or sorted(LAYER)

    manifes = []
    for nama in dipilih:
        fc = unduh_layer(nama, LAYER[nama], args.limit)
        tujuan = args.keluaran / f"{nama}.geojson"
        isi = json.dumps(fc, ensure_ascii=False)
        tujuan.write_text(isi, encoding="utf-8")
        mb = tujuan.stat().st_size / 1e6
        print(f"    ditulis {tujuan} — {len(fc['features'])} fitur, {mb:.1f} MB")
        manifes.append({
            "berkas": tujuan.name,
            "n_fitur": len(fc["features"]),
            "byte": tujuan.stat().st_size,
            "md5": hashlib.md5(isi.encode("utf-8")).hexdigest(),
            "tanggal_unduh": dt.date.today().isoformat(),
            "url": LAYER[nama]["url"],
            "where": LAYER[nama]["where"],
        })

    tulis_manifes(args.keluaran, manifes)
    return 0


def tulis_manifes(dirkeluaran: Path, baris: list[dict]) -> None:
    """Perbarui MANIFEST.csv — inilah yang di-commit, bukan geojson-nya.

    Digabung dengan baris lama supaya menjalankan --layer tunggal tidak
    menghapus catatan layer lain.
    """
    path = dirkeluaran / "MANIFEST.csv"
    kolom = ["berkas", "n_fitur", "byte", "md5", "tanggal_unduh", "url", "where"]
    lama = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                lama[r["berkas"]] = r
    for r in baris:
        lama[r["berkas"]] = {k: str(r[k]) for k in kolom}
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=kolom)
        w.writeheader()
        for k in sorted(lama):
            w.writerow(lama[k])
    print(f"    manifes diperbarui: {path} ({len(lama)} berkas)")


if __name__ == "__main__":
    raise SystemExit(main())
