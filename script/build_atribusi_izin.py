#!/usr/bin/env python3
"""Bangun tabel ATRIBUSI IZIN AKTIF — jendela era Minerba 2009-2025 (reproducible).

Angka utama lama (1.603.251 ha, 2001-2025) menjawab pertanyaan SPASIAL: hutan
hilang di dalam batas konsesi. Tabel-tabel ini menjawab pertanyaan ATRIBUSI:
berapa yang hilang KETIKA izinnya benar-benar berlaku, di jendela era UU
Minerba (2009-2025).

BENTUK BARIS (unpivot, Fase G 15 Agu): satu baris per (konsesi, aturan) —
dulu aturan-jadi-kolom (mulai_b/c/d + loss_mulai_{b,c,d}_sampai_2025_ha).
Kosakata `aturan` diselaraskan dgn keluarga backtrack_* (eks kode huruf):

  TANPA_ATRIBUSI  (eks X0) semua loss 2009-2025, tanpa atribusi — pembanding/
                  plafon; mulai selalu 2009.
  INDIKASI        (eks B)  PERPANJANGAN aktif sepanjang jendela (kegiatan sudah
                  berjalan sebelum SK perpanjangannya terbit — itulah makna
                  perpanjangan); IZIN_PERTAMA/TAK_DINILAI sejak max(2009, iup_year).
  POLOS           (eks D)  batas bawah: SEMUA sejak max(2009, iup_year) —
                  MENYANGKAL makna perpanjangan; pembanding paling konservatif.

Aturan C / PERKIRAAN (perpanjangan sejak taksiran tahun izin asal iup_year +
durasi_sk − 20) DIARSIPKAN 15 Agu 2026 dan SETOP DITULIS di sini: dgn cara baca
aditif yang benar (perpanjangan MENAMBAH waktu setelah izin asal habis)
taksirannya selalu < 2009 → terklem → C ≡ INDIKASI. Data C lama (kolom
mulai_c / loss_mulai_c_sampai_2025_ha) bisa diambil dari riwayat git
(lihat DECISIONS.md 15 Agu 2026).

Kasus tepi (terkunci): konsesi tanpa iup_year non-perpanjangan KELUAR kohort
(mulai NULL + loss NULL, baris tetap ditulis utk audit); perpanjangan iup_year
2026 MASUK via backtrack. Kohort INDIKASI = konsesi ber-mulai (saat ini 818
dari 825).

Tabel: atribusi_izin_aktif (per konsesi × aturan, 825×3) + _ringkas (1 baris/
aturan — sumber tunggal angka). Provenance/kamus kolom ditulis
build_periode_tables.py (jalan SETELAH skrip ini di process.sh, supaya filter
tabel-yang-ada meloloskan baris metanya).

Idempotent, stdlib saja.

    python3 scripts/build_atribusi_izin.py --db data/kalimantan.db
"""
from __future__ import annotations

import argparse
import sqlite3

JENDELA_MIN, JENDELA_MAX = 2009, 2025

ATURAN_LABEL = {
    "TANPA_ATRIBUSI": "Semua kehilangan era Minerba 2009-2025, tanpa atribusi izin (pembanding)",
    "INDIKASI": "Perpanjangan aktif sepanjang jendela; izin awal sejak tahun izinnya",
    "POLOS": "Batas bawah: semua sejak tahun SK terakhir (menyangkal makna perpanjangan)",
}
ATURAN_URUT = ("TANPA_ATRIBUSI", "INDIKASI", "POLOS")


def hitung_mulai(kelas, iup_year):
    """(mulai_indikasi, mulai_polos) — tahun pertama loss dihitung per aturan.

    None = keluar kohort (hanya terjadi utk non-perpanjangan tanpa iup_year).
    PERPANJANGAN tanpa iup_year (0 baris di data saat ini) jatuh ke 2009 di
    kedua aturan — didokumentasikan, bukan dibiarkan ambigu.
    (Aturan C/PERKIRAAN diarsipkan 15 Agu — lihat docstring modul.)
    """
    if kelas == "PERPANJANGAN":
        indikasi = JENDELA_MIN
        polos = max(JENDELA_MIN, iup_year) if iup_year is not None else JENDELA_MIN
        return indikasi, polos
    if iup_year is None:
        return None, None
    m = max(JENDELA_MIN, iup_year)
    return m, m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalimantan.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    # ── Baca konsesi (+ klasifikasi bila ada) ────────────────────────────────
    ada_klas = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='klasifikasi_izin'"
    ).fetchone() is not None
    if ada_klas:
        rows = con.execute(
            """SELECT g.kode_wiup, g.iup_year,
                      COALESCE(k.kelas, 'TAK_DINILAI'), k.bukti
               FROM wiup_geoportal g
               LEFT JOIN klasifikasi_izin k USING (kode_wiup)"""
        ).fetchall()
    else:
        print("PERINGATAN: klasifikasi_izin absen — semua konsesi TAK_DINILAI "
              "(INDIKASI jatuh = POLOS).")
        rows = [(kode, iy, "TAK_DINILAI", None) for kode, iy in
                con.execute("SELECT kode_wiup, iup_year FROM wiup_geoportal")]

    # ── Loss per (konsesi, tahun) di jendela ─────────────────────────────────
    loss_th: dict[str, dict[int, float]] = {}
    for kode, y, ha in con.execute(
        "SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly "
        "WHERE year BETWEEN ? AND ?", (JENDELA_MIN, JENDELA_MAX)):
        loss_th.setdefault(kode, {})[y] = ha or 0.0

    # ── Penyebut: hutan yang masih berdiri awal 2009 (identitas eksak) ───────
    f2000 = con.execute(
        "SELECT COALESCE(SUM(forest_2000_ha),0) FROM wiup_loss").fetchone()[0]
    loss0108 = con.execute(
        "SELECT COALESCE(SUM(loss_ha),0) FROM wiup_loss_yearly "
        "WHERE year BETWEEN 2001 AND 2008").fetchone()[0]
    hutan2009 = f2000 - loss0108

    # ── Hitung per (konsesi, aturan) + agregat ───────────────────────────────
    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif")
    # Bentuk BARIS (unpivot 15 Agu, Fase G): kolom mulai/loss tunggal berjangkar
    # kolom `aturan` di baris yang sama — bukan lagi tiga pasang kolom per
    # aturan. mulai NULL = keluar kohort → loss NULL (bukan 0: lossnya memang
    # tak terdefinisi, bukan nol temuan).
    con.execute(
        """CREATE TABLE atribusi_izin_aktif (
            kode_wiup TEXT, aturan TEXT, kelas TEXT, bukti TEXT, iup_year INTEGER,
            mulai INTEGER, loss_mulai_sampai_2025_ha REAL,
            PRIMARY KEY (kode_wiup, aturan))"""
    )
    tot = {a: 0.0 for a in ATURAN_URUT}
    n_kohort = {a: 0 for a in ATURAN_URUT}

    for kode, iy, kelas, bukti in rows:
        mi, mp = hitung_mulai(kelas, iy)
        th = loss_th.get(kode, {})
        for aturan, mulai in (("TANPA_ATRIBUSI", JENDELA_MIN),
                              ("INDIKASI", mi), ("POLOS", mp)):
            if mulai is None:
                loss = None
            else:
                # Agregat ringkas dijumlah dari nilai TAK-dibulatkan (identitas
                # eksak thd wiup_loss_yearly); baris per-konsesi disimpan
                # dibulatkan 2 desimal (tampilan) — selisihnya derau pembulatan.
                loss = sum(v for y, v in th.items() if y >= mulai)
                tot[aturan] += loss
                n_kohort[aturan] += 1
            con.execute("INSERT INTO atribusi_izin_aktif VALUES (?,?,?,?,?,?,?)",
                        (kode, aturan, kelas, bukti, iy, mulai,
                         None if loss is None else round(loss, 2)))

    # (_tahunan/_kelas dihapus cleanup 12 Agu r3; kolom aturan C setop ditulis
    # Fase G 15 Agu — bersihkan sisa build lama bila ada.)
    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif_tahunan")
    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif_kelas")

    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif_ringkas")
    # loss_mulai_aturan_sampai_2025_ha: jangkar jendela = kolom `aturan` baris
    # ini (TANPA_ATRIBUSI → 2009; INDIKASI/POLOS → mulai versi aturan per konsesi).
    con.execute(
        """CREATE TABLE atribusi_izin_aktif_ringkas (
            aturan TEXT PRIMARY KEY, label TEXT, loss_mulai_aturan_sampai_2025_ha REAL,
            pct_hutan2009 REAL, n_kohort INTEGER)"""
    )
    for aturan in ATURAN_URUT:
        pct = round(100.0 * tot[aturan] / hutan2009, 2) if hutan2009 > 0 else None
        con.execute("INSERT INTO atribusi_izin_aktif_ringkas VALUES (?,?,?,?,?)",
                    (aturan, ATURAN_LABEL[aturan], round(tot[aturan], 2),
                     pct, n_kohort[aturan]))

    con.commit()
    print(f"atribusi_izin_aktif: TANPA_ATRIBUSI={tot['TANPA_ATRIBUSI']:,.0f} "
          f"INDIKASI={tot['INDIKASI']:,.0f} POLOS={tot['POLOS']:,.0f} ha · "
          f"kohort INDIKASI {n_kohort['INDIKASI']} · hutan2009 {hutan2009:,.0f} ha")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
