#!/usr/bin/env python3
"""Pemeriksa invarian DB analisis — "make verify" (rekomendasi ANALISA-MENYELURUH #3).

Dokumentasi 4-unsur (Konvensi #4):

(a) APA (rumus/isi): kumpulan asersi bernama yang menegakkan identitas internal
    DB analisis — identitas yang HARUS berlaku untuk rebuild mana pun, bukan
    hafalan angka:
      • jumlah konsesi & cakupan baris pengukuran (geoportal vs wiup_loss);
      • identitas jendela Descals per baris: total_loss = loss_2001_2021 +
        loss_2022_2025 (toleransi 0,01 ha per baris; agregat |selisih| ≤ 5 ha);
      • sawit ≤ loss_2001_2021 per baris utk KETIGA varian; persen_sawit ∈ [0,100];
        atribusi_sawit_yearly berhenti persis di 2021 (batas peta Descals);
      • identitas silang F15: sawit_pra + sawit_pasca_2021 = sawit_tol2th, dan
        loss_pre + loss_post = total_loss; NULL pra/pasca hanya boleh utk
        iup_year di luar 2001–2025 atau NULL;
      • domain klasifikasi_izin (kelas/bukti) + jumlah = jumlah konsesi;
      • integritas rujukan: semua tabel anak → wiup_geoportal tanpa yatim;
      • rekonsiliasi periode_ringkasan vs hitung-ulang dari tabel dasar
        (pengelompokan meniru PERSIS to_periode() di build_periode_tables.py);
      • metadata: analysis_meta mencakup semua tabel non-inti; column_meta
        = 100% dua arah terhadap PRAGMA table_info;
      • opsional: dashboard-stats.json cocok dgn hitung-ulang DB (--stats).
    Tanpa dependensi pihak ketiga (murni stdlib: sqlite3/argparse/json) supaya
    bisa jalan di mesin mana pun, termasuk yang tak punya rasterio/numpy.

(b) CARA PAKAI:
      .venv/bin/python scripts/verify_invariants.py                  # data/ (default)
      ... --db data-full/kalimantan.db --light                       # varian lengkap
      ... --stats webapp/src/generated/dashboard-stats.json          # + cek stats web
      ... --no-expect                                                # tanpa snapshot kanonik
    Mode --light = hitungan + rujukan + klasifikasi-silang (data-full/ tak punya
    lapisan sawit/klasifikasi yang terisi — cangkang kosong by design — tapi
    baseline_tahunan TETAP terisi & tetap diverifikasi; cek_klasifikasi_silang()
    skip per-tabel-absen, jadi aman dipanggil di kedua mode).

(c) APA YANG DITAMPILKAN: satu baris per pemeriksaan
    `[PASS|WARN|FAIL] nama — detail angka`, lalu baris ringkasan. Exit code
    0 = semua PASS/WARN (WARN = anomali kecil yang sudah dikenal & terdokumentasi,
    mis. drift piksel-tepi 1,53 ha di 2 konsesi — lihat Metodologi), 1 = ada FAIL.

(d) CARA KERJA / REPRODUKSI: DB dibuka KETAT baca-saja (URI `file:...?mode=ro`)
    — skrip ini TIDAK PERNAH menulis apa pun. Semua asersi = query SQL murni yang
    bisa dijalankan ulang manual di sqlite3. Dipanggil otomatis di ujung
    rescrape/process.sh (bagian verifikasi, bukan langkah pipeline) supaya tiap
    rebuild DB langsung ketahuan sehat/tidaknya tanpa menunggu audit manual.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# ── Snapshot kanonik SAAT INI — perbarui bila data resmi berubah ─────────────
# Ini BUKAN orakel identitas internal (semua cek lain tetap berlaku utk rebuild
# apa pun); ini jangkar tambahan agar rebuild yang diam-diam menggeser angka
# utama tesis langsung ketahuan. Matikan dgn --no-expect, ganti nilai loss dgn
# --expect-headline. Sumber: batch_KALIMANTAN_t30 (Hansen GFC v1.13, ambang 30%).
SNAPSHOT_N_KONSESI = 825          # konsesi minerba (batubara + logam) di data/
SNAPSHOT_LOSS_TOTAL_HA = 1_603_251  # Σ loss_2001_2025_ha (eks total_loss_ha; toleransi ±1 ha)

# Tabel inti (bukan tabel analisis turunan) yang memang tak butuh baris
# analysis_meta. analysis_meta sendiri belum punya baris utk dirinya (catatan ⚪
# audit) — masuk daftar ini supaya tak jadi FAIL palsu.
CORE_TANPA_META = {"analysis_meta"}

# Kolom yang tak boleh negatif (per tabel). "light" = subset utk mode --light.
KOLOM_NON_NEGATIF = {
    "wiup_loss": ["polygon_area_ha", "forest_2000_ha", "loss_2001_2025_ha",
                  "loss_2001_2008_ha", "loss_2009_2025_ha", "loss_2009_2025_pct_hutan2009"],
    "wiup_loss_yearly": ["loss_ha"],
    "wiup_temporal": ["loss_2001_sampai_tahun_izin_ha", "loss_tahun_izin_sampai_2025_ha", "n_tahun_dari_2001_sampai_tahun_izin",
                      "n_tahun_dari_tahun_izin_sampai_2025", "rate_2001_sampai_tahun_izin_ha_per_year", "rate_tahun_izin_sampai_2025_ha_per_year"],
    "atribusi_sawit": ["loss_2001_2021_ha", "loss_sawit_tol2th_2001_2021_ha",
                       "loss_sawit_jeda5th_2001_2021_ha", "loss_sawit_tahunsama_2001_2021_ha",
                       "loss_2022_2025_ha", "loss_sawit_2001_sampai_tahun_izin_ha",
                       "loss_sawit_tahun_izin_sampai_2021_ha", "loss_tahun_izin_sampai_2021_ha"],
    "atribusi_sawit_yearly": ["loss_sawit_tol2th_ha"],
    "periode_ringkasan": ["luas_total_ha", "loss_2001_2025_ha", "forest2000_total_ha"],
    "laju_izin_konsesi": ["loss_mulai_aktif_sampai_2025_ha", "laju_mulai_aktif_sampai_2025_ha_thn", "laju_mulai_aktif_sampai_2025_pct_thn",
                          "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "laju_mulai_aktif_sampai_2021_tanpa_sawit_ha_thn", "laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn"],
    "laju_izin_eventstudy": ["sum_loss_ha", "mean_loss_ha",
                             "sum_tanpa_sawit_sampai_2021_ha",
                             "mean_tanpa_sawit_sampai_2021_ha"],
    "backtrack_tahunan": ["loss_ha", "loss_tanpa_sawit_ha", "hutan_awal_tahun_ha"],
}
TABEL_NON_NEGATIF_LIGHT = ("wiup_loss", "wiup_loss_yearly", "wiup_temporal")

# Tabel anak → wiup_geoportal (integritas rujukan). "light" = subset.
TABEL_ANAK = ("wiup_loss", "wiup_loss_yearly", "wiup_temporal", "wiup_match",
              "atribusi_sawit", "atribusi_sawit_yearly", "klasifikasi_izin")
TABEL_ANAK_LIGHT = ("wiup_loss", "wiup_loss_yearly", "wiup_temporal", "wiup_match")


def to_periode(y):
    """CERMIN PERSIS scripts/build_periode_tables.py::to_periode() — jendela izin
    1998–2025 simetris; NULL/di-luar-jendela dibuang (None). Sengaja direplikasi
    (bukan di-import) supaya pemeriksa berdiri sendiri dari kode yang diperiksanya;
    kalau definisi periode berubah di sana, ubah di sini juga (rekonsiliasi
    periode akan FAIL kalau keduanya tak sinkron — itu memang tujuannya)."""
    if y is None or y < 1998 or y > 2025:
        return None
    if y < 2009:
        return "Pra-2009"
    if y <= 2014:
        return "P1"
    if y <= 2019:
        return "P2"
    return "P3"


# Versi SQL dari to_periode() di atas — dipakai di rekonsiliasi periode.
SQL_PERIODE = """CASE
    WHEN iup_year < 2009 THEN 'Pra-2009'
    WHEN iup_year <= 2014 THEN 'P1'
    WHEN iup_year <= 2019 THEN 'P2'
    ELSE 'P3' END"""
SQL_JENDELA_IZIN = "iup_year IS NOT NULL AND iup_year BETWEEN 1998 AND 2025"


class Pelapor:
    """Kumpulkan hasil per pemeriksaan; cetak `[STATUS] nama — detail`."""

    def __init__(self):
        self.n = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def catat(self, status, nama, detail):
        self.n[status] += 1
        print(f"[{status}] {nama} — {detail}")

    def ok(self, nama, detail):
        self.catat("PASS", nama, detail)

    def warn(self, nama, detail):
        self.catat("WARN", nama, detail)

    def fail(self, nama, detail):
        self.catat("FAIL", nama, detail)

    def ringkas(self):
        total = sum(self.n.values())
        print(f"RINGKASAN: {total} pemeriksaan · {self.n['PASS']} PASS · "
              f"{self.n['WARN']} WARN · {self.n['FAIL']} FAIL")
        return 1 if self.n["FAIL"] else 0


def tabel_ada(con, nama):
    """True bila tabel/view bernama `nama` ada — guard utk DB varian (data-full
    tak punya atribusi_izin_aktif_ringkas dsb.; lihat temuan audit 15 Agu)."""
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (nama,)).fetchone() is not None


def satu(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return r[0] if r else None


# ── Pemeriksaan ──────────────────────────────────────────────────────────────

def cek_hitungan(con, lap, expect_n):
    n_geo = satu(con, "SELECT COUNT(*) FROM wiup_geoportal")
    n_loss = satu(con, "SELECT COUNT(*) FROM wiup_loss")
    if expect_n is not None:
        if n_geo == expect_n and n_loss == expect_n:
            lap.ok("jumlah-konsesi", f"wiup_geoportal={n_geo}, wiup_loss={n_loss} (= snapshot {expect_n})")
        else:
            lap.fail("jumlah-konsesi",
                     f"wiup_geoportal={n_geo}, wiup_loss={n_loss}, snapshot={expect_n}")
    else:
        lap.ok("jumlah-konsesi", f"wiup_geoportal={n_geo}, wiup_loss={n_loss} (tanpa snapshot)")

    # Konsesi terdaftar tapi tak terukur (tanpa baris loss). Kasus dikenal:
    # 1 PASIR KUARSA di data-full (lihat KATALOG-DATA) → WARN, bukan FAIL.
    hilang = con.execute(
        "SELECT kode_wiup FROM wiup_geoportal "
        "WHERE kode_wiup NOT IN (SELECT kode_wiup FROM wiup_loss) ORDER BY 1").fetchall()
    if not hilang:
        lap.ok("cakupan-pengukuran", "semua konsesi geoportal punya baris wiup_loss")
    else:
        kode = ", ".join(k for (k,) in hilang[:5]) + (" …" if len(hilang) > 5 else "")
        lap.warn("cakupan-pengukuran",
                 f"{len(hilang)} konsesi tanpa baris wiup_loss (tak terukur): {kode}")


def cek_headline(con, lap, expect_loss):
    total = satu(con, "SELECT COALESCE(SUM(loss_2001_2025_ha),0) FROM wiup_loss")
    selisih = abs(total - expect_loss)
    if selisih <= 1.0:
        lap.ok("total-loss-kanonik", f"Σ total_loss = {total:,.2f} ha (selisih {selisih:.2f} ≤ 1 ha dari {expect_loss:,})")
    else:
        lap.fail("total-loss-kanonik", f"Σ total_loss = {total:,.2f} ha, snapshot {expect_loss:,} (selisih {selisih:.2f} ha)")


def cek_jendela_descals(con, lap):
    # total = 2001–2021 + 2022–2025 per baris. Pelanggar kecil = drift
    # piksel-tepi attribution_sawit vs batch_analyze (terdokumentasi di
    # Metodologi) → WARN per baris; FAIL hanya bila agregat drift > 5 ha.
    rows = con.execute(
        """SELECT s.kode_wiup, ABS(l.loss_2001_2025_ha - (s.loss_2001_2021_ha + s.loss_2022_2025_ha)) d
           FROM atribusi_sawit s JOIN wiup_loss l USING (kode_wiup)
           WHERE ABS(l.loss_2001_2025_ha - (s.loss_2001_2021_ha + s.loss_2022_2025_ha)) > 0.01
           ORDER BY d DESC""").fetchall()
    # Agregat = Σ selisih HANYA baris pelanggar (> 0,01) — derau pembulatan
    # sub-toleransi di 800-an baris lain tak boleh merayap ke ambang FAIL.
    agregat = sum(d for _, d in rows)
    if agregat > 5.0:
        lap.fail("identitas-jendela-descals",
                 f"drift agregat {agregat:.2f} ha > 5 ha ({len(rows)} baris melanggar)")
    elif rows:
        rinci = "; ".join(f"{k} ({d:.2f} ha)" for k, d in rows[:5])
        lap.warn("identitas-jendela-descals",
                 f"{len(rows)} baris melanggar toleransi 0,01 ha (agregat {agregat:.2f} ha ≤ 5): {rinci}")
    else:
        lap.ok("identitas-jendela-descals",
               f"total = 2001_2021 + 2022_2025 di semua baris (drift agregat {agregat:.2f} ha)")


def cek_sawit(con, lap):
    # Tiap varian sawit ≤ loss_2001_2021 per baris.
    for varian in ("loss_sawit_tol2th_2001_2021_ha", "loss_sawit_jeda5th_2001_2021_ha", "loss_sawit_tahunsama_2001_2021_ha"):
        n = satu(con, f"SELECT COUNT(*) FROM atribusi_sawit WHERE {varian} > loss_2001_2021_ha + 0.01")
        if n:
            lap.fail(f"sawit≤loss:{varian}", f"{n} baris {varian} > loss_2001_2021_ha")
        else:
            lap.ok(f"sawit≤loss:{varian}", "semua baris ≤ loss_2001_2021_ha")

    n = satu(con, """SELECT COUNT(*) FROM atribusi_sawit
                     WHERE loss_2001_2021_ha > 0
                       AND (100.0*loss_sawit_tol2th_2001_2021_ha/loss_2001_2021_ha < 0
                            OR 100.0*loss_sawit_tol2th_2001_2021_ha/loss_2001_2021_ha > 100.000001)""")
    if n:
        lap.fail("persen-sawit-0-100", f"{n} baris persen_sawit di luar [0,100]")
    else:
        lap.ok("persen-sawit-0-100", "semua baris persen_sawit ∈ [0,100]")

    ymax = satu(con, "SELECT MAX(year) FROM atribusi_sawit_yearly")
    if ymax == 2021:
        lap.ok("sawit-yearly-batas-2021", "MAX(year) = 2021 (persis batas peta Descals)")
    else:
        lap.fail("sawit-yearly-batas-2021",
                 f"MAX(year) = {ymax} ≠ 2021 — 2022+ TIDAK boleh masuk lapisan sawit")


def cek_non_negatif(con, lap, tabel_saja=None):
    pelanggar = []
    for tabel, kolom2 in KOLOM_NON_NEGATIF.items():
        if tabel_saja is not None and tabel not in tabel_saja:
            continue
        for kolom in kolom2:
            n = satu(con, f"SELECT COUNT(*) FROM {tabel} WHERE {kolom} < 0")
            if n:
                pelanggar.append(f"{tabel}.{kolom}×{n}")
    if pelanggar:
        lap.fail("non-negatif", "nilai negatif: " + ", ".join(pelanggar))
    else:
        lap.ok("non-negatif", "tak ada nilai negatif di kolom-kolom kunci")


def cek_f15(con, lap):
    # (1) sawit_pra + sawit_pasca_2021 = sawit_tol2th (baris non-NULL).
    n = satu(con, """SELECT COUNT(*) FROM atribusi_sawit
                     WHERE loss_sawit_2001_sampai_tahun_izin_ha IS NOT NULL
                       AND ABS(loss_sawit_2001_sampai_tahun_izin_ha + loss_sawit_tahun_izin_sampai_2021_ha
                               - loss_sawit_tol2th_2001_2021_ha) > 0.01""")
    tot = satu(con, "SELECT COUNT(*) FROM atribusi_sawit WHERE loss_sawit_2001_sampai_tahun_izin_ha IS NOT NULL")
    if n:
        lap.fail("f15-identitas-sawit", f"{n}/{tot} baris pra+pasca ≠ tol2th (tol 0,01)")
    else:
        lap.ok("f15-identitas-sawit", f"sawit_pra + sawit_pasca_2021 = sawit_tol2th di semua {tot} baris non-NULL")

    # (2) loss_pre + loss_post = total_loss bila keduanya ada (tol 0,1 ha).
    n = satu(con, """SELECT COUNT(*) FROM wiup_temporal t JOIN wiup_loss l USING (kode_wiup)
                     WHERE t.loss_2001_sampai_tahun_izin_ha IS NOT NULL AND t.loss_tahun_izin_sampai_2025_ha IS NOT NULL
                       AND ABS(t.loss_2001_sampai_tahun_izin_ha + t.loss_tahun_izin_sampai_2025_ha - l.loss_2001_2025_ha) > 0.1""")
    if n:
        lap.fail("f15-pra+pasca=total", f"{n} baris pre+post ≠ total_loss (tol 0,1 ha)")
    else:
        lap.ok("f15-pra+pasca=total", "loss_pre + loss_post = total_loss di semua baris lengkap")

    # (3) NULL pra/pasca HANYA utk iup_year NULL atau di luar 2001–2025
    #     (garis izin tak terdefinisi di dalam deret Hansen 2001–2025).
    for tabel, kol in (("wiup_temporal", "loss_2001_sampai_tahun_izin_ha"),
                       ("atribusi_sawit", "loss_sawit_2001_sampai_tahun_izin_ha")):
        n = satu(con, f"""SELECT COUNT(*) FROM {tabel} x JOIN wiup_geoportal g USING (kode_wiup)
                          WHERE x.{kol} IS NULL
                            AND g.iup_year IS NOT NULL AND g.iup_year BETWEEN 2001 AND 2025""")
        if n:
            lap.fail(f"f15-null-terkendali:{tabel}",
                     f"{n} baris NULL padahal iup_year di dalam 2001–2025")
        else:
            n_null = satu(con, f"SELECT COUNT(*) FROM {tabel} WHERE {kol} IS NULL")
            lap.ok(f"f15-null-terkendali:{tabel}",
                   f"{n_null} baris NULL, semuanya ber-iup_year NULL/di luar 2001–2025 (by design)")


def cek_klasifikasi(con, lap):
    aneh = con.execute(
        """SELECT kelas, bukti, COUNT(*) FROM klasifikasi_izin
           WHERE kelas NOT IN ('IZIN_PERTAMA','PERPANJANGAN','TAK_DINILAI')
              OR (bukti IS NOT NULL AND bukti NOT IN ('KUAT','INDIKASI'))
           GROUP BY 1,2""").fetchall()
    if aneh:
        lap.fail("klasifikasi-domain", f"nilai di luar domain: {aneh}")
    else:
        lap.ok("klasifikasi-domain",
               "kelas ∈ {IZIN_PERTAMA, PERPANJANGAN, TAK_DINILAI}, bukti ∈ {KUAT, INDIKASI, NULL}")

    n_klas = satu(con, "SELECT COUNT(*) FROM klasifikasi_izin")
    n_geo = satu(con, "SELECT COUNT(*) FROM wiup_geoportal")
    if n_klas == n_geo:
        lap.ok("klasifikasi-lengkap", f"Σ klasifikasi_izin = {n_klas} = jumlah konsesi")
    else:
        lap.fail("klasifikasi-lengkap", f"Σ klasifikasi_izin = {n_klas} ≠ {n_geo} konsesi")


def cek_referensial(con, lap, tabel_anak):
    yatim = []
    for tabel in tabel_anak:
        n = satu(con, f"""SELECT COUNT(*) FROM {tabel}
                          WHERE kode_wiup NOT IN (SELECT kode_wiup FROM wiup_geoportal)""")
        if n:
            yatim.append(f"{tabel}×{n}")
    if yatim:
        lap.fail("integritas-rujukan", "baris yatim (kode_wiup tak dikenal): " + ", ".join(yatim))
    else:
        lap.ok("integritas-rujukan",
               f"0 baris yatim di {len(tabel_anak)} tabel anak → wiup_geoportal")


def cek_rekonsiliasi_periode(con, lap):
    # Hitung ulang dari tabel dasar dgn pengelompokan yang meniru PERSIS
    # to_periode(): jendela izin 1998–2025 (NULL & 2026+ dibuang), Pra-2009
    # ikut sebagai catatan kaki. SUM SQL mengabaikan NULL = perilaku builder
    # (baris tanpa data dibuang, bukan dianggap 0).
    ulang = {r[0]: r[1:] for r in con.execute(f"""
        SELECT {SQL_PERIODE} periode, COUNT(*) n,
               ROUND(SUM(g.luas_sk), 2)      luas,
               ROUND(SUM(l.loss_2001_2025_ha), 2) loss,
               ROUND(SUM(l.forest_2000_ha), 2) forest
        FROM wiup_geoportal g LEFT JOIN wiup_loss l USING (kode_wiup)
        WHERE {SQL_JENDELA_IZIN}
        GROUP BY 1""")}
    tersimpan = {r[0]: r[1:] for r in con.execute(
        "SELECT periode, n, luas_total_ha, loss_2001_2025_ha, forest2000_total_ha "
        "FROM periode_ringkasan")}
    beda = []
    for p in sorted(set(ulang) | set(tersimpan)):
        a, b = ulang.get(p), tersimpan.get(p)
        if a is None or b is None:
            beda.append(f"{p}: hanya di satu sisi")
            continue
        if a[0] != b[0]:
            beda.append(f"{p}: n {b[0]} ≠ {a[0]}")
        for i, kol in ((1, "luas"), (2, "loss"), (3, "forest2000")):
            if a[i] is not None and b[i] is not None and abs(a[i] - b[i]) > 0.02:
                beda.append(f"{p}: {kol} {b[i]} ≠ {a[i]}")
    if beda:
        lap.fail("rekonsiliasi-periode", "; ".join(beda))
    else:
        kohort = sum(v[0] for v in ulang.values())
        total = satu(con, "SELECT COUNT(*) FROM wiup_geoportal")
        lap.ok("rekonsiliasi-periode",
               f"periode_ringkasan = hitung-ulang (kohort {kohort}, "
               f"eksklusi jendela izin {total - kohort} dari {total})")


def cek_klasifikasi_silang(con, lap):
    """Sel periode_klasifikasi harus merekonsiliasi n periode, dan baseline
    harus utuh 2001-2025. Dua tabel ini opsional (lapisan) — absen = skip."""
    def tabel_ada(_con, nama):
        return satu(_con,
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name=?", (nama,)) > 0

    if not tabel_ada(con, "periode_klasifikasi"):
        lap.warn("klasifikasi-silang", "periode_klasifikasi absen — dilewati")
    else:
        beda = []
        for per, sel in con.execute(
            "SELECT periode, SUM(n) FROM periode_klasifikasi GROUP BY periode"
        ):
            ring = con.execute(
                "SELECT n FROM periode_ringkasan WHERE periode=?", (per,)
            ).fetchone()
            if ring is None:
                beda.append(f"{per}: tak ada di periode_ringkasan")
            elif sel != ring[0]:
                beda.append(f"{per}: Σ sel {sel} ≠ n periode {ring[0]}")
        if beda:
            lap.fail("klasifikasi-silang", "; ".join(beda))
        else:
            lap.ok("klasifikasi-silang", "Σ sel per periode = n periode_ringkasan")

    if not tabel_ada(con, "baseline_tahunan"):
        lap.warn("baseline-rentang", "baseline_tahunan absen — dilewati")
        return
    tahun = [r[0] for r in con.execute(
        "SELECT year FROM baseline_tahunan ORDER BY year")]
    if tahun != list(range(2001, 2026)):
        lap.fail("baseline-rentang",
                 f"tahun tak utuh: {len(tahun)} baris, {tahun[:1]}..{tahun[-1:]}")
        return
    total = con.execute("SELECT SUM(loss_ha) FROM baseline_tahunan").fetchone()[0]
    mentah = con.execute(
        "SELECT SUM(loss_ha) FROM wiup_loss_yearly WHERE year BETWEEN 2001 AND 2025"
    ).fetchone()[0]
    if abs((total or 0) - (mentah or 0)) > 0.02:
        lap.fail("baseline-rentang", f"Σ baseline {total} ≠ Σ mentah {mentah}")
    else:
        lap.ok("baseline-rentang", f"2001-2025 utuh, Σ = {total:,.2f} ha (= sumber)")


def cek_atribusi(con, lap):
    """Invarian atribusi izin aktif: monotonik D≤C≤B≤X0, rekonsiliasi 3 arah,
    identitas hutan-2009, kohort. Tabel opsional — absen = lewati (data-full)."""
    def tabel_ada(nama):
        return satu(con, "SELECT COUNT(*) FROM sqlite_master "
                         "WHERE type='table' AND name=?", (nama,)) > 0
    if not tabel_ada("atribusi_izin_aktif"):
        lap.warn("atribusi-izin", "atribusi_izin_aktif absen — dilewati")
        return
    buruk = satu(con, """SELECT COUNT(*) FROM atribusi_izin_aktif
        WHERE loss_mulai_d_sampai_2025_ha > loss_mulai_c_sampai_2025_ha + 0.011
           OR loss_mulai_c_sampai_2025_ha > loss_mulai_b_sampai_2025_ha + 0.011
           OR loss_mulai_b_sampai_2025_ha > loss_2009_2025_ha + 0.011""")
    if buruk:
        lap.fail("atribusi-monotonik", f"{buruk} konsesi melanggar D≤C≤B≤X0")
    else:
        lap.ok("atribusi-monotonik", "D ≤ C ≤ B ≤ X0 utk semua konsesi")
    x0 = satu(con, "SELECT loss_mulai_aturan_sampai_2025_ha "
                   "FROM atribusi_izin_aktif_ringkas WHERE aturan='X0'")
    sumber = satu(con, "SELECT SUM(loss_ha) FROM wiup_loss_yearly "
                       "WHERE year BETWEEN 2009 AND 2025")
    if abs((x0 or 0) - (sumber or 0)) > 0.02:
        lap.fail("atribusi-x0-sumber", f"ringkas X0 {x0} ≠ Σ sumber {sumber}")
    else:
        lap.ok("atribusi-x0-sumber", f"X0 = Σ wiup_loss_yearly 2009-2025 ({x0:,.2f} ha)")
    beda = []
    for aturan, kolom in (("X0", "loss_2009_2025_ha"), ("B", "loss_mulai_b_sampai_2025_ha"),
                          ("C", "loss_mulai_c_sampai_2025_ha"), ("D", "loss_mulai_d_sampai_2025_ha")):
        r = satu(con, "SELECT loss_mulai_aturan_sampai_2025_ha "
                      "FROM atribusi_izin_aktif_ringkas WHERE aturan=?", (aturan,))
        per = satu(con, f"SELECT SUM({kolom}) FROM atribusi_izin_aktif")
        if abs((r or 0) - (per or 0)) > 0.5:
            beda.append(f"{aturan}: ringkas {r} / per-konsesi {per}")
    if beda:
        lap.fail("atribusi-rekonsiliasi", "; ".join(beda))
    else:
        lap.ok("atribusi-rekonsiliasi", "ringkas = Σ per-konsesi (4 aturan; tabel _tahunan/_kelas dihapus cleanup r3)")
    n_b = satu(con, "SELECT n_kohort FROM atribusi_izin_aktif_ringkas WHERE aturan='B'")
    n_hitung = satu(con, "SELECT COUNT(*) FROM atribusi_izin_aktif WHERE mulai_b IS NOT NULL")
    if n_b != n_hitung:
        lap.fail("atribusi-kohort", f"n_kohort B {n_b} ≠ hitung ulang {n_hitung}")
    else:
        lap.ok("atribusi-kohort", f"kohort B = {n_b} konsesi (mulai_b NOT NULL)")
    # Identitas penyebut: pct tersimpan harus cocok dgn hitung ulang dari sumber
    # (hutan-2009 = Σforest_2000 − Σloss 2001-2008) — bukan angka lepas.
    pct_b = satu(con, "SELECT pct_hutan2009 FROM atribusi_izin_aktif_ringkas WHERE aturan='B'")
    loss_b = satu(con, "SELECT loss_mulai_aturan_sampai_2025_ha "
                      "FROM atribusi_izin_aktif_ringkas WHERE aturan='B'")
    h2009 = (satu(con, "SELECT COALESCE(SUM(forest_2000_ha),0) FROM wiup_loss")
             - satu(con, "SELECT COALESCE(SUM(loss_ha),0) FROM wiup_loss_yearly "
                         "WHERE year BETWEEN 2001 AND 2008"))
    if pct_b is None or h2009 <= 0:
        lap.warn("atribusi-hutan2009", "pct NULL atau penyebut ≤ 0 — periksa sumber")
    elif abs(pct_b - 100.0 * loss_b / h2009) > 0.02:
        lap.fail("atribusi-hutan2009",
                 f"pct B tersimpan {pct_b} ≠ hitung ulang {100.0 * loss_b / h2009:.2f}")
    else:
        lap.ok("atribusi-hutan2009",
               f"pct B = 100·loss/hutan2009 (hutan2009 = {h2009:,.2f} ha, identitas eksak)")


def cek_laju(con, lap):
    """Invarian laju jam-bukti (aturan E, pivot 12 Agu r2): rekonsiliasi mandiri
    Σ loss_kotor vs hitung-ulang dari wiup_loss_yearly per mulai, domain
    dasar_mulai, batas ≤ X0, bersih ≤ kotor, pct [0,100]. Absen = lewati."""
    ada = satu(con, "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='laju_izin_konsesi'") > 0
    if not ada:
        lap.warn("laju-izin", "laju_izin_konsesi absen — dilewati")
        return
    # Rekonsiliasi mandiri: Σ loss_kotor tersimpan = Σ hitung ulang dari sumber.
    tot_k = satu(con, "SELECT COALESCE(SUM(loss_mulai_aktif_sampai_2025_ha),0) FROM laju_izin_konsesi "
                      "WHERE mulai IS NOT NULL")
    ulang = satu(con, """SELECT COALESCE(SUM(y.loss_ha),0)
        FROM laju_izin_konsesi l JOIN wiup_loss_yearly y USING (kode_wiup)
        WHERE l.mulai IS NOT NULL AND y.year BETWEEN l.mulai AND 2025""")
    if abs(tot_k - ulang) > 5:
        lap.fail("laju-rekonsiliasi", f"Σ loss_kotor {tot_k:,.2f} ≠ hitung ulang {ulang:,.2f}")
    else:
        lap.ok("laju-rekonsiliasi", f"Σ loss_kotor = hitung ulang dari sumber ({tot_k:,.2f} ha)")
    # E tak boleh melebihi X0 (semua loss era Minerba tanpa atribusi).
    x0 = (satu(con, "SELECT loss_mulai_aturan_sampai_2025_ha "
                    "FROM atribusi_izin_aktif_ringkas WHERE aturan='X0'")
          if tabel_ada(con, "atribusi_izin_aktif_ringkas") else None)
    if x0 is not None and tot_k > x0 + 0.5:
        lap.fail("laju-batas-x0", f"Σ loss_kotor E {tot_k:,.2f} > X0 {x0:,.2f}")
    elif x0 is not None:
        lap.ok("laju-batas-x0", f"E ≤ X0 (selisih {x0 - tot_k:,.2f} ha = derau di bawah ambang bukti)")
    # Domain dasar_mulai & konsistensi mulai/tahun_bukti.
    n_dom = satu(con, """SELECT COUNT(*) FROM laju_izin_konsesi WHERE
        (mulai IS NOT NULL AND dasar_mulai NOT IN ('BUKTI','IZIN'))
        OR (mulai IS NULL AND dasar_mulai IS NOT NULL)
        -- BUKTI: mulai = tahun_bukti YANG SUDAH DIKLEM ke >= 2009 (bukti boleh
        -- 2001-2008; jendela hitung tetap era Minerba). Lihat hitung_mulai_bukti.
        OR (dasar_mulai = 'BUKTI' AND (tahun_bukti IS NULL OR mulai != MAX(2009, tahun_bukti)))
        OR (mulai IS NOT NULL AND (mulai < 2009 OR mulai > 2025))""")
    if n_dom:
        lap.fail("laju-dasar-mulai", f"{n_dom} baris melanggar domain dasar_mulai/mulai")
    else:
        n_bukti = satu(con, "SELECT COUNT(*) FROM laju_izin_konsesi WHERE dasar_mulai='BUKTI'")
        lap.ok("laju-dasar-mulai", f"domain OK; jam-bukti (backtrack) {n_bukti} konsesi")
    # Bersih tak boleh melebihi kotor (jendela lebih pendek DAN sawit dipotong).
    n_bad = satu(con, "SELECT COUNT(*) FROM laju_izin_konsesi "
                      "WHERE loss_mulai_aktif_sampai_2021_tanpa_sawit_ha IS NOT NULL "
                      "AND loss_mulai_aktif_sampai_2021_tanpa_sawit_ha > loss_mulai_aktif_sampai_2025_ha + 0.011")
    if n_bad:
        lap.fail("laju-bersih≤kotor", f"{n_bad} konsesi loss_bersih > loss_kotor")
    else:
        lap.ok("laju-bersih≤kotor", "loss_bersih ≤ loss_kotor utk semua konsesi")
    n_pct = satu(con, "SELECT COUNT(*) FROM laju_izin_konsesi WHERE "
                      "(laju_mulai_aktif_sampai_2025_pct_thn IS NOT NULL AND (laju_mulai_aktif_sampai_2025_pct_thn < 0 OR laju_mulai_aktif_sampai_2025_pct_thn > 100)) "
                      "OR (laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn IS NOT NULL AND (laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn < 0 OR laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn > 100))")
    if n_pct:
        lap.fail("laju-pct-0-100", f"{n_pct} baris laju %/thn di luar [0,100]")
    else:
        lap.ok("laju-pct-0-100", "laju %/thn dalam [0,100]")
    # Ringkas 'semua' harus merekonsiliasi total & n dgn per-konsesi.
    for basis, kolom, syarat in (("kotor", "loss_mulai_aktif_sampai_2025_ha", "mulai IS NOT NULL"),
                                 ("bersih", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha IS NOT NULL")):
        r = con.execute("SELECT n, total_loss_ha FROM laju_izin_ringkas "
                        "WHERE basis=? AND dimensi='semua'", (basis,)).fetchone()
        if r is None:
            lap.fail("laju-ringkas", f"baris ringkas basis {basis} hilang")
            continue
        n_hit = satu(con, f"SELECT COUNT(*) FROM laju_izin_konsesi WHERE {syarat}")
        tot = satu(con, f"SELECT COALESCE(SUM({kolom}),0) FROM laju_izin_konsesi")
        if r[0] != n_hit or abs((r[1] or 0) - tot) > 0.5:
            lap.fail("laju-ringkas",
                     f"basis {basis}: ringkas n={r[0]}/Σ={r[1]} ≠ hitung {n_hit}/{tot:,.2f}")
        else:
            lap.ok(f"laju-ringkas-{basis}", f"ringkas = Σ per-konsesi ({tot:,.2f} ha, n={n_hit})")


def cek_jendela2009(con, lap):
    """Identitas jendela era Minerba — kini KOLOM di wiup_loss (Fase B r2):
    hutan_2009 = forest_2000 − Σ loss 2001-2008 dan loss_2009_2025 =
    loss_2001_2025 − loss_2001_2008 — eksak dari sumber, bukan angka lepas."""
    kolom = {r[1] for r in con.execute("PRAGMA table_info(wiup_loss)")}
    if "hutan_2009_ha" not in kolom:
        lap.warn("jendela-2009", "kolom jendela era Minerba absen di wiup_loss — dilewati")
        return
    buruk = satu(con, """SELECT COUNT(*) FROM wiup_loss l
        LEFT JOIN (SELECT kode_wiup, SUM(loss_ha) s FROM wiup_loss_yearly
                   WHERE year < 2009 GROUP BY kode_wiup) y USING (kode_wiup)
        WHERE l.forest_2000_ha IS NOT NULL AND (
              ABS(l.hutan_2009_ha - (l.forest_2000_ha - COALESCE(y.s,0))) > 0.05
           OR ABS(l.loss_2001_2008_ha - COALESCE(y.s,0)) > 0.05
           OR ABS(COALESCE(l.loss_2001_2025_ha,0)
                  - l.loss_2001_2008_ha - l.loss_2009_2025_ha) > 0.1)""")
    # (Toleransi dekomposisi 0,1 — tiga nilai dibulatkan 2 desimal secara
    # independen (total dari CSV; pecahan dari per-tahun), drift sah s.d. ~0,05.)
    if buruk:
        lap.fail("jendela-2009-identitas", f"{buruk} baris melanggar identitas jendela")
    else:
        tot = satu(con, "SELECT SUM(loss_2009_2025_ha) FROM wiup_loss")
        lap.ok("jendela-2009-identitas",
               f"hutan_2009 = forest_2000 − loss 2001-2008 & dekomposisi jendela utuh "
               f"(Σ loss 2009-2025 = {tot:,.2f} ha)")


def cek_metadata(con, lap):
    semua = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%'")}
    ada_meta = {r[0] for r in con.execute("SELECT nama_tabel FROM analysis_meta")}
    tanpa_meta = semua - ada_meta - CORE_TANPA_META
    basi = ada_meta - semua
    if tanpa_meta or basi:
        pesan = []
        if tanpa_meta:
            pesan.append("tanpa baris analysis_meta: " + ", ".join(sorted(tanpa_meta)))
        if basi:
            pesan.append("baris meta utk objek yang tak ada: " + ", ".join(sorted(basi)))
        lap.fail("analysis-meta-cakupan", "; ".join(pesan))
    else:
        lap.ok("analysis-meta-cakupan",
               f"{len(ada_meta)} baris mencakup semua objek non-inti "
               f"(dikecualikan: {', '.join(sorted(CORE_TANPA_META))})")

    # column_meta dua arah: tiap tabel di column_meta punya set kolom PERSIS
    # sama dgn PRAGMA table_info, dan semua tabel ber-analysis_meta tercakup.
    beda = []
    tabel_cm = {r[0] for r in con.execute("SELECT DISTINCT nama_tabel FROM column_meta")}
    for t in sorted(tabel_cm):
        if t not in semua:
            beda.append(f"{t}: ada di column_meta tapi tak ada di DB")
            continue
        nyata = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        dicatat = {r[0] for r in con.execute(
            "SELECT nama_kolom FROM column_meta WHERE nama_tabel = ?", (t,))}
        if nyata - dicatat:
            beda.append(f"{t}: kolom tanpa meta {sorted(nyata - dicatat)}")
        if dicatat - nyata:
            beda.append(f"{t}: meta utk kolom yang tak ada {sorted(dicatat - nyata)}")
    hilang = (ada_meta | {"analysis_meta"}) - tabel_cm - (ada_meta - semua)
    if hilang:
        beda.append("tabel tanpa satu pun baris column_meta: " + ", ".join(sorted(hilang)))
    if beda:
        lap.fail("column-meta-dua-arah", "; ".join(beda))
    else:
        n = satu(con, "SELECT COUNT(*) FROM column_meta")
        lap.ok("column-meta-dua-arah",
               f"{n} baris = cakupan kolom 100% dua arah utk {len(tabel_cm)} tabel")


def cek_backtrack(con, lap):
    """Baris CITRA di backtrack_* WAJIB identik dgn tabel utama.

    backtrack_* adalah PEMBANDING; kalau jalur CITRA-nya menyimpang dari
    laju_izin_konsesi / konsesi_aktif_tahunan, ada dua sumber kebenaran.
    """
    ada = con.execute("SELECT 1 FROM sqlite_master WHERE name='backtrack_periode'").fetchone()
    if not ada:
        lap.ok("backtrack-absen", "tabel backtrack_* tak ada (lewati)")
        return
    # 1) Σ loss CITRA SELURUH ember (termasuk TANPA_PERIODE — audit 15 Agu:
    #    dulu 11 konsesi/4.165 ha bocor tanpa jejak) == Σ kohort penuh.
    a = satu(con, "SELECT ROUND(SUM(loss_mulai_aktif_sampai_2025_ha),1) FROM backtrack_periode "
                  "WHERE aturan='CITRA'")
    b = satu(con, "SELECT ROUND(SUM(loss_mulai_aktif_sampai_2025_ha),1) FROM laju_izin_konsesi "
                  "WHERE mulai IS NOT NULL")
    if a is None or b is None or abs(a - b) > 0.5:
        lap.fail("backtrack-citra-periode", f"Σ CITRA {a} ≠ laju_izin_konsesi {b}")
    else:
        lap.ok("backtrack-citra-periode", f"Σ loss CITRA per periode = tabel utama ({a:,.1f} ha)")
    # 2) n_aktif CITRA per tahun == konsesi_aktif_tahunan
    n_bad = satu(con, """SELECT COUNT(*) FROM backtrack_tahunan b
        JOIN konsesi_aktif_tahunan k USING (year)
        WHERE b.aturan='CITRA' AND (
          COALESCE(b.n_aktif,-1) != COALESCE(k.n_mulai_aktif,-1)
          OR COALESCE(b.n_sk_terbit,-1) != COALESCE(k.n_sk_terbit,-1)
          OR COALESCE(b.n_aktif_sebelum_sk,-1) != COALESCE(k.n_aktif_sebelum_sk,-1))""")
    if n_bad:
        lap.fail("backtrack-citra-tahunan", f"{n_bad} tahun CITRA ≠ konsesi_aktif_tahunan")
    else:
        lap.ok("backtrack-citra-tahunan", "deret CITRA = konsesi_aktif_tahunan (semua tahun)")
    # 3) INDIKASI/PERKIRAAN tak boleh MELEBIHI CITRA di total loss (jendela mulai
    #    citra selalu <= jendela dokumen utk konsesi yang sama... TIDAK selalu —
    #    mulai_c bisa < mulai citra (taksiran izin asal pra-bukti). Jadi cek
    #    longgar: total tiap aturan <= X0 (batas fisik semua loss 2009-2025).
    # POLOS (tanpa backtrack) harus ≈ aturan D lama (selisih pembulatan; tol 5 ha).
    if not tabel_ada(con, "atribusi_izin_aktif_ringkas"):
        lap.ok("backtrack-polos-vs-d", "atribusi_izin_aktif_ringkas absen (DB varian) — lewati")
        d_lama = None
    else:
        d_lama = satu(con, "SELECT loss_mulai_aturan_sampai_2025_ha "
                           "FROM atribusi_izin_aktif_ringkas WHERE aturan='D'")
    d_baru = satu(con, "SELECT SUM(loss_mulai_aktif_sampai_2025_ha) FROM backtrack_periode "
                       "WHERE aturan='POLOS'")
    if d_lama is not None and d_baru is not None:
        if abs(d_lama - d_baru) > 5:
            lap.fail("backtrack-polos-vs-d", f"POLOS {d_baru:,.0f} ≠ aturan D {d_lama:,.0f}")
        else:
            lap.ok("backtrack-polos-vs-d", f"POLOS = aturan D ({d_baru:,.0f} ha)")
    x0 = (satu(con, "SELECT loss_mulai_aturan_sampai_2025_ha "
                    "FROM atribusi_izin_aktif_ringkas WHERE aturan='X0'")
          if tabel_ada(con, "atribusi_izin_aktif_ringkas") else None)
    if x0 is not None:
        n_lebih = satu(con, """SELECT COUNT(*) FROM (
            SELECT aturan, SUM(loss_mulai_aktif_sampai_2025_ha) s FROM backtrack_periode
            GROUP BY aturan HAVING s > ? + 0.5)""", (x0,))
        if n_lebih:
            lap.fail("backtrack-batas-x0", f"{n_lebih} aturan melebihi X0 {x0:,.0f} ha")
        else:
            lap.ok("backtrack-batas-x0", f"semua aturan ≤ X0 ({x0:,.0f} ha)")
    # 4) Jendela KALENDER (redefinisi periode 15 Agu): ketiga jendela P1/P2/P3
    #    menyambung tanpa celah/tumpang tindih menutupi 2009-2025, jadi utk
    #    CITRA Σ loss_ha ketiganya HARUS = Σ flow backtrack_tahunan 2009-2025;
    #    dan loss_2022_2025_belum_terperiksa_ha WAJIB 0 utk P1/P2 (seluruh rentangnya
    #    ≤ 2021 = batas Descals — tak ada tahun yang "belum terperiksa").
    if not tabel_ada(con, "backtrack_periode_kalender"):
        lap.warn("backtrack-kalender-rekonsil",
                 "backtrack_periode_kalender absen — dilewati")
    else:
        kal = satu(con, "SELECT SUM(loss_ha) FROM backtrack_periode_kalender "
                        "WHERE aturan='CITRA' AND periode IN ('P1','P2','P3')")
        thn = satu(con, "SELECT SUM(loss_ha) FROM backtrack_tahunan "
                        "WHERE aturan='CITRA' AND year BETWEEN 2009 AND 2025")
        n_bocor = satu(con, "SELECT COUNT(*) FROM backtrack_periode_kalender "
                            "WHERE periode IN ('P1','P2') "
                            "AND COALESCE(loss_2022_2025_belum_terperiksa_ha, -1) != 0")
        if kal is None or thn is None or abs(kal - thn) > 0.5:
            lap.fail("backtrack-kalender-rekonsil",
                     f"Σ jendela kalender CITRA {kal} ≠ Σ tahunan 2009-2025 {thn}")
        elif n_bocor:
            lap.fail("backtrack-kalender-rekonsil",
                     f"{n_bocor} baris P1/P2 ber-loss_2022_2025_belum_terperiksa_ha ≠ 0")
        else:
            lap.ok("backtrack-kalender-rekonsil",
                   f"Σ P1+P2+P3 kalender = Σ tahunan 2009-2025 ({kal:,.2f} ha); "
                   "belum-terperiksa P1/P2 = 0")
        # 5) n_aktif_akhir tiap jendela == backtrack_tahunan.n_aktif @ tahun_akhir
        #    aturan yang sama. Lebih tahan daripada mengunci "CITRA P3 == 825":
        #    tak pecah bila kohort berubah, tapi tetap menjamin himpunan
        #    kumulatif kalender = deret tahunan (satu sumber kebenaran). P3
        #    (tahun_akhir 2025) otomatis tercakup utk semua aturan.
        n_beda = satu(con, """SELECT COUNT(*) FROM backtrack_periode_kalender k
            JOIN backtrack_tahunan t ON t.aturan = k.aturan AND t.year = k.tahun_akhir
            WHERE COALESCE(k.n_aktif_akhir, -1) != COALESCE(t.n_aktif, -2)""")
        if n_beda:
            lap.fail("backtrack-kalender-aktif",
                     f"{n_beda} baris n_aktif_akhir ≠ backtrack_tahunan.n_aktif @ tahun_akhir")
        else:
            lap.ok("backtrack-kalender-aktif",
                   "n_aktif_akhir tiap jendela = backtrack_tahunan.n_aktif @ tahun_akhir "
                   "(semua aturan, termasuk P3@2025)")
        # 6) gini_luas_aktif wajib di [0, 1] — di luar itu pasti bug rumus/data
        #    (gini selisih-berpasangan atas nilai >= 0 tak bisa keluar rentang).
        n_gini = satu(con, """SELECT COUNT(*) FROM backtrack_periode_kalender
            WHERE gini_luas_aktif IS NOT NULL
              AND (gini_luas_aktif < 0 OR gini_luas_aktif > 1)""")
        if n_gini:
            lap.fail("backtrack-kalender-gini",
                     f"{n_gini} baris gini_luas_aktif di luar [0,1]")
        else:
            lap.ok("backtrack-kalender-gini", "gini_luas_aktif ∈ [0,1] semua baris")


def cek_stats(con, lap, stats_path):
    """Kesegaran dashboard-stats.json vs hitung-ulang DB (rantai Konvensi #6)."""
    p = Path(stats_path)
    if not p.is_file():
        lap.warn("stats-web", f"{stats_path} tak ditemukan — lewati")
        return
    d = json.loads(p.read_text())
    beda = []

    n_geo = satu(con, "SELECT COUNT(*) FROM wiup_geoportal")
    if d["default"]["wiup"] != n_geo:
        beda.append(f"wiup {d['default']['wiup']} ≠ {n_geo}")

    loss = round(satu(con, "SELECT COALESCE(SUM(loss_2001_2025_ha),0) FROM wiup_loss"))
    if d["default"]["loss_ha"] != loss:
        beda.append(f"loss_ha {d['default']['loss_ha']:,} ≠ {loss:,}")

    a, b = con.execute("SELECT SUM(loss_2001_2021_ha), SUM(loss_sawit_tol2th_2001_2021_ha) "
                       "FROM atribusi_sawit").fetchone()
    persen = round(100.0 * b / a, 1) if a else None
    if d["lapisan"]["persen_sawit_2001_2021"] != persen:
        beda.append(f"persen_sawit_2001_2021 {d['lapisan']['persen_sawit_2001_2021']} ≠ {persen}")

    # Pangsa perpanjangan per periode — cermin gen_dashboard_stats.py
    # (penyebut = SEMUA konsesi periode termasuk TAK_DINILAI; Pra-2009 dikecualikan).
    hit = {}
    for kelas, iy in con.execute(
            "SELECT z.kelas, g.iup_year FROM klasifikasi_izin z "
            "JOIN wiup_geoportal g USING (kode_wiup)"):
        per = to_periode(iy)
        if per is None or per == "Pra-2009":
            continue
        tot, pj = hit.get(per, (0, 0))
        hit[per] = (tot + 1, pj + (1 if kelas == "PERPANJANGAN" else 0))
    pangsa = {per: round(100.0 * pj / tot, 1) for per, (tot, pj) in hit.items() if tot}
    if d["lapisan"]["pangsa_perpanjangan_periode"] != pangsa:
        beda.append(f"pangsa {d['lapisan']['pangsa_perpanjangan_periode']} ≠ {pangsa}")

    if beda:
        lap.fail("stats-web", "dashboard-stats.json basi: " + "; ".join(beda) +
                 " → jalankan ulang scripts/gen_dashboard_stats.py")
    else:
        lap.ok("stats-web",
               f"wiup={n_geo}, loss_ha={loss:,}, persen_sawit={persen}, pangsa={pangsa} — semua cocok")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Verifikasi invarian DB analisis (baca-saja; lihat docstring).")
    ap.add_argument("--db", default="data/kalimantan.db", help="path DB SQLite (dibuka mode=ro)")
    ap.add_argument("--light", action="store_true",
                    help="hitungan + rujukan saja (utk data-full/ yang lapisan "
                         "sawit/klasifikasi/periode-nya cangkang kosong)")
    ap.add_argument("--stats", metavar="JSON",
                    help="path dashboard-stats.json utk cek kesegaran angka web")
    ap.add_argument("--expect-headline", type=float, default=SNAPSHOT_LOSS_TOTAL_HA,
                    help=f"jangkar Σ total_loss ha (default {SNAPSHOT_LOSS_TOTAL_HA:,})")
    ap.add_argument("--no-expect", action="store_true",
                    help="matikan pemeriksaan snapshot kanonik (825 & Σ loss)")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.is_file():
        print(f"[FAIL] db — {db} tidak ditemukan", file=sys.stderr)
        return 1
    # KETAT baca-saja: URI mode=ro — koneksi ini tak bisa menulis apa pun.
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    lap = Pelapor()
    pakai_snapshot = not args.no_expect and not args.light
    print(f"verify_invariants — {db} ({'light' if args.light else 'penuh'})")

    cek_hitungan(con, lap, SNAPSHOT_N_KONSESI if pakai_snapshot else None)
    if args.light:
        cek_referensial(con, lap, TABEL_ANAK_LIGHT)
        cek_non_negatif(con, lap, tabel_saja=TABEL_NON_NEGATIF_LIGHT)
        # cek_klasifikasi_silang() sendiri sudah skip-jika-absen per tabel (lihat
        # tabel_ada() di dalamnya) — data-full/ TAK punya periode_klasifikasi
        # (cangkang kosong, diverifikasi kosong: 0 tabel), TAPI PUNYA
        # baseline_tahunan berisi (1.985.283,08 ha) yang sebelumnya tak pernah
        # diverifikasi di --light. Temuan review M8.
        cek_klasifikasi_silang(con, lap)
        cek_atribusi(con, lap)
        cek_laju(con, lap)
        cek_backtrack(con, lap)
    else:
        if pakai_snapshot:
            cek_headline(con, lap, args.expect_headline)
        cek_jendela_descals(con, lap)
        cek_sawit(con, lap)
        cek_non_negatif(con, lap)
        cek_f15(con, lap)
        cek_klasifikasi(con, lap)
        cek_referensial(con, lap, TABEL_ANAK)
        cek_rekonsiliasi_periode(con, lap)
        cek_klasifikasi_silang(con, lap)
        cek_atribusi(con, lap)
        cek_laju(con, lap)
        cek_backtrack(con, lap)
        cek_jendela2009(con, lap)
        cek_metadata(con, lap)
        if args.stats:
            cek_stats(con, lap, args.stats)

    con.close()
    return lap.ringkas()


if __name__ == "__main__":
    sys.exit(main())
