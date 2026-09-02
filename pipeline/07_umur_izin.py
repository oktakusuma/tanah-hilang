#!/usr/bin/env python3
"""Langkah 07 — tren deforestasi menurut UMUR IZIN, kurun 5 tahun, dinormalkan (SKEMA.md §6).

Port dari `scripts/build_umur_izin.py` (pipeline arsip): algoritma SAMA, input kini
`konsesi.tahun_izin` + `konsesi.luas_poligon_ha`, `hansen_ringkas.hutan_2000_ha`, dan
`hansen_tahunan` (jendela 2001–2024). Satu-satunya perubahan nama kolom: `loss_ha` → `hilang_ha`.

SUMBU UMUR. t0 = max(tahun_izin, 2001). Lantai 2001 = awal rekaman Hansen. Umur relatif
r = tahun kalender − t0; r < 0 = sebelum izin.

BATAS DATA YANG MENENTUKAN — sebutkan di tiap laporan: konsesi yang punya pengamatan
pasca-izin >= 20 tahun hanya belasan. Kurun 20+ = catatan kaki; kurun 30 tahun mustahil
dengan Hansen. `n_konsesi` dan `catatan` ikut disimpan supaya angkanya tak pernah tampil
tanpa bintangnya.

NORMALISASI = STOK YANG BERISIKO, bukan luas konsesi:

    laju bahaya = Σ(kehilangan dalam kurun) / Σ(hutan berdiri di AWAL tiap tahun teramati)

hutan(t) = hutan_2000_ha − kehilangan kumulatif 2001..t−1 (identitas Hansen sendiri, BUKAN
stok MapBiomas — hierarki sumber: kehilangan tutupan hutan = Hansen). Versi dibagi luas
poligon ikut dilaporkan sebagai pendamping.

DUA RANCANGAN, keduanya dijalankan; selisihnya sendiri adalah temuan:
  A_seimbang  hanya konsesi ber-tahun_izin 2009–2015 (n=260): 8 tahun penuh SEBELUM dan
              10 tahun penuh SESUDAH izin; kumpulan konsesi sama di tiap kurun → bebas bias
              komposisi.
  B_semua     seluruh konsesi ber-tahun_izin; tiap konsesi menyumbang ke kurun mana pun
              yang teramati; n dan paparan wajib ikut dibaca.

    .venv/bin/python pipeline/07_umur_izin.py --db data/tanah-hilang.db --himpunan minerba
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib.db import argparser, buka, gagal, tandai_selesai, wajib_tabel  # noqa: E402
from pipeline.lib.meta import LISENSI, tulis_meta  # noqa: E402

SKRIP = "pipeline/07_umur_izin.py"
TAHUN_MIN, TAHUN_MAX = 2001, 2024      # jendela tesis (proposal v0.3.2; igoen 1 Sep 2026)

KURUN = [
    ("-8..-1", -8, -1, "Delapan tahun SEBELUM izin — garis dasar pembanding."),
    ("0..4",    0,  4, "Lima tahun pertama izin."),
    ("5..9",    5,  9, "Tahun ke-6 sampai ke-10."),
    ("10..14", 10, 14, "Dekade kedua, paruh pertama — n menipis."),
    ("15..19", 15, 19, "Dekade kedua, paruh kedua — n sangat tipis."),
    ("20..24", 20, 24, "CATATAN KAKI SAJA: hanya belasan konsesi yang seumur ini teramati; JANGAN disimpulkan."),
]
# Kohort seimbang: 8 tahun penuh sebelum izin (t0-8 >= 2001) dan 10 tahun penuh sesudah (t0+9 <= 2024).
SEIMBANG_MIN, SEIMBANG_MAX = 2009, 2015
KURUN_SEIMBANG = {"-8..-1", "0..4", "5..9"}

SUMBER = ("Hansen GFC 2025 v1.13 ambang kanopi 30 (hansen_tahunan.hilang_ha, hansen_ringkas.hutan_2000_ha); "
          "tahun izin & luas poligon dari konsesi (Geoportal ESDM WIUP)")


def muat(con):
    """Per konsesi: t0, luas, stok hutan awal tiap tahun, kehilangan tiap tahun."""
    meta = {}
    for kode, th_izin, hutan2000, luas in con.execute("""
            SELECT k.kode_wiup, k.tahun_izin, h.hutan_2000_ha, k.luas_poligon_ha
            FROM konsesi k JOIN hansen_ringkas h USING (kode_wiup)
            WHERE k.tahun_izin IS NOT NULL AND h.hutan_2000_ha IS NOT NULL
            ORDER BY k.kode_wiup"""):
        meta[kode] = {"t0": max(TAHUN_MIN, int(th_izin)), "tahun_izin": int(th_izin),
                      "hutan2000": float(hutan2000 or 0.0), "luas": float(luas or 0.0), "loss": {}}
    for kode, th, ha in con.execute(
            "SELECT kode_wiup, tahun, hilang_ha FROM hansen_tahunan WHERE tahun BETWEEN ? AND ?",
            (TAHUN_MIN, TAHUN_MAX)):
        if kode in meta:
            meta[kode]["loss"][int(th)] = float(ha or 0.0)
    # Stok hutan di AWAL tahun t = hutan2000 − kehilangan kumulatif s/d t−1; dijaga tak negatif.
    for m in meta.values():
        stok, kum = {}, 0.0
        for t in range(TAHUN_MIN, TAHUN_MAX + 1):
            stok[t] = max(0.0, m["hutan2000"] - kum)
            kum += m["loss"].get(t, 0.0)
        m["stok"] = stok
    return meta


def hitung(meta, kode_ikut, kurun_ikut):
    """(baris agregat per kurun, baris per konsesi × kurun)."""
    agregat, rinci = [], []
    for nama, a, b, catatan in KURUN:
        if nama not in kurun_ikut:
            continue
        n = 0
        thn = loss = hutan_thn = hutan_awal = luas_thn = 0.0
        for kode in kode_ikut:
            m = meta[kode]
            tahun = [t for t in (m["t0"] + r for r in range(a, b + 1)) if TAHUN_MIN <= t <= TAHUN_MAX]
            if not tahun:
                continue
            l_k = sum(m["loss"].get(t, 0.0) for t in tahun)
            h_k = sum(m["stok"][t] for t in tahun)          # hutan-tahun berisiko
            n += 1
            thn += len(tahun)
            loss += l_k
            hutan_thn += h_k
            hutan_awal += m["stok"][tahun[0]]
            luas_thn += m["luas"] * len(tahun)
            rinci.append((kode, nama, len(tahun), tahun[0], tahun[-1], round(m["stok"][tahun[0]], 4),
                          round(l_k, 4), round(l_k / h_k, 8) if h_k > 0 else None))
        if n == 0:
            continue
        agregat.append((nama, a, b, n, round(thn, 1), round(hutan_awal, 2), round(hutan_thn, 2), round(loss, 2),
                        round(loss / hutan_thn, 8) if hutan_thn > 0 else None,
                        round(luas_thn, 2), round(loss / luas_thn, 8) if luas_thn > 0 else None, catatan))
    return agregat, rinci


def deret_tahunan(meta, kode_ikut):
    """Deret per umur relatif r (−24..+24) — kurva event-study, lengkap dengan n."""
    per: dict[int, list] = {}
    for kode in kode_ikut:
        m = meta[kode]
        for t in range(TAHUN_MIN, TAHUN_MAX + 1):
            r = t - m["t0"]
            if not (-24 <= r <= 24):
                continue
            d = per.setdefault(r, [0, 0.0, 0.0, 0.0])
            d[0] += 1
            d[1] += m["loss"].get(t, 0.0)
            d[2] += m["stok"][t]
            d[3] += m["luas"]
    return [(r, d[0], round(d[1], 2), round(d[2], 2),
             round(d[1] / d[2], 8) if d[2] > 0 else None,
             round(d[1] / d[3], 8) if d[3] > 0 else None) for r, d in sorted(per.items())]


DDL = """
DROP TABLE IF EXISTS umur_izin_kurun;
DROP TABLE IF EXISTS umur_izin_konsesi;
DROP TABLE IF EXISTS umur_izin_tahunan;
CREATE TABLE umur_izin_kurun (
    rancangan TEXT NOT NULL, kurun TEXT NOT NULL,
    umur_awal INTEGER NOT NULL, umur_akhir INTEGER NOT NULL,
    n_konsesi INTEGER NOT NULL, konsesi_tahun REAL NOT NULL,
    hutan_awal_ha REAL NOT NULL, hutan_tahun_ha REAL NOT NULL,
    hilang_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
    konsesi_luas_tahun_ha REAL NOT NULL, laju_luas_per_tahun REAL,
    catatan TEXT NOT NULL,
    PRIMARY KEY (rancangan, kurun));
CREATE TABLE umur_izin_konsesi (
    rancangan TEXT NOT NULL, kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), kurun TEXT NOT NULL,
    tahun_teramati INTEGER NOT NULL, tahun_awal INTEGER NOT NULL,
    tahun_akhir INTEGER NOT NULL, hutan_awal_ha REAL NOT NULL,
    hilang_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
    PRIMARY KEY (rancangan, kode_wiup, kurun));
CREATE TABLE umur_izin_tahunan (
    rancangan TEXT NOT NULL, umur_relatif INTEGER NOT NULL,
    n_konsesi INTEGER NOT NULL, hilang_ha REAL NOT NULL,
    hutan_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
    laju_luas_per_tahun REAL,
    PRIMARY KEY (rancangan, umur_relatif));
"""

_RANCANGAN = ("A_seimbang (konsesi ber-tahun_izin 2009–2015, kohort sama di tiap kurun) atau B_semua "
              "(semua konsesi ber-tahun_izin, dinormalkan paparan). Selisih keduanya itu sendiri temuan.")
_METODE = ("t0 = max(tahun_izin, 2001); umur relatif r = tahun − t0. laju_bahaya = Σ hilang_ha / Σ(stok hutan "
           "awal tahun) dengan stok(t) = hutan_2000_ha − Σ hilang 2001..t−1 (identitas Hansen, dijaga >= 0). "
           "Pendamping laju_luas = Σ hilang_ha / Σ(luas_poligon_ha × tahun teramati). Kurun: -8..-1, 0..4, "
           "5..9, 10..14, 15..19, 20..24; A_seimbang hanya tiga kurun pertama. Jendela 2001–2024. "
           "Reproduksi: jalankan skrip ini setelah 01 & 02.")


def tulis_meta_semua(con) -> None:
    lis = LISENSI["hansen"]
    tulis_meta(con, "umur_izin_kurun",
               deskripsi="Tren deforestasi per KURUN UMUR IZIN (5 tahun), dua rancangan (A_seimbang n=260 & "
                         "B_semua). Menampilkan laju bahaya (bagian hutan berdiri yang hilang per tahun) — "
                         "sebanding antar kurun & antar konsesi. Dipakai: tab 'umur izin' halaman guna lahan; "
                         "Bab hasil pertanyaan (a). WAJIB tampil bersama n_konsesi & catatan.",
               sumber=SUMBER, metode=_METODE, skrip=SKRIP, lisensi=lis, kolom=[
                   ("rancangan", _RANCANGAN, "-", SUMBER),
                   ("kurun", "Label kurun umur, mis. '-8..-1' atau '0..4'.", "-", SUMBER),
                   ("umur_awal", "Umur relatif awal kurun (negatif = sebelum izin).", "tahun − t0", SUMBER),
                   ("umur_akhir", "Umur relatif akhir kurun (inklusif).", "tahun − t0", SUMBER),
                   ("n_konsesi", "Banyak konsesi yang menyumbang ke kurun ini. WAJIB ditampilkan bersama "
                    "angkanya — kurun umur tua n-nya sangat tipis.", "COUNT konsesi dgn >= 1 tahun teramati", SUMBER),
                   ("konsesi_tahun", "Total paparan: jumlah (konsesi × tahun) teramati di kurun ini.",
                    "Σ tahun teramati per konsesi", SUMBER),
                   ("hutan_awal_ha", "Jumlah stok hutan di tahun PERTAMA kurun tiap konsesi — pelengkap "
                    "bacaan, bukan penyebut.", "Σ stok(tahun awal kurun)", SUMBER),
                   ("hutan_tahun_ha", "Penyebut laju bahaya: total hektar-tahun hutan yang berisiko.",
                    "Σ per konsesi per tahun stok hutan awal tahun", SUMBER),
                   ("hilang_ha", "Total kehilangan tutupan pohon Hansen di kurun ini (ha).",
                    "Σ hansen_tahunan.hilang_ha pada tahun-tahun kurun", SUMBER),
                   ("laju_bahaya_per_tahun", "Bagian hutan yang MASIH BERDIRI yang hilang per tahun (pecahan; "
                    "×100 = %/th). Ukuran utama.", "hilang_ha / hutan_tahun_ha", SUMBER),
                   ("konsesi_luas_tahun_ha", "Penyebut pendamping: total hektar-tahun luas poligon.",
                    "Σ luas_poligon_ha × tahun teramati", SUMBER),
                   ("laju_luas_per_tahun", "Pendamping: kehilangan per hektar luas konsesi per tahun (pecahan). "
                    "Kurang adil (konsesi minim hutan tak bisa mencetak angka tinggi) tapi lazim di literatur.",
                    "hilang_ha / konsesi_luas_tahun_ha", SUMBER),
                   ("catatan", "Peringatan yang WAJIB ikut tampil bersama angkanya (mis. n terlalu tipis).",
                    "-", SUMBER),
               ])
    tulis_meta(con, "umur_izin_konsesi",
               deskripsi="Rincian per konsesi × kurun umur — bahan telusur balik & uji ketahanan "
                         "umur_izin_kurun (agregatnya = Σ baris ini).",
               sumber=SUMBER, metode=_METODE + " Tabel ini = sebelum diagregat.", skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("rancangan", _RANCANGAN, "-", SUMBER),
                   ("kode_wiup", "Kode WIUP konsesi.", "-", SUMBER),
                   ("kurun", "Label kurun umur.", "-", SUMBER),
                   ("tahun_teramati", "Berapa tahun kurun ini benar-benar teramati untuk konsesi ini (bisa < "
                    "lebar kurun bila terpotong 2001/2024).", "COUNT tahun kurun ∩ [2001, 2024]", SUMBER),
                   ("tahun_awal", "Tahun kalender pertama kurun ini.", "t0 + umur_awal, diklem ke 2001", SUMBER),
                   ("tahun_akhir", "Tahun kalender terakhir kurun ini.", "t0 + umur_akhir, diklem ke 2024", SUMBER),
                   ("hutan_awal_ha", "Stok hutan berdiri di awal kurun (ha).",
                    "hutan_2000_ha − kehilangan kumulatif s/d tahun sebelumnya", SUMBER),
                   ("hilang_ha", "Kehilangan konsesi ini di kurun ini (ha).", "Σ hansen_tahunan.hilang_ha", SUMBER),
                   ("laju_bahaya_per_tahun", "Laju bahaya konsesi ini di kurun ini (pecahan).",
                    "hilang_ha / Σ stok hutan awal tiap tahun", SUMBER),
               ])
    tulis_meta(con, "umur_izin_tahunan",
               deskripsi="Kurva event-study per umur relatif (−24..+24) LENGKAP dengan n tiap titik — n yang "
                         "berubah itulah bias komposisi, jadi sengaja ditampilkan. Dipakai: grafik umur izin.",
               sumber=SUMBER, metode=_METODE + " Tabel ini = agregasi per r = tahun − t0.", skrip=SKRIP,
               lisensi=lis, kolom=[
                   ("rancangan", _RANCANGAN, "-", SUMBER),
                   ("umur_relatif", "Tahun relatif terhadap tahun izin; 0 = tahun izin, negatif = sebelumnya.",
                    "tahun kalender − t0", SUMBER),
                   ("n_konsesi", "Banyak konsesi teramati pada umur relatif itu. Perubahannya antar umur "
                    "ADALAH bias komposisi.", "COUNT konsesi", SUMBER),
                   ("hilang_ha", "Total kehilangan pada umur relatif itu (ha).", "Σ hansen_tahunan.hilang_ha", SUMBER),
                   ("hutan_ha", "Total stok hutan berdiri pada umur relatif itu — penyebut laju bahaya.",
                    "Σ stok hutan awal tahun", SUMBER),
                   ("laju_bahaya_per_tahun", "Bagian hutan berdiri yang hilang pada umur relatif itu (pecahan).",
                    "hilang_ha / hutan_ha", SUMBER),
                   ("laju_luas_per_tahun", "Pendamping: kehilangan per hektar luas konsesi (pecahan).",
                    "hilang_ha / Σ luas_poligon_ha", SUMBER),
               ])


def main(argv=None) -> int:
    ap = argparser(__doc__)
    a = ap.parse_args(argv)
    t_mulai = time.time()
    con = buka(a.db)
    wajib_tabel(con, "konsesi", "hansen_ringkas", "hansen_tahunan")
    meta = muat(con)
    if not meta:
        gagal("tidak ada konsesi ber-tahun_izin & ber-hutan_2000_ha")
    semua = sorted(meta)
    seimbang = sorted(k for k, m in meta.items() if SEIMBANG_MIN <= m["tahun_izin"] <= SEIMBANG_MAX)
    print(f"{len(semua)} konsesi ber-tahun_izin & ber-hutan_2000; kohort seimbang "
          f"(tahun_izin {SEIMBANG_MIN}-{SEIMBANG_MAX}) = {len(seimbang)}")

    agr, rinci, deret = [], [], []
    for nama, kode, kurun in (("A_seimbang", seimbang, KURUN_SEIMBANG), ("B_semua", semua, {k[0] for k in KURUN})):
        ag, rn = hitung(meta, kode, kurun)
        agr += [(nama, *r) for r in ag]
        rinci += [(nama, *r) for r in rn]
        deret += [(nama, *r) for r in deret_tahunan(meta, kode)]

    con.executescript(DDL)
    con.executemany("INSERT INTO umur_izin_kurun VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", agr)
    con.executemany("INSERT INTO umur_izin_konsesi VALUES (?,?,?,?,?,?,?,?,?)", rinci)
    con.executemany("INSERT INTO umur_izin_tahunan VALUES (?,?,?,?,?,?,?)", deret)
    tulis_meta_semua(con)
    tandai_selesai(con, "07_umur_izin", himpunan=a.himpunan, n_semua=len(semua), n_seimbang=len(seimbang))

    print(f"\n{'rancangan':<12}{'kurun':<9}{'n':>5}{'hilang ha':>12}{'laju bahaya/th':>16}{'laju luas/th':>14}")
    for r in agr:
        print(f"{r[0]:<12}{r[1]:<9}{r[4]:>5}{r[8]:>12,.0f}{(r[9] or 0) * 100:>15.3f}%{(r[11] or 0) * 100:>13.3f}%")
    con.close()
    print(f"Selesai {SKRIP} ({time.time() - t_mulai:.1f} s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
