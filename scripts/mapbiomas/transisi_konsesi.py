#!/usr/bin/env python3
# STATUS  : ARSIP — tabel transisi_* di data/mapbiomas.db v2
# CATATAN : pengganti: pipeline/05_transisi.py (transisi_kohort, transisi_konsesi, transisi_pasangan; jendela 2001-2024)
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""Matriks transisi guna lahan per konsesi — bahan diagram Sankey.

MASALAH YANG DIJAWAB. `landuse_konsesi` menjawab "tahun ini isinya apa" —
komposisi kelas per tahun. Ia TIDAK bisa menjawab "kelas ini berubah jadi
apa", karena luas per kelas tidak menyimpan asal-usul piksel. Sankey butuh
ALIRAN, jadi butuh tabulasi silang piksel antara dua tahun.

CARA. Untuk tiap konsesi dan tiap pasangan tahun (A, B): baca kedua raster
MapBiomas pada jendela yang sama, ambil piksel di dalam topeng poligon, lalu
hitung `bincount(kelas_A * 100 + kelas_B)`. Luas dikoreksi lintang persis
seperti `landuse_konsesi.py` — grid, topeng, dan rumus luasnya memang dipakai
ulang dari sana supaya angkanya bisa direkonsiliasi.

INVARIAN YANG DIUJI. Jumlah `ha` seluruh sel transisi sebuah konsesi harus
sama dengan luas piksel berkelas di kedua tahun. Diperiksa per konsesi.

DUA MODE PASANGAN TAHUN.

  --pasangan 2001:2024,2001:2012,2012:2024
      Pasangan KALENDER yang sama untuk semua konsesi. Panelnya seimbang
      sempurna (825/825 konsesi punya 25 tahun penuh, kelas 27 nol baris),
      jadi tak ada masalah observasi hilang.

  --umur-relatif -10:0,0:10
      Pasangan RELATIF UMUR IZIN: offset dihitung dari t0 = tahun izin
      konsesi itu sendiri. `-10:0` = sepuluh tahun SEBELUM izin (garis dasar
      pembanding); `0:10` = dekade pertama sesudah izin.

      Di sinilah "observasi tidak tetap" muncul — dan di sinilah ia
      TERTANGANI: lebar jendelanya dikunci sama untuk semua konsesi, jadi
      kohortnya seimbang DALAM UMUR walau tidak seimbang dalam kalender.
      Konsesi yang salah satu ujungnya jatuh di luar 2000-2024 DIKELUARKAN
      dari pasangan itu, dan jumlah yang keluar dicatat di `transisi_kohort`
      supaya bisa diberi catatan kaki (bukan disembunyikan).

BATAS DATA YANG MENENTUKAN RANCANGAN (diukur 31 Agu 2026, lihat
docs/landasan-teori-dan-arah-tulisan.md §10.2): dengan t0 = max(iup_year,
2001), hanya 313 konsesi punya >=10 tahun pengamatan pasca-izin, 161 punya
>=15, dan cuma 14 punya >=20. Jendela umur 20 tahun TIDAK layak disimpulkan;
30 tahun mustahil karena Hansen memang mulai 2001.

KAVEAT WAJIB IKUT SETIAP SANKEY:
  1. Akurasi kelas Lubang Tambang tidak dipublikasikan MapBiomas — semua
     aliran ke/dari lubang tambang dibaca sebagai BATAS BAWAH.
  2. Transisi rawan derau salah-klasifikasi kalau jendelanya sempit. Semua
     jendela baku di sini lebar (>=10 tahun). Versi tahunan WAJIB memakai
     penyaring keteguhan multi-tahun.
  3. MapBiomas berhenti 2024; Hansen sampai 2025. Jangan dicampur dalam satu
     penyebut.

    python3 scripts/mapbiomas/transisi_konsesi.py
    python3 scripts/mapbiomas/transisi_konsesi.py --pasangan 2001:2024
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from landuse_konsesi import (  # noqa: E402  (pakai ulang, jangan duplikasi)
    LEGEND, LULC_DIR_DEFAULT, OUT_DB_DEFAULT, WIUP_DB_DEFAULT,
    baca_wiup, cek_grid_sama, siapkan_konsesi,
)

# kelas_awal * PENGALI + kelas_akhir. Kode kelas MapBiomas tertinggi = 76,
# jadi 100 aman; dijaga assert supaya kode baru tak diam-diam membungkus.
PENGALI = 100
NKOMBO = PENGALI * PENGALI

TAHUN_MIN, TAHUN_MAX = 2000, 2024      # cakupan raster yang kita punya
# Jendela kalender baku. Tiga RANTAI tiga-kolom disediakan, bukan satu, karena
# tahun tengah Sankey BUKAN pilihan netral: ia menentukan jalur apa yang bisa
# terlihat. Kolom tengah hanya berguna kalau jatuh DI ANTARA dua peristiwa,
# sedangkan dua proses utama di konsesi punya jadwal yang berbeda tajam
# (diukur dari landuse_konsesi, 31 Agu 2026):
#
#   Sawit          173 rb ha (2002) → 439 rb (2012) → 664 rb (2024); mendatar
#                  sesudah 2021. Separuh konversinya SELESAI sebelum 2012.
#   Lubang tambang  14 rb ha (2002) →  56 rb (2012) → 157 rb (2024); menumpuk
#                  di belakang — 105→128→157 rb pada 2022-2024 saja.
#
# Karena itu tak ada satu tahun tengah yang baik untuk keduanya, dan pilihannya
# diserahkan ke pembaca di UI alih-alih dikunci di sini:
#   tengah 2009  batas UU Minerba 4/2009; paling tajam utk jalur menuju tambang
#                (77% bukaan terjadi sesudahnya)
#   tengah 2012  titik tengah kalender; kompromi
#   tengah 2015  paling tajam utk jalur menuju sawit (separuh konversi sawit
#                sudah lewat pada 2012, jadi 2012 menyembunyikan jalurnya)
# Jendela kalender per-KONSESI yang disimpan penuh. Cukup satu: rentang utuh.
# Rantai tiga-kolom lewat 2009/2012/2015 dulu ikut di sini, tapi jadi mubazir
# begitu transisi_pasangan memuat SEMUA pasangan tahun — dan menyimpannya
# per konsesi menggandakan tabel tanpa menambah apa pun yang bisa dibaca.
PASANGAN_BAKU = "2001:2024"
UMUR_BAKU = "-10:0,0:10"

# SEMUA pasangan tahun (a < b) dalam 2001..2024 = 276 pasangan, disimpan
# AGREGAT lintas konsesi (tanpa kode_wiup). Ini yang menghidupi tampilan
# "tahun dinamis" di web: pembaca mencentang tahun mana saja, dan tiap
# langkahnya dibaca LANGSUNG dari pasangan yang bersangkutan.
#
# KENAPA SEMUA PASANGAN, BUKAN CUMA YANG BERURUTAN. Kalau pembaca memilih
# 2001, 2009, 2024, langkah 2001->2009 HARUS dihitung langsung. Menjumlahkan
# delapan langkah tahunan 2001->2002->...->2009 akan menghitung ganda piksel
# yang berubah lalu kembali — dan itu bukan galat kecil: jumlah 23 langkah
# tahunan 3.130.393 ha vs langsung 2001->2024 hanya 1.270.225 ha (rasio 2,46x).
#
# Bentuk agregat dipilih karena tampilan ini memang lintas-populasi: per
# konsesi ukurannya ~4,7 juta baris, agregat hanya puluhan ribu.
PASANGAN_MIN, PASANGAN_MAX = 2001, 2024

_SKRIP = "scripts/mapbiomas/transisi_konsesi.py"


_SUMBER_T = ("MapBiomas Indonesia Koleksi 4.1 (CC BY-SA) — raster nasional "
             "coverage_lclu, tabulasi silang piksel dua tahun di dalam poligon "
             "wiup_geoportal (dibaca READ-ONLY dari kalimantan.db)")

ANALYSIS_META = [
    ("transisi_konsesi",
     "Matriks transisi guna lahan per konsesi — BAHAN DIAGRAM SANKEY. Menjawab "
     "'kelas ini berubah jadi apa', yang tak bisa dijawab landuse_konsesi karena "
     "luas per kelas tidak menyimpan asal-usul piksel.",
     _SUMBER_T,
     "Untuk tiap konsesi & pasangan tahun: bincount(kelas_A*100 + kelas_B) pada "
     "piksel di dalam topeng poligon; luas dikoreksi cos(lintang). Grid, topeng, "
     "dan rumus luas dipakai ulang dari landuse_konsesi.py agar angkanya bisa "
     "direkonsiliasi (Sigma transisi = Sigma landuse kedua tahun).",
     _SKRIP, "AKTIF"),
    ("transisi_kohort",
     "Definisi tiap kohort Sankey + berapa konsesi KELUAR karena ujung jendelanya "
     "di luar 2000-2024. Sengaja disimpan supaya angkanya tak pernah tampil tanpa "
     "catatan kakinya.",
     _SUMBER_T, "Dicatat saat menyusun tugas per pasangan tahun.", _SKRIP, "AKTIF"),
    ("transisi_pasangan",
     "SEMUA pasangan tahun (a<b) dalam 2001-2024 (276 pasangan) — AGREGAT "
     "lintas konsesi, tanpa kode_wiup. Menghidupi tampilan \"tahun dinamis\": "
     "pembaca mencentang tahun mana saja dan tiap langkah dibaca LANGSUNG dari "
     "pasangannya, bukan dari penjumlahan langkah tahunan (yang akan menghitung "
     "ganda piksel berubah-lalu-kembali; rasio 2,46x untuk 2001-2024).",
     _SUMBER_T,
     "Per konsesi: 24 raster dibaca sekali, lalu 276 tabulasi silang dihitung "
     "dari larik di memori. Agregat karena tampilannya lintas-populasi.",
     _SKRIP, "AKTIF"),
    ("v_transisi_agregat",
     "Agregat lintas konsesi per (label, kelas asal, kelas tujuan) — bentuk yang "
     "langsung dipakai diagram Sankey di web.",
     "transisi_konsesi", "GROUP BY label, kelas_awal, kelas_akhir.", _SKRIP, "AKTIF"),
]

COLUMN_META = [
    ("transisi_konsesi", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER_T),
    ("transisi_konsesi", "label", "Nama kohort: jendela kalender ('2001-2024') atau "
     "relatif umur izin ('umur+0_+10').", "-", _SUMBER_T),
    ("transisi_konsesi", "tahun_awal", "Tahun kalender sisi asal. Untuk kohort umur ini "
     "BERBEDA antar konsesi — itulah sebabnya disimpan per baris.", "-", _SUMBER_T),
    ("transisi_konsesi", "tahun_akhir", "Tahun kalender sisi tujuan.", "-", _SUMBER_T),
    ("transisi_konsesi", "kelas_awal", "Kode kelas MapBiomas di tahun_awal.", "-", _SUMBER_T),
    ("transisi_konsesi", "kelas_akhir", "Kode kelas MapBiomas di tahun_akhir.", "-", _SUMBER_T),
    ("transisi_konsesi", "nama_awal", "Nama kelas asal (legenda resmi C4.1).", "-", _SUMBER_T),
    ("transisi_konsesi", "nama_akhir", "Nama kelas tujuan.", "-", _SUMBER_T),
    ("transisi_konsesi", "pixels", "Cacah piksel yang menempuh transisi ini.",
     "bincount(kelas_awal*100 + kelas_akhir)", _SUMBER_T),
    ("transisi_konsesi", "ha", "Luas transisi. AWAS: aliran ke/dari Lubang Tambang "
     "adalah BATAS BAWAH — akurasi kelas itu tak dipublikasikan MapBiomas.",
     "Sigma luas piksel, koreksi cos(lintang)", _SUMBER_T),
    ("transisi_kohort", "jenis", "'kalender' atau 'umur'.", "-", _SUMBER_T),
    ("transisi_kohort", "label", "Nama kohort — kunci ke transisi_konsesi.label.",
     "-", _SUMBER_T),
    ("transisi_kohort", "awal", "Tahun awal (kalender) atau offset awal (umur).",
     "-", _SUMBER_T),
    ("transisi_kohort", "akhir", "Tahun akhir (kalender) atau offset akhir (umur).",
     "-", _SUMBER_T),
    ("transisi_kohort", "n_konsesi", "Konsesi yang MASUK kohort ini.", "-", _SUMBER_T),
    ("transisi_kohort", "n_keluar", "Konsesi yang KELUAR karena salah satu ujung "
     "jendelanya di luar 2000-2024. Wajib ikut dilaporkan.", "-", _SUMBER_T),
    ("transisi_kohort", "catatan", "Penjelasan kohort + catatan kaki yang harus ikut "
     "tampil bersama angkanya.", "-", _SUMBER_T),
    ("transisi_pasangan", "tahun_awal", "Tahun sisi asal.", "-", _SUMBER_T),
    ("transisi_pasangan", "tahun_akhir", "Tahun sisi tujuan (selalu > tahun_awal).",
     "-", _SUMBER_T),
    ("transisi_pasangan", "kelas_awal", "Kode kelas MapBiomas di tahun_awal.", "-", _SUMBER_T),
    ("transisi_pasangan", "kelas_akhir", "Kode kelas MapBiomas di tahun_akhir.", "-", _SUMBER_T),
    ("transisi_pasangan", "nama_awal", "Nama kelas asal.", "-", _SUMBER_T),
    ("transisi_pasangan", "nama_akhir", "Nama kelas tujuan.", "-", _SUMBER_T),
    ("transisi_pasangan", "n_konsesi", "Banyak konsesi yang punya aliran ini pada "
     "pasangan tahun itu.", "COUNT(DISTINCT kode_wiup)", _SUMBER_T),
    ("transisi_pasangan", "pixels", "Cacah piksel lintas konsesi.", "-", _SUMBER_T),
    ("transisi_pasangan", "ha", "Luas lintas konsesi untuk pasangan tahun itu — dihitung LANGSUNG, "
     "jadi sudah bersih dari hitung-ganda.", "Sigma luas piksel, koreksi cos(lintang)", _SUMBER_T),
    ("v_transisi_agregat", "label", "Nama kohort.", "-", _SUMBER_T),
    ("v_transisi_agregat", "kelas_awal", "Kode kelas asal.", "-", _SUMBER_T),
    ("v_transisi_agregat", "kelas_akhir", "Kode kelas tujuan.", "-", _SUMBER_T),
    ("v_transisi_agregat", "nama_awal", "Nama kelas asal.", "-", _SUMBER_T),
    ("v_transisi_agregat", "nama_akhir", "Nama kelas tujuan.", "-", _SUMBER_T),
    ("v_transisi_agregat", "n_konsesi", "Banyak konsesi yang punya aliran ini.",
     "COUNT(DISTINCT kode_wiup)", _SUMBER_T),
    ("v_transisi_agregat", "pixels", "Total piksel lintas konsesi.", "SUM(pixels)", _SUMBER_T),
    ("v_transisi_agregat", "ha", "Total luas lintas konsesi.", "ROUND(SUM(ha), 2)", _SUMBER_T),
]


def urai_pasangan(s: str) -> list[tuple[int, int]]:
    out = []
    for bag in s.split(","):
        bag = bag.strip()
        if not bag:
            continue
        a, b = bag.split(":")
        ya, yb = int(a), int(b)
        if not (TAHUN_MIN <= ya < yb <= TAHUN_MAX):
            raise SystemExit(
                f"Pasangan {bag} di luar cakupan raster {TAHUN_MIN}-{TAHUN_MAX} "
                "atau tahun awal >= tahun akhir.")
        out.append((ya, yb))
    return out


def urai_offset(s: str) -> list[tuple[int, int]]:
    out = []
    for bag in s.split(","):
        bag = bag.strip()
        if not bag:
            continue
        a, b = bag.split(":")
        oa, ob = int(a), int(b)
        if oa >= ob:
            raise SystemExit(f"Offset {bag}: awal harus < akhir.")
        out.append((oa, ob))
    return out


def baca_t0(db_path: Path) -> dict[str, int]:
    """t0 per konsesi = max(iup_year, 2001).

    Sengaja memakai `iup_year` APA ADANYA, bukan `mulai` dari
    atribusi_izin_aktif: `mulai` sudah mengandung klaim aturan INDIKASI
    (perpanjangan dianggap aktif sejak awal jendela), dan mencampurnya di
    sini akan mengunci Sankey ke satu tafsir. Lantai 2001 dipakai karena
    itu batas rekaman Hansen — biar sumbu umur sebanding dgn analisis loss.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT kode_wiup, iup_year FROM wiup_geoportal "
            "WHERE iup_year IS NOT NULL").fetchall()
    finally:
        con.close()
    return {k: max(int(y), 2001) for k, y in rows}


def silang(arr_a, arr_b, mask, area) -> tuple[np.ndarray, np.ndarray]:
    """(pixels, ha) per kombinasi kelas, panjang NKOMBO."""
    va = arr_a[mask].astype(np.int32)
    vb = arr_b[mask].astype(np.int32)
    assert va.max(initial=0) < PENGALI and vb.max(initial=0) < PENGALI, \
        "Ada kode kelas >= 100 — PENGALI harus dinaikkan."
    kombo = va * PENGALI + vb
    px = np.bincount(kombo, minlength=NKOMBO)
    ha = np.bincount(kombo, weights=area[mask], minlength=NKOMBO)
    return px, ha


def kerjakan_pasangan(ds_a, ds_b, konsesi, ikut) -> list[tuple]:
    """Tabulasi silang satu pasangan tahun untuk konsesi di `ikut` (set kode)."""
    baris = []
    for kode, win, mask, area in konsesi:
        if kode not in ikut:
            continue
        px, ha = silang(ds_a.read(1, window=win), ds_b.read(1, window=win),
                        mask, area)
        for k in np.nonzero(px)[0]:
            ka, kb = divmod(int(k), PENGALI)
            if ka == 0 and kb == 0:
                continue              # nodata↔nodata: laut / luar Indonesia
            baris.append((kode, ka, kb, int(px[k]), round(float(ha[k]), 4)))
    return baris


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lulc-dir", default=str(LULC_DIR_DEFAULT))
    ap.add_argument("--wiup-db", default=str(WIUP_DB_DEFAULT))
    ap.add_argument("--out-db", default=str(OUT_DB_DEFAULT))
    ap.add_argument("--pasangan", default=PASANGAN_BAKU,
                    help=f"pasangan tahun kalender (baku: {PASANGAN_BAKU})")
    ap.add_argument("--umur-relatif", default=UMUR_BAKU,
                    help=f"offset relatif tahun izin (baku: {UMUR_BAKU}); "
                         "kosongkan untuk melewati")
    ap.add_argument("--tanpa-pasangan", action="store_true",
                    help="lewati tabel semua-pasangan-tahun (transisi_pasangan)")
    a = ap.parse_args(argv)

    kalender = urai_pasangan(a.pasangan) if a.pasangan.strip() else []
    offset = urai_offset(a.umur_relatif) if a.umur_relatif.strip() else []

    lulc_dir = Path(a.lulc_dir)
    wiup = baca_wiup(Path(a.wiup_db))
    t0 = baca_t0(Path(a.wiup_db))
    print(f"{len(wiup)} konsesi; {len(t0)} punya iup_year")

    tahun_perlu = {y for p in kalender for y in p}
    for oa, ob in offset:
        for t in t0.values():
            tahun_perlu.update((t + oa, t + ob))
    tahun_perlu = {t for t in tahun_perlu if TAHUN_MIN <= t <= TAHUN_MAX}
    berkas = {t: lulc_dir / f"mapbiomas_c41_{t}.tif" for t in sorted(tahun_perlu)}
    hilang = [t for t, p in berkas.items() if not p.exists()]
    if hilang:
        raise SystemExit(f"Raster LULC belum ada utk {hilang} — fetch dulu.")

    ref = rasterio.open(berkas[min(berkas)])
    for p in list(berkas.values())[1:]:
        cek_grid_sama(ref, p)
    konsesi = siapkan_konsesi(wiup, ref)
    ref.close()
    kode_ada = {k for k, _, _, _ in konsesi}
    print(f"{len(konsesi)} konsesi punya piksel di grid MapBiomas")

    # ── Susun tugas: label → (tahun_awal, tahun_akhir) → himpunan konsesi ──
    # Dikelompokkan per pasangan tahun supaya tiap raster dibuka sekali saja.
    tugas: dict[tuple[str, int, int], set[str]] = {}
    kohort: list[tuple] = []

    for ya, yb in kalender:
        label = f"{ya}-{yb}"
        tugas[(label, ya, yb)] = set(kode_ada)
        kohort.append(("kalender", label, ya, yb, len(kode_ada), 0,
                       f"Semua konsesi, jendela kalender {ya}-{yb}."))

    for oa, ob in offset:
        label = f"umur{oa:+d}_{ob:+d}"
        ikut, keluar = 0, 0
        for kode in kode_ada:
            t = t0.get(kode)
            if t is None:
                keluar += 1
                continue
            ya, yb = t + oa, t + ob
            if not (TAHUN_MIN <= ya and yb <= TAHUN_MAX):
                keluar += 1
                continue
            tugas.setdefault((label, ya, yb), set()).add(kode)
            ikut += 1
        kohort.append(("umur", label, oa, ob, ikut, keluar,
                       f"Jendela {ob - oa} tahun relatif tahun izin (t0 = "
                       f"max(iup_year, 2001)), offset {oa:+d} sampai {ob:+d}. "
                       f"{ikut} konsesi masuk, {keluar} keluar karena salah satu "
                       f"ujungnya di luar {TAHUN_MIN}-{TAHUN_MAX}."))
        print(f"  {label}: {ikut} masuk, {keluar} keluar")

    # ── Jalankan, satu pasangan tahun sekali baca ────────────────────────────
    semua: list[tuple] = []
    for (label, ya, yb), ikut in sorted(tugas.items()):
        with rasterio.open(berkas[ya]) as da, rasterio.open(berkas[yb]) as db_:
            baris = kerjakan_pasangan(da, db_, konsesi, ikut)
        semua.extend((kode, label, ya, yb, ka, kb,
                      LEGEND.get(ka, (f"UNKNOWN_{ka}", ""))[0],
                      LEGEND.get(kb, (f"UNKNOWN_{kb}", ""))[0], px, ha)
                     for kode, ka, kb, px, ha in baris)
        print(f"  {label} {ya}->{yb}: {len(ikut)} konsesi, "
              f"{len(baris):,} sel transisi", flush=True)

    tahunan = []
    if not a.tanpa_pasangan:
        tahunan = semua_pasangan(lulc_dir, konsesi)

    tulis(Path(a.out_db), semua, kohort, tahunan)
    print(f"\nSelesai — {len(semua):,} baris transisi, {len(kohort)} kohort, "
          f"{len(tahunan):,} baris semua-pasangan.")
    return 0


def semua_pasangan(lulc_dir: Path, konsesi) -> list[tuple]:
    """Agregat transisi untuk SEMUA pasangan tahun (a < b) di 2001..2024.

    Dikerjakan per KONSESI, bukan per pasangan: tiap raster dibaca sekali per
    konsesi (24 x 825 = 19.800 pembacaan jendela), lalu 276 tabulasi silang
    dihitung dari larik yang sudah di memori. Kalau dibalik — per pasangan —
    pembacaannya jadi 276 x 2 x 825 dan berkali-kali lebih lama.
    """
    tahun = list(range(PASANGAN_MIN, PASANGAN_MAX + 1))
    berkas = {y: lulc_dir / f"mapbiomas_c41_{y}.tif" for y in tahun}
    kurang = [y for y, p in berkas.items() if not p.exists()]
    if kurang:
        print(f"  (lewati semua-pasangan: raster tak lengkap utk {kurang})")
        return []

    pasangan = [(a, b) for i, a in enumerate(tahun) for b in tahun[i + 1:]]
    print(f"  semua-pasangan: {len(pasangan)} pasangan x {len(konsesi)} konsesi…")

    agg: dict[tuple[int, int, int, int], list] = {}
    ds = {y: rasterio.open(p) for y, p in berkas.items()}
    try:
        for n, (kode, win, mask, area) in enumerate(konsesi, 1):
            nilai = {y: ds[y].read(1, window=win)[mask].astype(np.int32)
                     for y in tahun}
            luas = area[mask]
            for ya, yb in pasangan:
                kombo = nilai[ya] * PENGALI + nilai[yb]
                px = np.bincount(kombo, minlength=NKOMBO)
                ha = np.bincount(kombo, weights=luas, minlength=NKOMBO)
                for k in np.nonzero(px)[0]:
                    ka, kb = divmod(int(k), PENGALI)
                    if ka == 0 and kb == 0:
                        continue
                    s = agg.setdefault((ya, yb, ka, kb), [0, 0.0, 0])
                    s[0] += int(px[k])
                    s[1] += float(ha[k])
                    s[2] += 1
            if n % 100 == 0:
                print(f"    {n}/{len(konsesi)} konsesi, {len(agg):,} sel", flush=True)
    finally:
        for d in ds.values():
            d.close()

    return [(ya, yb, ka, kb,
             LEGEND.get(ka, (f"UNKNOWN_{ka}", ""))[0],
             LEGEND.get(kb, (f"UNKNOWN_{kb}", ""))[0],
             n_kons, px, round(ha, 4))
            for (ya, yb, ka, kb), (px, ha, n_kons) in sorted(agg.items())]


def tulis(out: Path, baris: list[tuple], kohort: list[tuple],
          tahunan: list[tuple]) -> None:
    con = sqlite3.connect(out)
    try:
        con.executescript("""
            DROP TABLE IF EXISTS transisi_konsesi;
            DROP TABLE IF EXISTS transisi_kohort;
            -- transisi_tahunan: tabel lama (hanya 23 langkah berurutan) yang
            -- digantikan transisi_pasangan (276 pasangan). Di-drop supaya DB
            -- lama tak menyisakan tabel usang yang bisa terbaca keliru.
            DROP TABLE IF EXISTS transisi_tahunan;
            DROP TABLE IF EXISTS transisi_pasangan;
            DROP VIEW IF EXISTS v_transisi_agregat;
            CREATE TABLE transisi_konsesi (
                kode_wiup    TEXT    NOT NULL,
                label        TEXT    NOT NULL,
                tahun_awal   INTEGER NOT NULL,
                tahun_akhir  INTEGER NOT NULL,
                kelas_awal   INTEGER NOT NULL,
                kelas_akhir  INTEGER NOT NULL,
                nama_awal    TEXT    NOT NULL,
                nama_akhir   TEXT    NOT NULL,
                pixels       INTEGER NOT NULL,
                ha           REAL    NOT NULL,
                PRIMARY KEY (kode_wiup, label, kelas_awal, kelas_akhir)
            );
            CREATE INDEX idx_trans_label ON transisi_konsesi(label);
            CREATE TABLE transisi_kohort (
                jenis      TEXT    NOT NULL,
                label      TEXT    PRIMARY KEY,
                awal       INTEGER NOT NULL,
                akhir      INTEGER NOT NULL,
                n_konsesi  INTEGER NOT NULL,
                n_keluar   INTEGER NOT NULL,
                catatan    TEXT    NOT NULL
            );
            CREATE TABLE transisi_pasangan (
                tahun_awal   INTEGER NOT NULL,
                tahun_akhir  INTEGER NOT NULL,
                kelas_awal   INTEGER NOT NULL,
                kelas_akhir  INTEGER NOT NULL,
                nama_awal    TEXT    NOT NULL,
                nama_akhir   TEXT    NOT NULL,
                n_konsesi    INTEGER NOT NULL,
                pixels       INTEGER NOT NULL,
                ha           REAL    NOT NULL,
                PRIMARY KEY (tahun_awal, tahun_akhir, kelas_awal, kelas_akhir)
            );
            CREATE INDEX idx_pasangan ON transisi_pasangan(tahun_awal, tahun_akhir);
            CREATE VIEW v_transisi_agregat AS
                SELECT label, kelas_awal, kelas_akhir, nama_awal, nama_akhir,
                       COUNT(DISTINCT kode_wiup) AS n_konsesi,
                       SUM(pixels)               AS pixels,
                       ROUND(SUM(ha), 2)         AS ha
                FROM transisi_konsesi
                GROUP BY label, kelas_awal, kelas_akhir;
        """)
        con.executemany("INSERT INTO transisi_konsesi VALUES (?,?,?,?,?,?,?,?,?,?)",
                        baris)
        con.executemany("INSERT INTO transisi_kohort VALUES (?,?,?,?,?,?,?)", kohort)
        con.executemany("INSERT INTO transisi_pasangan VALUES (?,?,?,?,?,?,?,?,?)", tahunan)

        # Kamus Data (Konvensi #4/#5). Skrip ini menulis HANYA baris miliknya —
        # landuse_konsesi.py melakukan hal yang sama untuk tabelnya, sehingga
        # dua skrip bisa berbagi mapbiomas.db tanpa saling menghapus metadata.
        con.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_meta (
                nama_tabel TEXT PRIMARY KEY, deskripsi TEXT, sumber TEXT, metode TEXT,
                script TEXT, status TEXT NOT NULL DEFAULT 'AKTIF'
                CHECK (status IN ('AKTIF','ARSIP','PROYEKSI')));
            CREATE TABLE IF NOT EXISTS column_meta (
                nama_tabel TEXT, nama_kolom TEXT, deskripsi TEXT, rumus TEXT, sumber TEXT,
                PRIMARY KEY (nama_tabel, nama_kolom));
        """)
        con.executemany("INSERT OR REPLACE INTO analysis_meta VALUES (?,?,?,?,?,?)",
                        ANALYSIS_META)
        con.executemany("INSERT OR REPLACE INTO column_meta VALUES (?,?,?,?,?)",
                        COLUMN_META)

        # Guard cakupan dua arah utk objek milik skrip ini.
        for nama in sorted({r[0] for r in ANALYSIS_META}):
            nyata = {r[1] for r in con.execute(f'PRAGMA table_info("{nama}")')}
            terdok = {r[0] for r in con.execute(
                "SELECT nama_kolom FROM column_meta WHERE nama_tabel=?", (nama,))}
            if nyata != terdok:
                raise SystemExit(
                    f"column_meta {nama} tak sinkron: kurang={sorted(nyata - terdok)} "
                    f"lebih={sorted(terdok - nyata)}")
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
