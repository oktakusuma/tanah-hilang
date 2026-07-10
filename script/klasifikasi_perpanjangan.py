#!/usr/bin/env python3
"""Klasifikasi per konsesi: izin ini pemberian pertama, atau perpanjangan?

MASALAH YANG DIJAWAB. Kerangka tiga periode kewenangan izin mengelompokkan
konsesi menurut `iup_year`. Kelompok itu hanya sahih kalau `iup_year` benar
berarti "tahun izin PERTAMA terbit". Kalau sebagian di antaranya ternyata
tahun PERPANJANGAN, konsesinya sudah beroperasi jauh lebih dulu daripada yang
tercatat — dan pengelompokannya keliru. Skrip ini menguji dugaan itu memakai
DATA KITA SENDIRI saja, tanpa sumber luar.

DASAR HUKUMNYA. UU 4/2009 Pasal 47: IUP Operasi Produksi diberikan pertama
kali untuk 20 tahun, lalu dapat diperpanjang dua kali masing-masing 10 tahun.
Jadi SK Operasi Produksi yang berjangka KURANG dari 20 tahun mustahil menjadi
pemberian pertama — hampir pasti ia perpanjangan.

TIGA KELAS + KEKUATAN BUKTI. `vonis()` memutuskan satu dari tiga VONIS
(PERPANJANGAN / IZIN_PERTAMA / TAK_DINILAI), lalu — kecuali TAK_DINILAI —
memberi label seberapa kuat buktinya (KUAT / INDIKASI). Aturan & urutan
evaluasinya sama persis dengan v1 (empat kelas datar); yang berubah cuma
bentuk keluaran: vonis dipisah dari kekuatan bukti.

  PERPANJANGAN + KUAT       Jenis izinnya PKP2B/KK — sistem kontrak karya
                             UU 11/1967 yang BERHENTI terbit setelah UU
                             4/2009 — tapi iup_year tercatat >= 2009. Mustahil
                             kontrak baru, jadi tahun di data pasti bukan
                             tahun pemberian pertama. INI KEMUSTAHILAN LOGIS,
                             bukan dugaan — karena itu buktinya KUAT. Yang
                             terbukti cuma bahwa tahunnya salah; bentuk
                             persisnya (perpanjangan? pendaftaran ulang?
                             perubahan?) tak bisa dipastikan — makanya tetap
                             divonis PERPANJANGAN sebagai kelas yang paling
                             mendekati, bukan diklaim sebagai kepastian penuh.

  PERPANJANGAN + INDIKASI   IUP Operasi Produksi ber-SK < 20 tahun (lihat
                             dasar hukum di atas). Petunjuk kuat, tapi tetap
                             INFERENSI dari norma hukum — bukan dokumen yang
                             menyatakan "ini perpanjangan". Karena itu
                             INDIKASI, bukan KUAT.

  IZIN_PERTAMA + INDIKASI   IUP Operasi Produksi ber-SK >= 20 tahun — cocok
                             dengan pemberian pertama. Buktinya tetap
                             INDIKASI, bukan KUAT: registri tak menyimpan
                             sejarah izin, jadi konsesi di kelas ini tetap
                             BISA saja perpanjangan yang jangkanya kebetulan
                             panjang. Konsisten, bukan terbukti.

  TAK_DINILAI                Tahap eksplorasi (jangka legalnya memang pendek
                             7-8 tahun, jadi ukuran 20-tahun tak berlaku),
                             tanggal tak lengkap, atau jenis izin lain.
                             Buktinya None — dua hal yang sangat berbeda dari
                             "izin pertama", dan menyamakannya berarti
                             mengklaim sesuatu yang datanya tidak dukung.

DUA BENDERA PELENGKAP (tidak menentukan kelas, tapi memperkaya bacaan):

  masa_berlaku_diwarisi  tahun tanggal_berlaku < iup_year — izin "baru" yang
                         membawa masa berlaku pendahulunya.
  pra_izin_dominan       > 50% kehilangan Hansen terjadi SEBELUM iup_year.
                         Satelit menguatkan bahwa kegiatan sudah berjalan
                         lebih dulu. NULL bila konsesi tak punya kehilangan.

BATAS YANG WAJIB DIBACA SEBELUM MENGUTIP. Kelas PERPANJANGAN tersebar TIDAK
merata antar periode — pangsanya naik dari P1 ke P3, nyaris berimpit dengan
variabel periode itu sendiri. Karena itu angka ini TIDAK boleh dipakai
sebagai variabel kontrol begitu saja; ia penanda risiko salah-kelompok, dan
harus dilaporkan sebagai keterbatasan. Ada dua bacaan yang belum
terpisahkan: (a) memang gelombang perpanjangan, atau (b) artefak
pencatatan — registri merekam masa berlaku SEKARANG, bukan jangka aslinya.
Keduanya sama-sama berarti `iup_year` tak bisa dibaca sebagai "tahun izin
pertama" di P3.

JANGAN TULIS ANGKA PASTINYA DI SINI — cepat basi (berubah tiap re-scrape/
re-run pipeline); nilai persis SELALU dihitung ulang dari DB, bukan disalin
dari komentar ini. Sumber tunggal: blok `lapisan.pangsa_perpanjangan_periode`
di `scripts/gen_dashboard_stats.py` (dipakai MethodologyView.tsx via
`webapp/src/lib/stats.ts::LAP`). Query yang sama persis (pakai
`build_periode_tables.to_periode()` utk pengelompokan periode, JANGAN CASE
WHEN SQL duplikat):

    SELECT to_periode(g.iup_year) AS periode,
           100.0 * SUM(z.kelas='PERPANJANGAN') / COUNT(*) AS pangsa_persen
    FROM klasifikasi_izin z JOIN wiup_geoportal g ON g.kode_wiup = z.kode_wiup
    -- to_periode() adalah fungsi Python (lihat build_periode_tables.py), bukan
    -- fungsi SQL bawaan — jalankan lewat Python (conn.execute + groupby di atas),
    -- baris ini sekadar ilustrasi bentuk agregasinya.
    GROUP BY periode  -- kecualikan None & 'Pra-2009', hanya P1/P2/P3 yg relevan

INPUT   : data/kalimantan.db (wiup_master)
OUTPUT  : tabel klasifikasi_izin (satu baris per konsesi) di DB yang sama —
          skema (cangkang) didefinisikan di build_combined_db.LAPISAN_SHELLS.

Pemakaian:
    python scripts/klasifikasi_perpanjangan.py
    python scripts/klasifikasi_perpanjangan.py --kering
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

TABEL = "klasifikasi_izin"
JANGKA_PENUH = 20          # UU 4/2009 Pasal 47 — jangka pemberian pertama
SISTEM_LAMA = ("PKP2B", "KK")

# Harus persis sama dgn cangkang di build_combined_db.LAPISAN_SHELLS.
SCHEMA = """
CREATE TABLE klasifikasi_izin (
  kode_wiup              TEXT PRIMARY KEY,
  kelas                  TEXT NOT NULL,
  bukti                  TEXT,
  dasar                  TEXT NOT NULL,
  durasi_sk              INTEGER,
  masa_berlaku_diwarisi  INTEGER NOT NULL,
  pra_izin_dominan       INTEGER
)
"""

URUTAN_KELAS = (
    ("PERPANJANGAN", "KUAT"),
    ("PERPANJANGAN", "INDIKASI"),
    ("IZIN_PERTAMA", "INDIKASI"),
    ("TAK_DINILAI", None),
)


def tahun(tgl) -> int | None:
    if not tgl or len(str(tgl)) < 4 or not str(tgl)[:4].isdigit():
        return None
    return int(str(tgl)[:4])


def vonis(baris: dict) -> tuple[str, str | None, str]:
    """(kelas, bukti, dasar) untuk satu konsesi.

    `baris` butuh: jenis_izin, iup_year, nama_tahap_kegiatan, durasi_sk
    (durasi_sk sudah dihitung pemanggil dari tanggal_berlaku/tanggal_berakhir —
    vonis() sendiri murni, tak menyentuh tanggal mentah).
    Urutan uji = urutan kekuatan bukti (aturan sama persis dgn v1).
    """
    jenis_izin = baris.get("jenis_izin")
    iup_year = baris.get("iup_year")
    tahap = (baris.get("nama_tahap_kegiatan") or "").strip().upper()
    dur = baris.get("durasi_sk")

    if jenis_izin in SISTEM_LAMA and (iup_year or 0) >= 2009:
        return ("PERPANJANGAN", "KUAT",
                "PKP2B/KK tak mungkin terbit ≥2009 (sistem kontrak karya UU "
                "11/1967 berhenti sejak UU 4/2009); tahun ini pasti bukan "
                "pemberian pertama.")

    if tahap == "OPERASI PRODUKSI" and dur is not None:
        if dur < JANGKA_PENUH:
            return ("PERPANJANGAN", "INDIKASI",
                    f"SK Operasi Produksi berjangka {dur} th; pemberian pertama "
                    f"menurut UU 4/2009 Ps. 47 adalah {JANGKA_PENUH} th.")
        return ("IZIN_PERTAMA", "INDIKASI",
                f"Durasi SK {dur} th konsisten sebagai pemberian pertama — "
                f"konsisten, bukan terbukti.")

    sebab = ("tahap eksplorasi (jangka legalnya memang pendek)"
             if tahap == "EKSPLORASI"
             else "tanggal berlaku/berakhir tak lengkap"
             if dur is None else f"jenis izin {jenis_izin or '?'}")
    return ("TAK_DINILAI", None, f"Tak bisa dinilai: {sebab}.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="data/kalimantan.db")
    ap.add_argument("--kering", action="store_true")
    args = ap.parse_args(argv)

    db = pathlib.Path(args.db)
    if not db.exists():
        print(f"GAGAL: basis data tak ada di {db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        baris = []
        for m in con.execute(
                """SELECT kode_wiup, jenis_izin, nama_tahap_kegiatan, iup_year,
                          tanggal_berlaku, tanggal_berakhir,
                          loss_pre_iup_ha, loss_post_iup_ha
                   FROM wiup_master"""):
            m = dict(m)
            y_berlaku = tahun(m["tanggal_berlaku"])
            y_akhir = tahun(m["tanggal_berakhir"])
            durasi_sk = (y_akhir - y_berlaku) if (y_berlaku is not None
                                                   and y_akhir is not None) else None
            kelas, bukti, dasar = vonis({
                "jenis_izin": m["jenis_izin"],
                "iup_year": m["iup_year"],
                "nama_tahap_kegiatan": m["nama_tahap_kegiatan"],
                "durasi_sk": durasi_sk,
            })
            pre = m["loss_pre_iup_ha"] or 0.0
            post = m["loss_post_iup_ha"] or 0.0
            baris.append({
                "kode_wiup": m["kode_wiup"],
                "kelas": kelas,
                "bukti": bukti,
                "dasar": dasar,
                "durasi_sk": durasi_sk,
                "masa_berlaku_diwarisi": int(
                    y_berlaku is not None and m["iup_year"] is not None
                    and y_berlaku < m["iup_year"]),
                "pra_izin_dominan": None if (pre + post) <= 0 else int(pre / (pre + post) > 0.5),
            })

        agg: dict[tuple[str, str | None], int] = {}
        for b in baris:
            k = (b["kelas"], b["bukti"])
            agg[k] = agg.get(k, 0) + 1
        print("[klasifikasi] sebaran kelas+bukti (dari data sendiri, tanpa sumber luar):",
              file=sys.stderr)
        for kelas, bukti in URUTAN_KELAS:
            label = f"{kelas} ({bukti})" if bukti else kelas
            print(f"    {label:30s} {agg.get((kelas, bukti), 0):4d}", file=sys.stderr)

        if args.kering:
            print("(kering — tabel tidak ditulis)", file=sys.stderr)
            return 0
        con.execute(f"DROP TABLE IF EXISTS {TABEL}")
        con.execute(SCHEMA)
        con.executemany(
            f"""INSERT INTO {TABEL} VALUES
                (:kode_wiup,:kelas,:bukti,:dasar,:durasi_sk,
                 :masa_berlaku_diwarisi,:pra_izin_dominan)""", baris)
        con.commit()
        print(f"\n{TABEL}: {len(baris)} baris ditulis ke {db}", file=sys.stderr)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
