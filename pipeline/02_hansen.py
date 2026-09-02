#!/usr/bin/env python3
"""Langkah 02 — Hansen GFC jendela tesis 2001–2024 (SKEMA.md §2): `hansen_ringkas`,
`hansen_tahunan` (PADAT: 24 baris per konsesi, 0 bila tak ada kehilangan), `izin_laju`.

    python pipeline/02_hansen.py --db data/tanah-hilang.db --himpunan minerba

Input: data/analysis/batch_KALIMANTAN_t30_wide.csv (hasil scripts/batch_analyze.py — overlay poligon ×
raster Hansen, ambang kanopi 30). Kolom `loss_2025_ha` SENGAJA diabaikan (jendela tesis 2001–2024).
Logika vonis = PORT scripts/temporal_iup.py bagian jendela 2024 (ambang 1,5 / 0,67); `tahun_izin`
dibaca dari tabel `konsesi` (bukan geojson). Aturan batas: tahun_izin NULL atau < 2001 →
`tanpa_tahun_izin`; tahun_izin > 2024 → `izin_setelah_jendela` (sisi pra tetap dihitung 2001–2024,
sisi pasca NULL).

Invarian yang di-assert di sini: Σ hansen_tahunan per konsesi = hilang_2001_2024_ha (tol 0,01).
Idempoten: DROP + CREATE tabel miliknya saja. Gagal keras bila CSV/tabel hulu absen.
"""
from __future__ import annotations

import csv
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib import db as L  # noqa: E402
from pipeline.lib.meta import LISENSI, tulis_meta  # noqa: E402
from pipeline.lib.w1_util import TAHUN_AKHIR, TAHUN_AWAL, TAHUN_JENDELA, ke_angka  # noqa: E402

SKRIP = "pipeline/02_hansen.py"
BATCH = L.AKAR / "data/analysis/batch_KALIMANTAN_t30_wide.csv"
AMBANG_NAIK, AMBANG_TURUN = 1.5, 0.67

DDL = """
CREATE TABLE hansen_ringkas (
  kode_wiup           TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  hutan_2000_ha       REAL NOT NULL,
  hilang_2001_2024_ha REAL NOT NULL,
  pct_hutan_2000      REAL,
  tahun_puncak        INTEGER,
  tile_hansen         TEXT
);
CREATE TABLE hansen_tahunan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup),
  tahun     INTEGER NOT NULL CHECK (tahun BETWEEN 2001 AND 2024),
  hilang_ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, tahun)
);
CREATE TABLE izin_laju (
  kode_wiup          TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  tahun_izin         INTEGER,
  hilang_pra_ha      REAL, n_tahun_pra INTEGER, laju_pra_ha_thn REAL,
  hilang_pasca_ha    REAL, n_tahun_pasca INTEGER, laju_pasca_ha_thn REAL,
  rasio_pasca_pra    REAL,
  vonis              TEXT NOT NULL
);
"""


def vonis_laju(laju_pra: float, laju_pasca: float, rasio: float) -> str:
    """PORT temporal_iup.nilai_verdict — urutan uji sama persis."""
    if laju_pra == 0 and laju_pasca > 0:
        return "loss_only_after_iup"
    if rasio > AMBANG_NAIK:
        return "accelerated_post_iup"
    if 0 < rasio < AMBANG_TURUN:
        return "decelerated_post_iup"
    if rasio == 0:
        return "no_loss_either"
    return "stable"


def hitung_izin_laju(kode: str, tahun_izin: int | None, tahunan: dict[int, float]) -> dict:
    d = {"kode_wiup": kode, "tahun_izin": tahun_izin, "hilang_pra_ha": None, "n_tahun_pra": None,
         "laju_pra_ha_thn": None, "hilang_pasca_ha": None, "n_tahun_pasca": None, "laju_pasca_ha_thn": None,
         "rasio_pasca_pra": None, "vonis": "tanpa_tahun_izin"}
    if tahun_izin is None or tahun_izin < TAHUN_AWAL:
        return d
    pra = sum(v for y, v in tahunan.items() if y < tahun_izin)
    n_pra = sum(1 for y in TAHUN_JENDELA if y < tahun_izin)
    laju_pra = pra / n_pra if n_pra > 0 else 0.0
    d.update(hilang_pra_ha=round(pra, 2), n_tahun_pra=n_pra, laju_pra_ha_thn=round(laju_pra, 2))
    if tahun_izin > TAHUN_AKHIR:
        d["vonis"] = "izin_setelah_jendela"
        return d
    pasca = sum(v for y, v in tahunan.items() if y >= tahun_izin)
    n_pasca = TAHUN_AKHIR - tahun_izin + 1
    laju_pasca = pasca / n_pasca
    rasio = laju_pasca / laju_pra if laju_pra > 0 else (float("inf") if laju_pasca > 0 else 0.0)
    d.update(hilang_pasca_ha=round(pasca, 2), n_tahun_pasca=n_pasca, laju_pasca_ha_thn=round(laju_pasca, 2),
             rasio_pasca_pra=None if rasio == float("inf") else round(rasio, 2),
             vonis=vonis_laju(laju_pra, laju_pasca, rasio))
    return d


def tulis_semua_meta(con) -> None:
    s = "data/analysis/batch_KALIMANTAN_t30_wide.csv (Hansen GFC-2025 v1.13 × poligon konsesi, ambang kanopi 30%)"
    tulis_meta(con, "hansen_ringkas",
               deskripsi="Ringkasan Hansen per konsesi, jendela tesis 2001–2024: hutan 2000, total kehilangan tutupan "
                         "pohon, persen terhadap hutan 2000, tahun puncak, ubin GFC.",
               sumber=s,
               metode="Rasterisasi poligon ke grid Hansen (~30 m), piksel hutan = treecover2000 ≥30% di dalam poligon; "
                      "kehilangan tahunan = Σ luas piksel (koreksi lintang per baris) dengan lossyear = tahun. "
                      "hilang_2001_2024_ha = Σ loss_2001_ha..loss_2024_ha (kolom 2025 diabaikan). Angka = batas atas "
                      "deforestasi: Hansen mengukur hilangnya tutupan pohon apa pun.",
               skrip=SKRIP, lisensi=LISENSI["hansen"], kolom=[
        ("kode_wiup", "Kode WIUP (kunci, FK konsesi).", "-", "konsesi"),
        ("hutan_2000_ha", "Luas hutan tahun 2000 (kanopi ≥30%) di dalam konsesi, ha (eks forest_2000_ha).", "Σ luas piksel treecover2000≥30 ∧ mask", s),
        ("hilang_2001_2024_ha", "Total kehilangan tutupan pohon 2001–2024, ha.", "Σ_{t=2001..2024} hansen_tahunan.hilang_ha (identitas, di-assert)", s),
        ("pct_hutan_2000", "Persen kehilangan 2001–2024 terhadap hutan 2000; NULL bila hutan 2000 = 0.", "ROUND(100 × hilang_2001_2024_ha / hutan_2000_ha, 2)", s),
        ("tahun_puncak", "Tahun dengan kehilangan tahunan terbesar (tahun terkecil bila seri; NULL bila semua 0).", "argmax_t hilang_ha", s),
        ("tile_hansen", "Ubin GFC 10°×10° yang menaungi poligon, dipisah '|' (eks tiles).", "-", s),
    ])
    tulis_meta(con, "hansen_tahunan",
               deskripsi="Kehilangan tutupan pohon per konsesi per tahun 2001–2024 — PADAT: 24 baris per konsesi, "
                         "0 bila tak ada kehilangan (eks wiup_loss_yearly yang hanya menyimpan baris >0).",
               sumber=s, metode="Kolom loss_YYYY_ha batch CSV di-unpivot; nilai 0 tetap ditulis supaya deret waktu lengkap.",
               skrip=SKRIP, lisensi=LISENSI["hansen"], kolom=[
        ("kode_wiup", "Kode WIUP (FK konsesi).", "-", "konsesi"),
        ("tahun", "Tahun kehilangan, 2001–2024 (lossyear 1..24).", "-", s),
        ("hilang_ha", "Kehilangan tutupan pohon pada tahun itu, ha (0 bila tak ada).", "Σ luas piksel lossyear = tahun ∧ hutan 2000", s),
    ])
    tulis_meta(con, "izin_laju",
               deskripsi="Laju kehilangan SEBELUM vs SESUDAH tahun SK yang berlaku saat ini, jendela 2001–2024 "
                         "(eks wiup_temporal kolom *_2024). Vonis = petunjuk kasar, bukan sebab-akibat: tahun_izin "
                         "sering tahun perpanjangan (lihat izin_klasifikasi).",
               sumber="hansen_tahunan × konsesi.tahun_izin",
               metode="pra = Σ hilang 2001..tahun_izin−1 (n_pra tahun), pasca = Σ hilang tahun_izin..2024 (n_pasca tahun); "
                      "laju = Σ/n; rasio = laju_pasca/laju_pra. Vonis (port scripts/temporal_iup.py): laju_pra=0 ∧ "
                      "laju_pasca>0 → loss_only_after_iup; rasio>1,5 → accelerated_post_iup; 0<rasio<0,67 → "
                      "decelerated_post_iup; rasio=0 → no_loss_either; selainnya stable. tahun_izin>2024 → "
                      "izin_setelah_jendela (pasca NULL); NULL atau <2001 → tanpa_tahun_izin.",
               skrip=SKRIP, lisensi=LISENSI["hansen"], kolom=[
        ("kode_wiup", "Kode WIUP (kunci, FK konsesi).", "-", "konsesi"),
        ("tahun_izin", "Tahun SK berlaku saat ini (salinan konsesi.tahun_izin).", "konsesi.tahun_izin", "konsesi"),
        ("hilang_pra_ha", "Kehilangan 2001..tahun_izin−1, ha (NULL bila tanpa_tahun_izin).", "Σ hilang_ha, tahun < tahun_izin", "hansen_tahunan"),
        ("n_tahun_pra", "Jumlah tahun pra-izin di jendela (tahun_izin − 2001).", "COUNT tahun ∈ [2001, tahun_izin)", "hansen_tahunan"),
        ("laju_pra_ha_thn", "Laju pra-izin, ha/tahun (0 bila n_tahun_pra = 0).", "hilang_pra_ha / n_tahun_pra", "hansen_tahunan"),
        ("hilang_pasca_ha", "Kehilangan tahun_izin..2024, ha (NULL bila tahun_izin di luar jendela).", "Σ hilang_ha, tahun ≥ tahun_izin", "hansen_tahunan"),
        ("n_tahun_pasca", "Jumlah tahun pasca-izin di jendela (2024 − tahun_izin + 1).", "COUNT tahun ∈ [tahun_izin, 2024]", "hansen_tahunan"),
        ("laju_pasca_ha_thn", "Laju pasca-izin, ha/tahun.", "hilang_pasca_ha / n_tahun_pasca", "hansen_tahunan"),
        ("rasio_pasca_pra", "Rasio laju pasca/pra; NULL bila laju_pra = 0 (tak terdefinisi — lihat vonis).", "laju_pasca_ha_thn / laju_pra_ha_thn", "hansen_tahunan"),
        ("vonis", "accelerated_post_iup | decelerated_post_iup | loss_only_after_iup | stable | no_loss_either | izin_setelah_jendela | tanpa_tahun_izin.", "lihat metode", "hansen_tahunan"),
    ])


def main() -> int:
    ap = L.argparser("02 hansen: hansen_ringkas, hansen_tahunan, izin_laju")
    a = ap.parse_args()
    t0 = time.time()
    L.wajib_ada(BATCH, keterangan="batch CSV Hansen — jalankan scripts/batch_analyze.py")
    con = L.buka(a.db)
    L.wajib_tabel(con, "konsesi")
    if L.baca_bangun(con, "himpunan") != a.himpunan:
        L.gagal(f"DB dibangun untuk himpunan '{L.baca_bangun(con, 'himpunan')}', bukan '{a.himpunan}'")

    konsesi = dict(con.execute("SELECT kode_wiup, tahun_izin FROM konsesi"))
    ringkas, tahunan, laju = [], [], []
    with open(BATCH, newline="") as f:
        for r in csv.DictReader(f):
            kode = r["kode_wiup"]
            if kode not in konsesi:
                continue
            per_tahun = {y: (ke_angka(r.get(f"loss_{y}_ha"), 0.0) or 0.0) for y in TAHUN_JENDELA}
            hutan = ke_angka(r.get("forest_2000_ha"))
            if hutan is None:
                L.gagal(f"forest_2000_ha kosong untuk {kode}")
            total = round(sum(per_tahun.values()), 2)
            puncak = max(per_tahun, key=lambda y: (per_tahun[y], -y)) if total > 0 else None
            ringkas.append((kode, hutan, total, round(100.0 * total / hutan, 2) if hutan > 0 else None,
                            puncak, r.get("tiles") or None))
            tahunan.extend((kode, y, v) for y, v in per_tahun.items())
            laju.append(hitung_izin_laju(kode, konsesi[kode], per_tahun))
    tanpa = sorted(set(konsesi) - {x[0] for x in ringkas})
    if tanpa:
        print(f"  {len(tanpa)} konsesi tanpa baris batch CSV (tak ada baris Hansen): {', '.join(tanpa)}")
    if not ringkas:
        L.gagal("tak satu pun kode_wiup batch CSV ada di tabel konsesi")

    con.executescript("DROP TABLE IF EXISTS izin_laju; DROP TABLE IF EXISTS hansen_tahunan; DROP TABLE IF EXISTS hansen_ringkas;")
    con.executescript(DDL)
    con.executemany("INSERT INTO hansen_ringkas VALUES (?,?,?,?,?,?)", ringkas)
    con.executemany("INSERT INTO hansen_tahunan VALUES (?,?,?)", tahunan)
    con.executemany("""INSERT INTO izin_laju VALUES (:kode_wiup,:tahun_izin,:hilang_pra_ha,:n_tahun_pra,:laju_pra_ha_thn,
        :hilang_pasca_ha,:n_tahun_pasca,:laju_pasca_ha_thn,:rasio_pasca_pra,:vonis)""", laju)
    con.execute("CREATE INDEX IF NOT EXISTS idx_hansen_tahunan_tahun ON hansen_tahunan(tahun)")
    con.commit()

    # Invarian: Σ tahunan = ringkas per konsesi; 24 baris per konsesi.
    salah = con.execute("""SELECT h.kode_wiup, h.hilang_2001_2024_ha, t.s, t.n FROM hansen_ringkas h
                           JOIN (SELECT kode_wiup, SUM(hilang_ha) s, COUNT(*) n FROM hansen_tahunan GROUP BY 1) t USING (kode_wiup)
                           WHERE ABS(h.hilang_2001_2024_ha - t.s) > 0.01 OR t.n <> 24""").fetchall()
    if salah:
        L.gagal(f"identitas Σ hansen_tahunan ≠ hilang_2001_2024_ha / bukan 24 baris pada {len(salah)} konsesi, contoh {salah[:3]}")

    tulis_semua_meta(con)
    total = con.execute("SELECT SUM(hilang_2001_2024_ha), SUM(hutan_2000_ha) FROM hansen_ringkas").fetchone()
    vonis = Counter(x["vonis"] for x in laju)
    L.tandai_selesai(con, "02_hansen", n_konsesi=len(ringkas), hilang_2001_2024_ha=round(total[0], 2),
                     hutan_2000_ha=round(total[1], 2), konsesi_tanpa_csv=",".join(tanpa) or "-")
    con.close()
    print(f"02_hansen selesai: {len(ringkas)} konsesi, Σ hilang 2001–2024 = {total[0]:,.2f} ha dari hutan 2000 "
          f"{total[1]:,.2f} ha; vonis: {dict(vonis)} ({time.time()-t0:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
