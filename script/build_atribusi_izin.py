#!/usr/bin/env python3
"""Bangun tabel ATRIBUSI IZIN AKTIF — jendela era Minerba 2009-2025 (reproducible).

Angka utama lama (1.603.251 ha, 2001-2025) menjawab pertanyaan SPASIAL: hutan
hilang di dalam batas konsesi. Tabel-tabel ini menjawab pertanyaan ATRIBUSI:
berapa yang hilang KETIKA izinnya benar-benar berlaku, di jendela era UU
Minerba (2009-2025). Empat aturan `mulai` (tahun pertama loss dihitung):

  X0  semua loss 2009-2025, tanpa atribusi (pembanding).
  B   ANGKA UTAMA KANDIDAT: PERPANJANGAN aktif sepanjang jendela (kegiatan
      sudah berjalan sebelum SK perpanjangannya terbit — itulah makna
      perpanjangan); IZIN_PERTAMA/TAK_DINILAI sejak max(2009, iup_year).
  C   sensitivitas halus: perpanjangan sejak PERKIRAAN tahun izin asal =
      iup_year + durasi_sk - 20 (UU 4/2009 Ps. 47: pemberian pertama OP
      20 th); bukti KUAT (PKP2B/KK, eksis pra-2009) tetap 2009.
  D   batas bawah: SEMUA sejak max(2009, iup_year) — MENYANGKAL makna
      perpanjangan; bukan kandidat, hanya ujung rentang sensitivitas.

Kasus tepi (terkunci): konsesi tanpa iup_year non-perpanjangan KELUAR kohort
(mulai NULL, baris tetap ditulis utk audit); perpanjangan iup_year 2026 MASUK
via backtrack. Kohort B = konsesi ber-mulai_b (saat ini 818 dari 825).

Tabel: atribusi_izin_aktif (per konsesi, 825) + _ringkas (X0/B/C/D — sumber
tunggal angka). (_tahunan & _kelas dihapus — cleanup 12 Agu r3: UI tak lagi
memakainya; audit per-konsesi tetap utuh di tabel utama.)
Provenance/kamus kolom ditulis build_periode_tables.py (jalan SETELAH skrip
ini di process.sh, supaya filter tabel-yang-ada meloloskan baris metanya).

Idempotent, stdlib saja.

    python3 scripts/build_atribusi_izin.py --db data/kalimantan.db
"""
from __future__ import annotations

import argparse
import sqlite3

JENDELA_MIN, JENDELA_MAX = 2009, 2025
DURASI_PEMBERIAN_PERTAMA = 20  # UU 4/2009 Ps. 47: OP pemberian pertama 20 th

ATURAN_LABEL = {
    "X0": "Semua kehilangan era Minerba 2009-2025, tanpa atribusi izin",
    "B": "Perpanjangan aktif sepanjang jendela; izin awal sejak tahun izinnya",
    "C": "Perpanjangan sejak perkiraan tahun izin asal (Ps. 47: pemberian pertama 20 th)",
    "D": "Batas bawah: semua sejak tahun SK terakhir (menyangkal makna perpanjangan)",
}


def hitung_mulai(kelas, bukti, durasi_sk, iup_year):
    """(mulai_b, mulai_c, mulai_d) — tahun pertama loss dihitung per aturan.

    None = keluar kohort (hanya terjadi utk non-perpanjangan tanpa iup_year).
    PERPANJANGAN tanpa iup_year (0 baris di data saat ini) jatuh ke 2009 di
    ketiga aturan — didokumentasikan, bukan dibiarkan ambigu.
    """
    if kelas == "PERPANJANGAN":
        b = JENDELA_MIN
        if bukti == "KUAT":
            c = JENDELA_MIN
        elif (durasi_sk is not None and durasi_sk < DURASI_PEMBERIAN_PERTAMA
              and iup_year is not None):
            c = max(JENDELA_MIN, iup_year + durasi_sk - DURASI_PEMBERIAN_PERTAMA)
        else:
            c = JENDELA_MIN
        d = max(JENDELA_MIN, iup_year) if iup_year is not None else JENDELA_MIN
        return b, c, d
    if iup_year is None:
        return None, None, None
    m = max(JENDELA_MIN, iup_year)
    return m, m, m


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
                      COALESCE(k.kelas, 'TAK_DINILAI'), k.bukti, k.durasi_sk
               FROM wiup_geoportal g
               LEFT JOIN klasifikasi_izin k USING (kode_wiup)"""
        ).fetchall()
    else:
        print("PERINGATAN: klasifikasi_izin absen — semua konsesi TAK_DINILAI (B jatuh = D).")
        rows = [(kode, iy, "TAK_DINILAI", None, None) for kode, iy in
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

    # ── Hitung per konsesi + agregat ─────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif")
    # Nama kolom loss menyebut jendelanya (rename 15 Agu, konvensi DECISIONS
    # 13 Agu): X0 = jendela tetap 2009-2025 → loss_2009_2025_ha; B/C/D
    # berjangkar kolom mulai_b/c/d di baris yang sama → loss_mulai_{b,c,d}_
    # sampai_2025_ha (eks loss_x0/b/c/d_ha — kode aturan telanjang tak
    # memberi tahu jendela apa pun).
    con.execute(
        """CREATE TABLE atribusi_izin_aktif (
            kode_wiup TEXT PRIMARY KEY, kelas TEXT, bukti TEXT, iup_year INTEGER,
            mulai_b INTEGER, mulai_c INTEGER, mulai_d INTEGER,
            loss_2009_2025_ha REAL, loss_mulai_b_sampai_2025_ha REAL,
            loss_mulai_c_sampai_2025_ha REAL, loss_mulai_d_sampai_2025_ha REAL)"""
    )
    tot = {"X0": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
    n_kohort = {"X0": 0, "B": 0, "C": 0, "D": 0}

    for kode, iy, kelas, bukti, durasi in rows:
        mb, mc, md = hitung_mulai(kelas, bukti, durasi, iy)
        th = loss_th.get(kode, {})
        x0 = sum(th.values())
        lb = sum(v for y, v in th.items() if mb is not None and y >= mb)
        lc = sum(v for y, v in th.items() if mc is not None and y >= mc)
        ld = sum(v for y, v in th.items() if md is not None and y >= md)
        con.execute("INSERT INTO atribusi_izin_aktif VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (kode, kelas, bukti, iy, mb, mc, md,
                     round(x0, 2), round(lb, 2), round(lc, 2), round(ld, 2)))
        tot["X0"] += x0; tot["B"] += lb; tot["C"] += lc; tot["D"] += ld
        n_kohort["X0"] += 1
        if mb is not None: n_kohort["B"] += 1
        if mc is not None: n_kohort["C"] += 1
        if md is not None: n_kohort["D"] += 1

    # (_tahunan dihapus — cleanup 12 Agu r3.)
    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif_tahunan")

    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif_ringkas")
    # loss_mulai_aturan_sampai_2025_ha (eks loss_ha): jangkar jendela = kolom
    # `aturan` baris ini (X0 → 2009; B/C/D → mulai versi aturan per konsesi).
    con.execute(
        """CREATE TABLE atribusi_izin_aktif_ringkas (
            aturan TEXT PRIMARY KEY, label TEXT, loss_mulai_aturan_sampai_2025_ha REAL,
            pct_hutan2009 REAL, n_kohort INTEGER)"""
    )
    for aturan in ("X0", "B", "C", "D"):
        pct = round(100.0 * tot[aturan] / hutan2009, 2) if hutan2009 > 0 else None
        con.execute("INSERT INTO atribusi_izin_aktif_ringkas VALUES (?,?,?,?,?)",
                    (aturan, ATURAN_LABEL[aturan], round(tot[aturan], 2),
                     pct, n_kohort[aturan]))

    # (_kelas dihapus — cleanup 12 Agu r3.)
    con.execute("DROP TABLE IF EXISTS atribusi_izin_aktif_kelas")

    con.commit()
    print(f"atribusi_izin_aktif: X0={tot['X0']:,.0f} B={tot['B']:,.0f} "
          f"C={tot['C']:,.0f} D={tot['D']:,.0f} ha · kohort B {n_kohort['B']} · "
          f"hutan2009 {hutan2009:,.0f} ha")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
