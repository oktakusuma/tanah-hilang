#!/usr/bin/env python3
# STATUS  : ARSIP — tabel umur_izin_* di kalimantan.db v2
# CATATAN : pengganti: pipeline/07_umur_izin.py (algoritma sama, TAHUN_MAX 2024, kolom loss_ha -> hilang_ha)
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""Tren deforestasi menurut UMUR IZIN — kurun 5/10 tahun, dinormalkan.

MASALAH YANG DIJAWAB (permintaan istri, 31 Agu 2026): "cek tren deforestasi
berdasarkan umur izin per 10 tahun ... normalisasi dengan luasan atau apa
gitu biar comparable".

SUMBU UMUR. t0 = max(iup_year, 2001). Lantai 2001 dipakai karena itu awal
rekaman Hansen — umur yang lebih tua dari itu memang tak teramati, bukan
belum diolah. Umur relatif r = tahun_kalender - t0; r < 0 = sebelum izin.

BATAS DATA YANG MENENTUKAN — SEBUTKAN DI SETIAP LAPORAN. Dari 818 konsesi
ber-iup_year, yang punya pengamatan pasca-izin >=10 tahun hanya 313; >=15
tahun 161; >=20 tahun **14**; >=25 tahun 7. Kurun "20 tahun pertama" TIDAK
layak disimpulkan dan kurun 30 tahun MUSTAHIL dengan Hansen. Kolom
`n_konsesi` dan `catatan` di tabel keluaran memuat peringatan itu supaya
angkanya tak pernah tampil tanpa bintangnya.

NORMALISASI — kenapa BUKAN luas konsesi. Membagi dengan luas poligon tidak
adil: konsesi yang 90% isinya bukan hutan tak akan pernah bisa mencetak
angka tinggi, walau menghabiskan seluruh hutannya. Penyebut yang benar
adalah STOK YANG BERISIKO:

    laju bahaya = SUM(kehilangan dalam kurun)
                  ------------------------------------------------
                  SUM(hutan berdiri di AWAL tiap tahun teramati)

Satuannya "bagian hutan tersisa yang hilang per tahun", sebanding lurus
antar kurun umur DAN antar konsesi. Pembagi berbasis tahun membuat kurun
yang belum penuh tetap sebanding. Versi sederhana (dibagi luas konsesi)
tetap ikut dilaporkan sebagai pendamping supaya bisa diadu dengan literatur
yang memakai cara itu.

STOK HUTAN memakai identitas Hansen sendiri — hutan(t) = forest_2000_ha
dikurangi kehilangan kumulatif 2001..t — BUKAN stok MapBiomas. Alasannya
hierarki sumber di CLAUDE.md: kehilangan tutupan hutan = Hansen. Mencampur
penyebut MapBiomas dengan pembilang Hansen akan membuat selisih alat ukur
menyamar jadi selisih umur.

DUA RANCANGAN, dijalankan keduanya, dan selisihnya SENDIRI adalah temuan.

  A_seimbang  Hanya konsesi ber-t0 2009-2015 (n=260): semuanya punya 8 tahun
              penuh SEBELUM izin dan 10 tahun penuh SESUDAH. Kumpulan
              konsesinya SAMA PERSIS di tiap kurun, jadi bebas bias
              komposisi. Harganya n mengecil dan kohortnya condong ke izin
              lama.
  B_semua     Seluruh 818 konsesi; tiap konsesi menyumbang ke kurun mana pun
              yang teramati. Sebanding karena sudah dibagi paparan, TAPI
              komposisinya berubah antar kurun — karena itu n dan paparan
              wajib ikut dilaporkan.

Kalau A dan B sepakat, temuannya kokoh. Kalau berbeda, yang bicara adalah
komposisi, bukan umur.

BIAS KOMPOSISI ITU NYATA, bukan kekhawatiran teoretis: tabel lama
`laju_izin_eventstudy` punya n = 1.366 di rel_year -10, 1.570 di 0, 520 di
+10, dan 88 di +16. Rata-ratanya dihitung atas kumpulan konsesi yang
berbeda-beda.

    python3 scripts/build_umur_izin.py
"""
from __future__ import annotations

import argparse
import sqlite3

# 2024, bukan 2025: jendela tesis dikunci 2001-2024 (proposal v0.3.2 —
# sebanding dgn MapBiomas yang berhenti 2024; keputusan igoen 1 Sep 2026).
TAHUN_MIN, TAHUN_MAX = 2001, 2024

# Kurun umur mengikuti kenyataan data, bukan angan-angan. Dekade pertama
# dipecah dua karena di situlah n paling tebal; 20+ hanya catatan kaki.
KURUN = [
    ("-8..-1", -8, -1, "Delapan tahun SEBELUM izin — garis dasar pembanding."),
    ("0..4",    0,  4, "Lima tahun pertama izin."),
    ("5..9",    5,  9, "Tahun ke-6 sampai ke-10."),
    ("10..14", 10, 14, "Dekade kedua, paruh pertama — n menipis."),
    ("15..19", 15, 19, "Dekade kedua, paruh kedua — n sangat tipis."),
    ("20..24", 20, 24, "CATATAN KAKI SAJA: hanya belasan konsesi yang seumur "
                       "ini teramati; JANGAN disimpulkan."),
]

# Kohort seimbang: 8 tahun penuh sebelum izin (t0-8 >= 2001) dan 10 tahun
# penuh sesudah (t0+9 <= 2025).
SEIMBANG_MIN, SEIMBANG_MAX = 2009, 2015  # t0+9 <= 2024 tetap terpenuhi
KURUN_SEIMBANG = {"-8..-1", "0..4", "5..9"}

_SKRIP = "scripts/build_umur_izin.py"

META_DDL = """
    CREATE TABLE IF NOT EXISTS analysis_meta (
        nama_tabel TEXT PRIMARY KEY, deskripsi TEXT, sumber TEXT, metode TEXT,
        script TEXT, status TEXT NOT NULL DEFAULT 'AKTIF'
        CHECK (status IN ('AKTIF','ARSIP','PROYEKSI')));
    CREATE TABLE IF NOT EXISTS column_meta (
        nama_tabel TEXT, nama_kolom TEXT, deskripsi TEXT, rumus TEXT, sumber TEXT,
        PRIMARY KEY (nama_tabel, nama_kolom));
"""

_SUMBER = ("Hansen GFC v1.13 (ambang kanopi 30) lewat wiup_loss_yearly + "
           "wiup_loss.forest_2000_ha; tahun izin dari wiup_geoportal.iup_year")


def muat(con):
    """Per konsesi: t0, luas, stok hutan awal tiap tahun, kehilangan tiap tahun."""
    con.row_factory = sqlite3.Row
    meta = {}
    for r in con.execute("""
            SELECT g.kode_wiup, g.iup_year, l.forest_2000_ha, l.polygon_area_ha
            FROM wiup_geoportal g JOIN wiup_loss l USING (kode_wiup)
            WHERE g.iup_year IS NOT NULL AND l.forest_2000_ha IS NOT NULL"""):
        meta[r["kode_wiup"]] = {
            "t0": max(TAHUN_MIN, int(r["iup_year"])),
            "iup_year": int(r["iup_year"]),
            "hutan2000": float(r["forest_2000_ha"] or 0.0),
            "luas": float(r["polygon_area_ha"] or 0.0),
            "loss": {},
        }
    for kode, th, ha in con.execute(
            "SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly "
            "WHERE year BETWEEN ? AND ?", (TAHUN_MIN, TAHUN_MAX)):
        if kode in meta:
            meta[kode]["loss"][int(th)] = float(ha or 0.0)

    # Stok hutan di AWAL tahun t = hutan2000 - kehilangan kumulatif s/d t-1.
    # Identitas eksak di dalam Hansen; dijaga tak negatif (pembulatan raster).
    for m in meta.values():
        stok, kum = {}, 0.0
        for t in range(TAHUN_MIN, TAHUN_MAX + 1):
            stok[t] = max(0.0, m["hutan2000"] - kum)
            kum += m["loss"].get(t, 0.0)
        m["stok"] = stok
    return meta


def hitung(meta, kode_ikut, kurun_ikut):
    """(baris agregat per kurun, baris per konsesi x kurun)."""
    agregat, rinci = [], []
    for nama, a, b, catatan in KURUN:
        if nama not in kurun_ikut:
            continue
        n = 0
        thn = loss = hutan_thn = hutan_awal = luas_thn = 0.0
        for kode in kode_ikut:
            m = meta[kode]
            tahun = [m["t0"] + r for r in range(a, b + 1)]
            tahun = [t for t in tahun if TAHUN_MIN <= t <= TAHUN_MAX]
            if not tahun:
                continue
            l_k = sum(m["loss"].get(t, 0.0) for t in tahun)
            h_k = sum(m["stok"][t] for t in tahun)      # hutan-tahun berisiko
            n += 1
            thn += len(tahun)
            loss += l_k
            hutan_thn += h_k
            hutan_awal += m["stok"][tahun[0]]
            luas_thn += m["luas"] * len(tahun)
            rinci.append((kode, nama, len(tahun), tahun[0], tahun[-1],
                          round(m["stok"][tahun[0]], 4), round(l_k, 4),
                          round(l_k / h_k, 8) if h_k > 0 else None))
        if n == 0:
            continue
        agregat.append((
            nama, a, b, n, round(thn, 1), round(hutan_awal, 2),
            round(hutan_thn, 2), round(loss, 2),
            round(loss / hutan_thn, 8) if hutan_thn > 0 else None,
            round(luas_thn, 2),
            round(loss / luas_thn, 8) if luas_thn > 0 else None,
            catatan))
    return agregat, rinci


def deret_tahunan(meta, kode_ikut):
    """Deret per umur relatif (r) — kurva event-study, LENGKAP dengan n."""
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
             round(d[1] / d[3], 8) if d[3] > 0 else None)
            for r, d in sorted(per.items())]


DDL = """
DROP TABLE IF EXISTS umur_izin_kurun;
DROP TABLE IF EXISTS umur_izin_konsesi;
DROP TABLE IF EXISTS umur_izin_tahunan;
CREATE TABLE umur_izin_kurun (
    rancangan TEXT NOT NULL, kurun TEXT NOT NULL,
    umur_awal INTEGER NOT NULL, umur_akhir INTEGER NOT NULL,
    n_konsesi INTEGER NOT NULL, konsesi_tahun REAL NOT NULL,
    hutan_awal_ha REAL NOT NULL, hutan_tahun_ha REAL NOT NULL,
    loss_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
    konsesi_luas_tahun_ha REAL NOT NULL, laju_luas_per_tahun REAL,
    catatan TEXT NOT NULL,
    PRIMARY KEY (rancangan, kurun));
CREATE TABLE umur_izin_konsesi (
    rancangan TEXT NOT NULL, kode_wiup TEXT NOT NULL, kurun TEXT NOT NULL,
    tahun_teramati INTEGER NOT NULL, tahun_awal INTEGER NOT NULL,
    tahun_akhir INTEGER NOT NULL, hutan_awal_ha REAL NOT NULL,
    loss_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
    PRIMARY KEY (rancangan, kode_wiup, kurun));
CREATE TABLE umur_izin_tahunan (
    rancangan TEXT NOT NULL, umur_relatif INTEGER NOT NULL,
    n_konsesi INTEGER NOT NULL, loss_ha REAL NOT NULL,
    hutan_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
    laju_luas_per_tahun REAL,
    PRIMARY KEY (rancangan, umur_relatif));
"""

META = [
    ("umur_izin_kurun",
     "Tren deforestasi per KURUN UMUR IZIN (t0 = max(iup_year, 2001)), dua "
     "rancangan: A_seimbang (n=260, kohort sama di tiap kurun) dan B_semua "
     "(818, dinormalkan paparan). Kolom catatan memuat peringatan n tipis.",
     _SUMBER,
     "laju_bahaya = SUM(loss) / SUM(stok hutan awal tahun) — stok = "
     "forest_2000_ha dikurangi kehilangan kumulatif (identitas Hansen). "
     "Pendamping laju_luas = SUM(loss)/SUM(luas poligon x tahun).",
     _SKRIP, "AKTIF"),
    ("umur_izin_konsesi",
     "Rincian per konsesi x kurun — bahan telusur balik dan uji ketahanan.",
     _SUMBER, "Sama seperti umur_izin_kurun, sebelum diagregat.", _SKRIP, "AKTIF"),
    ("umur_izin_tahunan",
     "Kurva event-study per umur relatif (-24..+24) LENGKAP dengan n tiap "
     "titik — n yang berubah itulah bias komposisi, jadi sengaja ditampilkan.",
     _SUMBER, "Agregasi per r = tahun - t0.", _SKRIP, "AKTIF"),
]

KOLOM = [
    ("umur_izin_kurun", "laju_bahaya_per_tahun",
     "Bagian hutan yang MASIH BERDIRI yang hilang per tahun. Ukuran utama — "
     "sebanding antar kurun umur dan antar konsesi.",
     "SUM(loss_ha) / SUM(stok hutan awal tahun)", _SUMBER),
    ("umur_izin_kurun", "laju_luas_per_tahun",
     "Pendamping: kehilangan per hektar luas konsesi per tahun. Kurang adil "
     "(konsesi minim hutan tak bisa mencetak angka tinggi) tapi lazim di "
     "literatur, jadi disediakan untuk pembanding.",
     "SUM(loss_ha) / SUM(luas poligon x tahun)", _SUMBER),
    ("umur_izin_kurun", "hutan_tahun_ha",
     "Penyebut laju bahaya: total hektar-tahun hutan yang berisiko.",
     "SUM per konsesi per tahun dari stok hutan awal tahun", _SUMBER),
    ("umur_izin_kurun", "n_konsesi",
     "Banyak konsesi yang menyumbang ke kurun ini. WAJIB ditampilkan bersama "
     "angkanya — kurun umur tua n-nya sangat tipis.", "-", _SUMBER),
    ("umur_izin_tahunan", "n_konsesi",
     "Banyak konsesi teramati pada umur relatif itu. Perubahannya antar umur "
     "ADALAH bias komposisi.", "-", _SUMBER),

    # ── sisa kolom (Konvensi #4/#5: cakupan dua arah, diikat verify_invariants) ──
    ("umur_izin_kurun", "rancangan",
     "A_seimbang (kohort sama di tiap kurun) atau B_semua (semua konsesi, "
     "dinormalkan paparan). Selisih keduanya itu sendiri temuan.", "-", _SUMBER),
    ("umur_izin_kurun", "kurun", "Label kurun umur, mis. '-8..-1' atau '0..4'.",
     "-", _SUMBER),
    ("umur_izin_kurun", "umur_awal", "Umur relatif awal kurun (negatif = sebelum izin).",
     "tahun - t0", _SUMBER),
    ("umur_izin_kurun", "umur_akhir", "Umur relatif akhir kurun (inklusif).",
     "tahun - t0", _SUMBER),
    ("umur_izin_kurun", "konsesi_tahun", "Total paparan: jumlah (konsesi x tahun) teramati "
     "di kurun ini.", "SUM(tahun teramati per konsesi)", _SUMBER),
    ("umur_izin_kurun", "hutan_awal_ha", "Jumlah stok hutan di tahun PERTAMA kurun tiap "
     "konsesi — pelengkap bacaan, bukan penyebut.",
     "SUM(stok hutan pada tahun awal kurun)", _SUMBER),
    ("umur_izin_kurun", "loss_ha", "Total kehilangan tutupan pohon Hansen di kurun ini.",
     "SUM(wiup_loss_yearly.loss_ha) pada tahun-tahun kurun", _SUMBER),
    ("umur_izin_kurun", "konsesi_luas_tahun_ha",
     "Penyebut pendamping: total hektar-tahun luas poligon.",
     "SUM(polygon_area_ha x tahun teramati)", _SUMBER),
    ("umur_izin_kurun", "catatan", "Peringatan yang WAJIB ikut tampil bersama angkanya "
     "(mis. n terlalu tipis untuk disimpulkan).", "-", _SUMBER),
    ("umur_izin_konsesi", "rancangan", "Rancangan kohort — lihat umur_izin_kurun.rancangan.",
     "-", _SUMBER),
    ("umur_izin_konsesi", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER),
    ("umur_izin_konsesi", "kurun", "Label kurun umur.", "-", _SUMBER),
    ("umur_izin_konsesi", "tahun_teramati", "Berapa tahun kurun ini benar-benar teramati "
     "untuk konsesi ini (bisa < lebar kurun bila terpotong 2001/2025).", "-", _SUMBER),
    ("umur_izin_konsesi", "tahun_awal", "Tahun kalender pertama kurun ini.",
     "t0 + umur_awal, diklem ke 2001", _SUMBER),
    ("umur_izin_konsesi", "tahun_akhir", "Tahun kalender terakhir kurun ini.",
     "t0 + umur_akhir, diklem ke 2025", _SUMBER),
    ("umur_izin_konsesi", "hutan_awal_ha", "Stok hutan berdiri di awal kurun.",
     "forest_2000_ha - kehilangan kumulatif s/d tahun sebelumnya", _SUMBER),
    ("umur_izin_konsesi", "loss_ha", "Kehilangan konsesi ini di kurun ini.", "-", _SUMBER),
    ("umur_izin_konsesi", "laju_bahaya_per_tahun", "Laju bahaya konsesi ini di kurun ini.",
     "loss_ha / SUM(stok hutan awal tiap tahun)", _SUMBER),
    ("umur_izin_tahunan", "rancangan", "Rancangan kohort — lihat umur_izin_kurun.rancangan.",
     "-", _SUMBER),
    ("umur_izin_tahunan", "umur_relatif", "Tahun relatif terhadap tahun izin; 0 = tahun "
     "izin, negatif = sebelumnya.", "tahun kalender - t0", _SUMBER),
    ("umur_izin_tahunan", "loss_ha", "Total kehilangan pada umur relatif itu.", "-", _SUMBER),
    ("umur_izin_tahunan", "hutan_ha", "Total stok hutan berdiri pada umur relatif itu — "
     "penyebut laju bahaya.", "SUM(stok hutan awal tahun)", _SUMBER),
    ("umur_izin_tahunan", "laju_bahaya_per_tahun", "Bagian hutan berdiri yang hilang pada "
     "umur relatif itu.", "loss_ha / hutan_ha", _SUMBER),
    ("umur_izin_tahunan", "laju_luas_per_tahun", "Pendamping: kehilangan per hektar luas "
     "konsesi.", "loss_ha / SUM(polygon_area_ha)", _SUMBER),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/kalimantan.db")
    a = ap.parse_args(argv)

    con = sqlite3.connect(a.db)
    meta = muat(con)
    semua = sorted(meta)
    seimbang = sorted(k for k, m in meta.items()
                      if SEIMBANG_MIN <= m["iup_year"] <= SEIMBANG_MAX)
    print(f"{len(semua)} konsesi ber-iup_year & ber-hutan2000; "
          f"kohort seimbang (iup_year {SEIMBANG_MIN}-{SEIMBANG_MAX}) = {len(seimbang)}")

    agr, rinci, deret = [], [], []
    for nama, kode, kurun in (
            ("A_seimbang", seimbang, KURUN_SEIMBANG),
            ("B_semua", semua, {k[0] for k in KURUN})):
        ag, rn = hitung(meta, kode, kurun)
        agr += [(nama, *r) for r in ag]
        rinci += [(nama, *r) for r in rn]
        deret += [(nama, *r) for r in deret_tahunan(meta, kode)]

    con.executescript(META_DDL)
    con.executescript(DDL)
    con.executemany("INSERT INTO umur_izin_kurun VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", agr)
    con.executemany("INSERT INTO umur_izin_konsesi VALUES (?,?,?,?,?,?,?,?,?)", rinci)
    con.executemany("INSERT INTO umur_izin_tahunan VALUES (?,?,?,?,?,?,?)", deret)
    con.executemany("INSERT OR REPLACE INTO analysis_meta "
                    "(nama_tabel,deskripsi,sumber,metode,script,status) "
                    "VALUES (?,?,?,?,?,?)", META)
    con.executemany("INSERT OR REPLACE INTO column_meta "
                    "(nama_tabel,nama_kolom,deskripsi,rumus,sumber) VALUES (?,?,?,?,?)",
                    KOLOM)
    con.commit()

    print(f"\n{'rancangan':<12}{'kurun':<9}{'n':>5}{'loss ha':>12}"
          f"{'laju bahaya/th':>16}{'laju luas/th':>14}")
    for r in agr:
        print(f"{r[0]:<12}{r[1]:<9}{r[4]:>5}{r[8]:>12,.0f}"
              f"{(r[9] or 0) * 100:>15.3f}%{(r[11] or 0) * 100:>13.3f}%")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
