#!/usr/bin/env python3
"""Langkah 03 — klasifikasi izin (SKEMA.md §3 `izin_klasifikasi`): apakah `tahun_izin` konsesi
tahun pemberian PERTAMA atau PERPANJANGAN. INDIKASI, bukan kepastian.

    python pipeline/03_izin.py --db data/tanah-hilang.db --himpunan minerba

PORT scripts/klasifikasi_perpanjangan.py (aturan `vonis()` sama persis, urutan uji = kekuatan bukti):
  PERPANJANGAN+KUAT      jenis_izin PKP2B/KK dengan tahun_izin ≥ 2009 — sistem kontrak karya UU 11/1967
                         berhenti terbit sejak UU 4/2009, jadi tahun di data pasti bukan pemberian pertama.
  PERPANJANGAN+INDIKASI  SK Operasi Produksi (registri) berjangka < 20 th (UU 4/2009 Ps. 47: pemberian
                         pertama 20 th).
  IZIN_PERTAMA+INDIKASI  SK Operasi Produksi berjangka ≥ 20 th — konsisten, bukan terbukti.
  TAK_DINILAI            tahap eksplorasi, tanggal registri tak lengkap (termasuk tak cocok registri),
                         atau jenis izin lain.
Dua bendera pelengkap (tak menentukan kelas): masa_berlaku_diwarisi (tahun berlaku registri <
tahun_izin) dan pra_izin_dominan (>50% kehilangan Hansen terjadi sebelum tahun_izin; NULL bila tak
ada kehilangan). Input pengganti wiup_master: `konsesi` (jenis_izin, tahun_izin), `konsesi_registri`
(tahap_kegiatan, tanggal_berlaku/berakhir), `izin_laju` (hilang_pra_ha, hilang_pasca_ha — jendela 2024;
arsip memakai jendela 2025 untuk sisi pasca, hanya menyentuh bendera pra_izin_dominan).

Kaveat wajib: pangsa PERPANJANGAN naik monoton menurut tahun izin → perancu, bukan variabel kontrol.
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib import db as L  # noqa: E402
from pipeline.lib.meta import LISENSI, tulis_meta  # noqa: E402
from pipeline.lib.w1_util import pastikan_v_konsesi, tahun_dari  # noqa: E402

SKRIP = "pipeline/03_izin.py"
JANGKA_PENUH = 20                 # UU 4/2009 Pasal 47
SISTEM_LAMA = ("PKP2B", "KK")

DDL = """
CREATE TABLE izin_klasifikasi (
  kode_wiup             TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  kelas                 TEXT NOT NULL,
  bukti                 TEXT,
  dasar                 TEXT NOT NULL,
  durasi_sk             INTEGER,
  masa_berlaku_diwarisi INTEGER NOT NULL,
  pra_izin_dominan      INTEGER,
  tahun_mulai_indikasi  INTEGER
);
"""


def tahun_mulai_indikasi(kelas: str, tahun_izin) -> int | None:
    """Tahun konsesi DIANGGAP mulai aktif menurut indikasi kelas izin — jam bersama
    poligon peta & Sankey 'konsesi aktif' (permintaan istri user, 25 Sep 2026).

    Aturan (identik T41C stata & garis hijau slide igoen): PERPANJANGAN → tahun_izin − 20
    (SK yang tercatat diasumsikan perpanjangan satu jangka penuh UU 4/2009 Ps. 47);
    IZIN_PERTAMA & TAK_DINILAI → tahun_izin apa adanya; tanpa tahun_izin → NULL.
    KAVEAT WAJIB ikut tampil: ini INDIKASI — aturan −20 meleset pada 82% konsesi
    PERPANJANGAN yang bisa diperiksa (median 14 th terlalu awal; lihat
    docs/analisis/bukaan-tambang-harga-dan-umur.md §4.4)."""
    if tahun_izin is None:
        return None
    return int(tahun_izin) - JANGKA_PENUH if kelas == "PERPANJANGAN" else int(tahun_izin)


def vonis(jenis_izin, tahun_izin, tahap, durasi) -> tuple[str, str | None, str]:
    """PORT klasifikasi_perpanjangan.vonis — murni, urutan uji tak diubah."""
    tahap = (tahap or "").strip().upper()
    if jenis_izin in SISTEM_LAMA and (tahun_izin or 0) >= 2009:
        return ("PERPANJANGAN", "KUAT",
                "PKP2B/KK tak mungkin terbit ≥2009 (sistem kontrak karya UU 11/1967 berhenti sejak UU 4/2009); "
                "tahun ini pasti bukan pemberian pertama.")
    if tahap == "OPERASI PRODUKSI" and durasi is not None:
        if durasi < JANGKA_PENUH:
            return ("PERPANJANGAN", "INDIKASI",
                    f"SK Operasi Produksi berjangka {durasi} th; pemberian pertama menurut UU 4/2009 Ps. 47 "
                    f"adalah {JANGKA_PENUH} th.")
        return ("IZIN_PERTAMA", "INDIKASI",
                f"Durasi SK {durasi} th konsisten sebagai pemberian pertama — konsisten, bukan terbukti.")
    sebab = ("tahap eksplorasi (jangka legalnya memang pendek)" if tahap == "EKSPLORASI"
             else "tanggal berlaku/berakhir tak lengkap" if durasi is None
             else f"jenis izin {jenis_izin or '?'}")
    return ("TAK_DINILAI", None, f"Tak bisa dinilai: {sebab}.")


def tulis_semua_meta(con) -> None:
    s = "konsesi (jenis_izin, tahun_izin) ⋈ konsesi_registri (tahap_kegiatan, tanggal_berlaku, tanggal_berakhir) ⋈ izin_laju (hilang_pra_ha, hilang_pasca_ha)"
    tulis_meta(con, "izin_klasifikasi",
               deskripsi="Vonis per konsesi: tahun_izin adalah izin PERTAMA atau PERPANJANGAN — INDIKASI dari data "
                         "sendiri (norma UU 4/2009), bukan kepastian. Penanda risiko salah-baca tahun_izin sebagai "
                         "'tahun izin pertama'; pangsa PERPANJANGAN naik menurut tahun izin → perancu.",
               sumber=s,
               metode="vonis() berurutan: PKP2B/KK ber-tahun_izin ≥2009 → PERPANJANGAN+KUAT; SK Operasi Produksi "
                      "(registri) durasi <20 th → PERPANJANGAN+INDIKASI; ≥20 th → IZIN_PERTAMA+INDIKASI; selainnya "
                      "TAK_DINILAI. durasi_sk = tahun(tanggal_berakhir) − tahun(tanggal_berlaku) registri. "
                      "Port scripts/klasifikasi_perpanjangan.py.",
               skrip=SKRIP, lisensi=LISENSI["turunan"], kolom=[
        ("kode_wiup", "Kode WIUP (kunci, FK konsesi).", "-", "konsesi"),
        ("kelas", "IZIN_PERTAMA | PERPANJANGAN | TAK_DINILAI.", "vonis()", s),
        ("bukti", "KUAT (kemustahilan logis) | INDIKASI (inferensi norma hukum) | NULL bila TAK_DINILAI.", "vonis()", s),
        ("dasar", "Kalimat alasan vonis, siap kutip.", "vonis()", s),
        ("durasi_sk", "Jangka SK registri dalam tahun; NULL bila tanggal tak lengkap / tak cocok registri.", "tahun(tanggal_berakhir) − tahun(tanggal_berlaku)", "konsesi_registri"),
        ("masa_berlaku_diwarisi", "1 bila tahun tanggal_berlaku registri < tahun_izin (izin 'baru' membawa masa berlaku pendahulu).", "tahun(tanggal_berlaku) < tahun_izin", s),
        ("pra_izin_dominan", "1 bila >50% kehilangan Hansen jendela 2001–2024 terjadi sebelum tahun_izin; NULL bila tak ada kehilangan.", "hilang_pra_ha / (hilang_pra_ha + hilang_pasca_ha) > 0,5", "izin_laju"),
        ("tahun_mulai_indikasi", "Tahun konsesi DIANGGAP mulai aktif menurut indikasi kelas izin — jam poligon peta & "
         "Sankey 'konsesi aktif'. INDIKASI: aturan −20 meleset pada 82% konsesi PERPANJANGAN yang bisa diperiksa "
         "(median 14 th terlalu awal; docs/analisis/bukaan-tambang-harga-dan-umur.md §4.4). NULL bila tanpa tahun_izin.",
         "tahun_izin − 20 bila kelas = PERPANJANGAN; selainnya tahun_izin", s),
    ])


def main() -> int:
    ap = L.argparser("03 izin_klasifikasi (indikasi izin pertama vs perpanjangan)")
    a = ap.parse_args()
    t0 = time.time()
    con = L.buka(a.db)
    L.wajib_tabel(con, "konsesi", "konsesi_registri", "izin_laju")
    if L.baca_bangun(con, "himpunan") != a.himpunan:
        L.gagal(f"DB dibangun untuk himpunan '{L.baca_bangun(con, 'himpunan')}', bukan '{a.himpunan}'")

    baris = []
    for kode, jenis, tahun_izin, tahap, t_b, t_a, pra, pasca in con.execute("""
            SELECT k.kode_wiup, k.jenis_izin, k.tahun_izin, r.tahap_kegiatan, r.tanggal_berlaku, r.tanggal_berakhir,
                   l.hilang_pra_ha, l.hilang_pasca_ha
            FROM konsesi k LEFT JOIN konsesi_registri r USING (kode_wiup) LEFT JOIN izin_laju l USING (kode_wiup)
            ORDER BY k.kode_wiup"""):
        y_b, y_a = tahun_dari(t_b), tahun_dari(t_a)
        durasi = (y_a - y_b) if (y_b is not None and y_a is not None) else None
        kelas, bukti, dasar = vonis(jenis, tahun_izin, tahap, durasi)
        pra, pasca = pra or 0.0, pasca or 0.0
        baris.append((kode, kelas, bukti, dasar, durasi,
                      int(y_b is not None and tahun_izin is not None and y_b < tahun_izin),
                      None if (pra + pasca) <= 0 else int(pra / (pra + pasca) > 0.5),
                      tahun_mulai_indikasi(kelas, tahun_izin)))

    con.execute("DROP TABLE IF EXISTS izin_klasifikasi")
    con.executescript(DDL)
    con.executemany("INSERT INTO izin_klasifikasi VALUES (?,?,?,?,?,?,?,?)", baris)
    con.commit()
    tulis_semua_meta(con)
    sebaran = Counter((b[1], b[2]) for b in baris)
    # Angka jangkar jam indikasi (himpunan minerba: 378 aktif ≤2001, 808 ≤2024 — T41C).
    aktif_2001 = sum(1 for b in baris if b[7] is not None and b[7] <= 2001)
    aktif_2024 = sum(1 for b in baris if b[7] is not None and b[7] <= 2024)
    print(f"  jam indikasi: aktif ≤2001 = {aktif_2001}, ≤2024 = {aktif_2024}, tanpa tahun = "
          f"{sum(1 for b in baris if b[7] is None)}")
    pastikan_v_konsesi(con, SKRIP)
    L.tandai_selesai(con, "03_izin", n_konsesi=len(baris),
                     aktif_2001_indikasi=aktif_2001, aktif_2024_indikasi=aktif_2024,
                     sebaran="; ".join(f"{k}{'+' + b if b else ''}={n}" for (k, b), n in sorted(sebaran.items(), key=lambda x: str(x[0]))))
    con.close()
    print(f"03_izin selesai: {len(baris)} konsesi; sebaran " +
          ", ".join(f"{k}{'+' + b if b else ''}={n}" for (k, b), n in sorted(sebaran.items(), key=lambda x: str(x[0]))) +
          f" ({time.time()-t0:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
