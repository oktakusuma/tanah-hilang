#!/usr/bin/env python3
"""Bangun tabel analisis 3-PERIODE ke SQLite (data acuan, reproducible).

Periode kewenangan izin (dari `iup_year`):
    <2009 Pra-2009 | 2009-2014 R1 | 2015-2019 R2 | 2020-2025 R3
Jendela: tahun terbit izin 1998-2025. Konsesi iup_year 2026 (4, semua R3) & tanpa
tahun (7) DIBUANG. Deforestasi (Hansen) diamati 2001-2025.

Tabel yang dibuat (semua di data/kalimantan.db):
  1. periode_ringkasan          — 1 baris/periode: n, luas (total/mean/median), loss,
                                hutan-2000, %poligon, laju pasca (mean/median),
                                %akselerasi, korelasi (r luas·loss, r luas·rate_post).
  3. periode_slope              — 1 baris/periode: slope OLS loss~year, r2, puncak.
                                rel_year = tahun kalender − iup_year (perbandingan adil).
  5. periode_komoditas          — periode × grup komoditas (BATUBARA vs MINERAL LOGAM):
                                n, luas, loss, %poligon, %akselerasi, laju pasca median.
  7. periode_signifikansi       — uji beda antar periode R1/R2/R3: Kruskal-Wallis +
                                pairwise Mann-Whitney U (butuh scipy; skip jika absen).
  8. analysis_meta            — PROVENANCE tiap tabel (sumber + metode + script).

Sumber kolom: wiup_geoportal(iup_year,luas_sk,pejabat), wiup_loss(polygon_area_ha,
loss_2001_2025_ha), wiup_temporal(verdict,rate_tahun_izin_sampai_2025_ha_per_year), wiup_loss_yearly(year,loss_ha).

Jalankan SETELAH data/kalimantan.db terbentuk (mis. process.sh, sesudah filter_minerba).
Idempotent. Tak butuh dependensi eksternal (stdlib saja).

    python3 scripts/build_periode_tables.py --db data/kalimantan.db
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics


# Jendela izin yang dianalisis: tahun terbit 1998–2025.
# Konsesi ber-iup_year 2026 (4 konsesi, semua R3) & tanpa-tahun (7) DIBUANG agar
# batas periode jelas dan tak ada "out-of-range" yang membingungkan.
IUP_YEAR_MIN = 1998
IUP_YEAR_MAX = 2025


def to_periode(y):
    # Jendela izin 1998–2025 ditegakkan di KEDUA batas (simetris): izin <1998 &
    # >2025 dibuang, agar re-scrape yang memunculkan izin di luar jendela tak
    # diam-diam membengkakkan kohort tanpa error.
    if y is None or y < IUP_YEAR_MIN or y > IUP_YEAR_MAX:
        return None
    if y < 2009:
        return "Pra-2009"
    if y <= 2014:
        return "P1"
    if y <= 2019:
        return "P2"
    return "P3"  # 2020–2025


PERIODES = ["P1", "P2", "P3", "Pra-2009"]
RENTANG = {"P1": "2009-2014", "P2": "2015-2019", "P3": "2020-2025", "Pra-2009": "<2009"}
ACCEL_VERDICTS = ("accelerated_post_iup", "loss_only_after_iup")

# Periode yang IKUT uji beda (Pra-2009 = catatan kaki, dikecualikan dari semua
# uji — n=29 dan 0 di antaranya perpanjangan, jadi barisnya separuh kosong).
PERIODES_UJI = ["P1", "P2", "P3"]
# Kelas lapisan pemeriksa "klasifikasi izin". TAK_DINILAI ikut ditulis supaya
# Σ n per periode = n periode di periode_ringkasan (bisa direkonsiliasi).
KELAS_IZIN = ["IZIN_PERTAMA", "PERPANJANGAN", "TAK_DINILAI"]

YEAR_MIN, YEAR_MAX = 2001, 2025

# Jendela varian BERSIH (Task F1, FASE F): loss dipotong perkiraan konversi
# sawit (atribusi_sawit, varian tol2th/UTAMA), dibatasi tahun 2001-2021 —
# Descals dkk. (2024) berhenti 2021, jadi 2022-2025 TAK BISA diperiksa thd
# sawit sama sekali dan DIBUANG SELURUHNYA dari varian ini (bukan cuma
# sawit-nya yg diabaikan; lihat FASE F di docs/superpowers/plans/2026-08-04-
# descall-lapisan.md). "_bersih" = "bersih dari sawit", BUKAN singkatan lain.
YEAR_MAX_BERSIH = 2021
BERSIH_SUFFIX = "_bersih"


# Kamus kolom (data dictionary). (nama_tabel, nama_kolom, deskripsi, rumus|None, sumber|None).
# Kolom turunan/analisis diisi lengkap (rumus+sumber); kolom mentah/jelas-sendiri cukup deskripsi.
# Rumus diturunkan dari analysis_meta.metode + docstring pipeline (build_combined_db.py,
# temporal_iup.py, batch_analyze.py, match_harder.py) — bukan dikarang.
COLUMN_META = [
    # ── periode_ringkasan: 1 baris/periode (P1/P2/P3/Pra-2009) ─────────────────
    ("periode_ringkasan", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_ringkasan", "rentang_tahun", "Rentang tahun kalender periode kewenangan (mis. '2009-2014').",
     None, "konstanta RENTANG per periode"),
    ("periode_ringkasan", "n", "Jumlah konsesi WIUP dalam periode (setelah filter jendela izin 1998-2025).",
     "count(kode_wiup) dengan to_periode(iup_year) = periode", "wiup_geoportal.iup_year"),
    ("periode_ringkasan", "luas_total_ha", "Total luas SK (ha) seluruh konsesi periode.",
     "Σ luas_sk", "wiup_geoportal.luas_sk"),
    ("periode_ringkasan", "luas_mean_ha", "Rata-rata luas SK per konsesi periode.",
     "Σ luas_sk / n", "wiup_geoportal.luas_sk"),
    ("periode_ringkasan", "luas_median_ha", "Median luas SK konsesi periode (tahan-outlier; distribusi luas sangat skew).",
     "median(luas_sk)", "wiup_geoportal.luas_sk"),
    ("periode_ringkasan", "loss_2001_2025_ha", "Total kehilangan tutupan pohon (ha) seluruh konsesi periode, 2001-2025 (eks loss_total_ha — jendela masuk nama, DECISIONS 13 Agu).",
     "Σ loss_2001_2025_ha", "wiup_loss.loss_2001_2025_ha"),
    ("periode_ringkasan", "loss_2009_2025_ha", "Σ kehilangan jendela era Minerba 2009-2025 kohort ini (Fase B dual-window).", "Σ wiup_loss.loss_2009_2025_ha", "wiup_loss.loss_2009_2025_ha"),
    ("periode_ringkasan", "polygon_total_ha", "Total luas poligon konsesi hasil overlay raster Hansen (bisa beda tipis dari luas_sk dokumen SK).",
     "Σ polygon_area_ha", "wiup_loss.polygon_area_ha"),
    ("periode_ringkasan", "forest2000_total_ha", "Total tutupan pohon tahun 2000 di dalam konsesi periode.",
     "Σ forest_2000_ha", "wiup_loss.forest_2000_ha"),
    ("periode_ringkasan", "pct_poligon_2001_2025", "Persen luas poligon konsesi yang kehilangan tutupan pohon 2001-2025 (eks pct_poligon).",
     "100 · Σ loss_2001_2025_ha / Σ polygon_area_ha", "wiup_loss"),
    ("periode_ringkasan", "rate_tahun_izin_sampai_2025_mean", "Rata-rata laju deforestasi pasca-izin (ha/tahun) konsesi periode (eks rate_post_mean).",
     "mean(rate_tahun_izin_sampai_2025_ha_per_year)", "wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("periode_ringkasan", "rate_tahun_izin_sampai_2025_median", "Median laju deforestasi pasca-izin (ha/tahun) — tahan-outlier (eks rate_post_median).",
     "median(rate_tahun_izin_sampai_2025_ha_per_year)", "wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("periode_ringkasan", "pct_akselerasi", "Persen konsesi yang laju deforestasinya berakselerasi pasca-izin terbit.",
     "100 · count(verdict ∈ {accelerated_post_iup, loss_only_after_iup}) / n", "wiup_temporal.verdict"),
    ("periode_ringkasan", "r_luas_loss_2001_2025", "Korelasi Pearson antara luas SK konsesi dan total loss 2001-2025 (apakah konsesi lebih luas cenderung lebih banyak deforestasi).",
     "Pearson(luas_sk, loss_2001_2025_ha) per konsesi periode", "wiup_geoportal.luas_sk × wiup_loss.loss_2001_2025_ha"),
    ("periode_ringkasan", "r_luas_rate_tahun_izin_sampai_2025", "Korelasi Pearson antara luas SK konsesi dan laju deforestasi pasca-izin (eks r_luas_ratepost).",
     "Pearson(luas_sk, rate_tahun_izin_sampai_2025_ha_per_year) per konsesi periode", "wiup_geoportal.luas_sk × wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("periode_ringkasan", "komposisi_otoritas", "Komposisi pejabat penerbit izin (Bupati/Gubernur/Menteri) dalam periode, format 'penerbit:n' terurut menurun.",
     "count(kode_wiup) group by pejabat, per periode", "wiup_geoportal.pejabat"),

    # ── periode_slope: slope OLS deforestasi tahunan (basis since-permit) ───────
    ("periode_slope", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_slope", "slope_ha_per_year", "Laju tren deforestasi tahunan (ha/tahun) berbasis izin-aktif.",
     "OLS loss_ha ~ year, HANYA atas tahun sejak iup_year kohort (since-permit) — bukan jendela penuh 2001-2025 yang terkontaminasi loss pra-izin",
     "periode_tahunan_aktif (deret since-permit)"),
    ("periode_slope", "r2", "R² regresi OLS loss~year (since-permit) — proporsi variasi loss tahunan yang dijelaskan tren linear waktu.",
     "1 − SS_res/SS_tot dari regresi ols_slope()", "periode_tahunan_aktif"),
    ("periode_slope", "peak_year", "Tahun kalender dengan loss tahunan (since-permit) tertinggi dalam periode.",
     "argmax_tahun(loss_ha) atas deret since-permit", "periode_tahunan_aktif"),
    ("periode_slope", "peak_loss_ha", "Nilai loss (ha) pada peak_year.",
     "max(loss_ha) atas deret since-permit", "periode_tahunan_aktif"),

    # ── periode_tahunan_aktif: deret stok izin-aktif per periode-tahun ──────────
    ("periode_tahunan_aktif", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_tahunan_aktif", "year", "Tahun kalender (2001-2025).", None, "wiup_loss_yearly.year"),
    ("periode_tahunan_aktif", "loss_ha", "Loss (ha) tahun itu, HANYA dari konsesi yang izinnya sudah aktif (iup_year ≤ tahun) — flow tahunan, bukan kumulatif.",
     "Σ loss_ha izin aktif tahun itu", "wiup_loss_yearly × wiup_geoportal.iup_year"),
    ("periode_tahunan_aktif", "n_konsesi_aktif", "Jumlah konsesi berizin aktif pada tahun itu (iup_year ≤ tahun).",
     "count(kode_wiup) dengan iup_year ≤ tahun", "wiup_geoportal.iup_year"),
    ("periode_tahunan_aktif", "luas_aktif_ha", "Total luas SK konsesi izin-aktif pada tahun itu.",
     "Σ luas_sk izin aktif", "wiup_geoportal.luas_sk"),
    ("periode_tahunan_aktif", "forest_aktif_ha", "Total tutupan pohon 2000 di dalam konsesi izin-aktif pada tahun itu.",
     "Σ forest_2000_ha izin aktif", "wiup_loss.forest_2000_ha"),
    ("periode_tahunan_aktif", "loss_kumulatif_sejak_2001_ha", "Akumulasi loss pasca-izin sejak awal jendela 2001 s/d tahun itu (stok, bukan flow; eks loss_kumulatif_ha — awal jendela masuk nama; varian _bersih memakai nama sendiri loss_kumulatif_2001_sampai_2021_ha karena berhenti 2021).",
     "Σ_{y≤tahun} loss_ha (deret since-permit)", "kolom loss_ha tabel ini"),

    # ── penerbit_tahunan_aktif: sama dgn periode_tahunan_aktif, tapi lensa PENERBIT ─
    ("penerbit_tahunan_aktif", "penerbit", "Pejabat penerbit izin (BUPATI/GUBERNUR/MENTERI) — memperlihatkan cutoff kewenangan langsung.",
     None, "wiup_geoportal.pejabat"),
    ("penerbit_tahunan_aktif", "year", "Tahun kalender (2001-2025).", None, "wiup_loss_yearly.year"),
    ("penerbit_tahunan_aktif", "loss_ha", "Loss (ha) tahun itu, HANYA dari konsesi yang izinnya sudah aktif pada penerbit itu (iup_year ≤ tahun) — flow tahunan.",
     "Σ loss_ha izin aktif tahun itu, group by pejabat", "wiup_loss_yearly × wiup_geoportal.pejabat/iup_year"),
    ("penerbit_tahunan_aktif", "n_konsesi_aktif", "Jumlah konsesi berizin aktif pada tahun itu, per penerbit.",
     "count(kode_wiup) dengan iup_year ≤ tahun, group by pejabat", "wiup_geoportal.pejabat/iup_year"),
    ("penerbit_tahunan_aktif", "luas_aktif_ha", "Total luas SK konsesi izin-aktif pada tahun itu, per penerbit.",
     "Σ luas_sk izin aktif, group by pejabat", "wiup_geoportal.luas_sk"),
    ("penerbit_tahunan_aktif", "forest_aktif_ha", "Total tutupan pohon 2000 di dalam konsesi izin-aktif pada tahun itu, per penerbit.",
     "Σ forest_2000_ha izin aktif, group by pejabat", "wiup_loss.forest_2000_ha"),
    ("penerbit_tahunan_aktif", "loss_kumulatif_sejak_2001_ha", "Akumulasi loss pasca-izin sejak awal jendela 2001 s/d tahun itu, per penerbit (stok, bukan flow; eks loss_kumulatif_ha). Mencakup SEMUA iup_year 1998-2025 termasuk kohort Pra-2009 (Menteri KK/PKP2B).",
     "Σ_{y≤tahun} loss_ha (deret since-permit), per penerbit", "kolom loss_ha tabel ini"),

    # ── periode_komoditas: kontrol komoditas (BATUBARA vs MINERAL LOGAM) ───────
    ("periode_komoditas", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_komoditas", "grup_komoditas", "Grup komoditas untuk kontrol variabel: BATUBARA atau MINERAL LOGAM.",
     "BATUBARA jika komoditas diawali 'BATUBARA', selainnya MINERAL LOGAM", "wiup_geoportal.komoditas"),
    ("periode_komoditas", "n", "Jumlah konsesi grup-komoditas dalam periode.", "count(kode_wiup) per (periode, grup_komoditas)", "wiup_geoportal"),
    ("periode_komoditas", "luas_total_ha", "Total luas SK (ha) grup-komoditas dalam periode.", "Σ luas_sk", "wiup_geoportal.luas_sk"),
    ("periode_komoditas", "luas_median_ha", "Median luas SK grup-komoditas dalam periode.", "median(luas_sk)", "wiup_geoportal.luas_sk"),
    ("periode_komoditas", "loss_2001_2025_ha", "Total kehilangan tutupan pohon (ha) grup-komoditas dalam periode, 2001-2025 (eks loss_total_ha).", "Σ loss_2001_2025_ha", "wiup_loss.loss_2001_2025_ha"),
    ("periode_komoditas", "loss_2009_2025_ha", "Σ kehilangan jendela era Minerba 2009-2025 kohort ini (Fase B dual-window).", "Σ wiup_loss.loss_2009_2025_ha", "wiup_loss.loss_2009_2025_ha"),
    ("periode_komoditas", "pct_poligon_2001_2025", "Persen luas poligon grup-komoditas yang kehilangan tutupan pohon 2001-2025 (eks pct_poligon).",
     "100 · Σ loss_2001_2025_ha / Σ polygon_area_ha", "wiup_loss"),
    ("periode_komoditas", "rate_tahun_izin_sampai_2025_median", "Median laju deforestasi pasca-izin (ha/tahun) grup-komoditas (eks rate_post_median).",
     "median(rate_tahun_izin_sampai_2025_ha_per_year)", "wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("periode_komoditas", "pct_akselerasi", "Persen konsesi grup-komoditas yang laju deforestasinya berakselerasi pasca-izin.",
     "100 · count(verdict ∈ {accelerated_post_iup, loss_only_after_iup}) / n", "wiup_temporal.verdict"),

    # ── periode_klasifikasi: matriks periode × kelas izin ──────────────────────
    ("periode_klasifikasi", "periode", "Kode periode kewenangan izin (P1/P2/P3). Pra-2009 dikecualikan.", None, "periode(iup_year)"),
    ("periode_klasifikasi", "kelas", "Kelas klasifikasi izin: IZIN_PERTAMA (konsisten dgn pemberian pertama, BUKAN terbukti) / PERPANJANGAN (payung 'bukan pemberian pertama') / TAK_DINILAI.", None, "klasifikasi_izin.kelas"),
    ("periode_klasifikasi", "n", "Jumlah konsesi di sel periode × kelas. Konsesi tanpa baris klasifikasi_izin dihitung TAK_DINILAI agar Σ sel = n periode.", "count(kode_wiup) per (periode, kelas)", "wiup_geoportal × klasifikasi_izin"),
    ("periode_klasifikasi", "n_akselerasi", "Jumlah konsesi sel yang laju deforestasinya berakselerasi pasca-izin.", "count(verdict ∈ {accelerated_post_iup, loss_only_after_iup})", "wiup_temporal.verdict"),
    ("periode_klasifikasi", "pct_akselerasi", "Persen konsesi sel yang berakselerasi pasca-izin. NULL bila sel kosong (bukan 0).", "100 · n_akselerasi / n", "wiup_temporal.verdict"),
    ("periode_klasifikasi", "rate_tahun_izin_sampai_2025_median", "Median laju deforestasi pasca-izin (ha/tahun) di sel (eks rate_post_median).", "median(rate_tahun_izin_sampai_2025_ha_per_year)", "wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("periode_klasifikasi", "rate_tahun_izin_sampai_2025_mean", "Rata-rata laju deforestasi pasca-izin (ha/tahun) di sel (eks rate_post_mean).", "mean(rate_tahun_izin_sampai_2025_ha_per_year)", "wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("periode_klasifikasi", "loss_2001_2025_ha", "Total kehilangan tutupan pohon (ha) seluruh konsesi di sel, 2001-2025 (eks total_loss_ha).", "Σ loss_2001_2025_ha", "wiup_loss.loss_2001_2025_ha"),
    ("periode_klasifikasi", "loss_2009_2025_ha", "Σ kehilangan jendela era Minerba 2009-2025 kohort ini (Fase B dual-window).", "Σ wiup_loss.loss_2009_2025_ha", "wiup_loss.loss_2009_2025_ha"),
    ("periode_klasifikasi", "luas_sk_ha", "Total luas SK (ha) konsesi di sel.", "Σ luas_sk", "wiup_geoportal.luas_sk"),

    # ── periode_klasifikasi_uji: beda antar periode DI DALAM tiap kelas ────────
    ("periode_klasifikasi_uji", "kelas", "Kelas izin tempat perbandingan dilakukan (stratum).", None, "klasifikasi_izin.kelas"),
    ("periode_klasifikasi_uji", "periode_a", "Periode pertama yang dibandingkan.", None, "periode(iup_year)"),
    ("periode_klasifikasi_uji", "periode_b", "Periode kedua yang dibandingkan.", None, "periode(iup_year)"),
    ("periode_klasifikasi_uji", "n_a", "Jumlah konsesi periode_a di stratum ini.", "count(kode_wiup)", "periode_klasifikasi.n"),
    ("periode_klasifikasi_uji", "n_b", "Jumlah konsesi periode_b di stratum ini.", "count(kode_wiup)", "periode_klasifikasi.n"),
    ("periode_klasifikasi_uji", "pct_a", "Persen akselerasi periode_a di stratum ini.", "100 · n_akselerasi / n", "periode_klasifikasi.pct_akselerasi"),
    ("periode_klasifikasi_uji", "pct_b", "Persen akselerasi periode_b di stratum ini.", "100 · n_akselerasi / n", "periode_klasifikasi.pct_akselerasi"),
    ("periode_klasifikasi_uji", "p_value", "Nilai-p Fisher exact dua-sisi untuk beda proporsi akselerasi. NULL bila scipy absen atau salah satu grup kosong.", "scipy.stats.fisher_exact([[acc_a, n_a−acc_a], [acc_b, n_b−acc_b]])", "wiup_temporal.verdict"),
    ("periode_klasifikasi_uji", "signifikan_005", "1 bila p < 0,05. Kuasa uji rendah di sel kecil — 'tak nyata' BUKAN 'terbukti sama'.", "p_value < 0.05", "kolom p_value tabel ini"),
    ("periode_klasifikasi_uji", "metode", "Nama uji yang dipakai ('fisher_exact_two_sided'). Fisher dipilih karena sel terkecil hanya 8 kejadian dari 24 — chi-square tak sah di situ.", None, "konstanta builder"),

    # ── baseline_tahunan: deret seluruh konsesi, tanpa pembagian periode ───────
    ("baseline_tahunan", "year", "Tahun kalender 2001-2025 (Hansen mencatat kehilangan mulai 2001; 2000 adalah basis tutupan hutan, bukan tahun kehilangan).", None, "wiup_loss_yearly.year"),
    ("baseline_tahunan", "loss_ha", "Kehilangan tutupan pohon (ha) tahun itu di SELURUH konsesi — penyebutnya sengaja berbeda dari tabel periode_* (tanpa filter jendela izin).", "Σ loss_ha seluruh konsesi per tahun", "wiup_loss_yearly.loss_ha"),
    ("baseline_tahunan", "n_konsesi", "Jumlah konsesi yang mencatat kehilangan > 0 pada tahun itu.", "count(distinct kode_wiup) dgn loss_ha > 0", "wiup_loss_yearly"),

    # ── atribusi_izin_aktif: atribusi loss ke izin aktif, jendela 2009-2025 ────
    # Bentuk BARIS (unpivot Fase G 15 Agu): 1 baris per (konsesi, aturan) —
    # eks aturan-jadi-kolom (mulai_b/c/d). Aturan C/PERKIRAAN diarsipkan 15 Agu
    # (data lama di riwayat git — lihat DECISIONS.md).
    ("atribusi_izin_aktif", "kode_wiup", "Kode WIUP konsesi (semua 825 konsesi × 3 aturan, termasuk yang keluar kohort — utk audit).", None, "wiup_geoportal.kode_wiup"),
    ("atribusi_izin_aktif", "aturan", "Aturan atribusi baris ini: TANPA_ATRIBUSI (eks X0 — semua loss 2009-2025, pembanding) / INDIKASI (eks B — perpanjangan aktif sepanjang jendela) / POLOS (eks D — semua sejak max(2009, tahun SK)). Kosakata sama dgn backtrack_*.aturan; aturan C/PERKIRAAN diarsipkan 15 Agu.", None, "konstanta builder"),
    ("atribusi_izin_aktif", "kelas", "Kelas klasifikasi izin (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI; tanpa baris klasifikasi = TAK_DINILAI).", None, "klasifikasi_izin.kelas"),
    ("atribusi_izin_aktif", "bukti", "Kekuatan bukti klasifikasi (KUAT/INDIKASI/NULL).", None, "klasifikasi_izin.bukti"),
    ("atribusi_izin_aktif", "iup_year", "Tahun terbit SK izin yang tercatat.", None, "wiup_geoportal.iup_year"),
    ("atribusi_izin_aktif", "mulai", "Tahun pertama loss dihitung menurut `aturan` baris ini: TANPA_ATRIBUSI selalu 2009; INDIKASI = 2009 utk perpanjangan, max(2009, iup_year) lainnya; POLOS = max(2009, iup_year). NULL = keluar kohort (non-perpanjangan tanpa iup_year).", "hitung_mulai()", "scripts/build_atribusi_izin.py"),
    ("atribusi_izin_aktif", "loss_mulai_sampai_2025_ha", "Loss teratribusi jendela [mulai, 2025] versi `aturan` baris ini (jangkar = kolom mulai baris yang sama). NULL bila mulai NULL (keluar kohort — tak terdefinisi, bukan 0).", "Σ loss_ha year ≥ mulai", "wiup_loss_yearly"),


    # ── atribusi_izin_aktif_ringkas ────────────────────────────────────────────
    ("atribusi_izin_aktif_ringkas", "aturan", "Aturan atribusi: TANPA_ATRIBUSI (eks X0 — pembanding/plafon) / INDIKASI (eks B) / POLOS (eks D — batas bawah). Selaras dgn kosakata backtrack_*; aturan C/PERKIRAAN diarsipkan 15 Agu.", None, "konstanta builder"),
    ("atribusi_izin_aktif_ringkas", "label", "Deskripsi satu kalimat aturan (Bahasa Indonesia).", None, "konstanta ATURAN_LABEL"),
    ("atribusi_izin_aktif_ringkas", "loss_mulai_aturan_sampai_2025_ha", "Total loss teratribusi aturan ini (jendela [mulai versi aturan, 2025], di dalam era Minerba 2009-2025; eks loss_ha — jangkar = kolom aturan baris ini).", "Σ loss per aturan", "atribusi_izin_aktif"),
    ("atribusi_izin_aktif_ringkas", "pct_hutan2009", "Persen terhadap hutan yang masih berdiri awal 2009 (= hutan-2000 − loss 2001-2008; identitas eksak, bukan estimasi).", "100·loss/(Σforest_2000 − Σloss_2001_2008)", "wiup_loss × wiup_loss_yearly"),
    ("atribusi_izin_aktif_ringkas", "n_kohort", "Jumlah konsesi ber-mulai tidak-NULL utk aturan ini (TANPA_ATRIBUSI = semua konsesi).", "count(mulai NOT NULL)", "atribusi_izin_aktif"),


    # ── laju_izin_konsesi: laju deforestasi per jam izin (pivot laju-dulu) ──────
    ("laju_izin_konsesi", "kode_wiup", "Kode WIUP konsesi (semua 825 baris; keluar kohort = mulai NULL, utk audit).", None, "atribusi_izin_aktif.kode_wiup"),
    ("laju_izin_konsesi", "kelas", "Kelas klasifikasi izin (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI).", None, "atribusi_izin_aktif.kelas"),
    ("laju_izin_konsesi", "bukti", "Kekuatan bukti klasifikasi (KUAT/INDIKASI/NULL).", None, "atribusi_izin_aktif.bukti"),
    ("laju_izin_konsesi", "iup_year", "Tahun terbit SK izin yang tercatat.", None, "atribusi_izin_aktif.iup_year"),
    ("laju_izin_konsesi", "periode", "Periode kewenangan menurut iup_year (Pra-2009/P1/P2/P3; NULL bila di luar jendela 1998-2025).", "to_periode(iup_year)", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "mulai", "Tahun konsesi ini mulai dihitung aktif, aturan E (bukti lapangan): min(max(2009, tahun_bukti), max(2009, iup_year)) — selalu >= 2009 karena jendela hitung era UU Minerba. NULL = keluar kohort (tanpa bukti & tanpa tahun izin dalam jendela).", "min(klem2009(bukti), klem2009(izin))", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "dasar_mulai", "Mana yang menang: 'BUKTI' (pembukaan non-sawit terlihat sebelum SK — backtrack) atau 'IZIN' (jam = tahun SK, clamp 2009).", None, "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "tahun_bukti", "Tahun pertama [2001, 2021] dgn loss non-sawit ≥ 1 ha (ambang bukti). BOLEH pra-2009: pembukaan 2003 di poligon ber-SK 2017 adalah bukti tambangnya sudah ada (SK itu perpanjangan) — dulu jendela ini keliru dipatok 2009 sehingga kasus begitu jatuh ke tahun SK dan kehilangan 2009..SK tak terhitung. Yang diklem ke 2009 hanya kolom `mulai`, kolom ini apa adanya. 2022-2025 tak bisa diverifikasi non-sawit — tak pernah jadi bukti.", "tahun pertama loss−sawit ≥ 1 ha", "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("laju_izin_konsesi", "hutan_mulai_aktif_ha", "Stok hutan saat jam mulai: forest_2000 − Σ loss 2001..(mulai−1). Penyebut laju %/thn.", "forest_2000 − Σ loss pra-mulai", "wiup_loss × wiup_loss_yearly"),
    ("laju_izin_konsesi", "n_tahun_dari_mulai_aktif_sampai_2025", "Panjang jendela kotor: 2025 − mulai + 1 tahun.", "2025 − mulai + 1", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "loss_mulai_aktif_sampai_2025_ha", "Loss Hansen di jendela [mulai, 2025] (basis kotor — TANPA pemotongan sawit).", "Σ loss_ha, year ∈ [mulai, 2025]", "wiup_loss_yearly"),
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2025_ha_thn", "Metrik (a) basis kotor: loss_kotor / n_tahun_dari_mulai_aktif_sampai_2025.", "loss_mulai_aktif_sampai_2025_ha / n_tahun_dari_mulai_aktif_sampai_2025", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2025_pct_thn", "Metrik (b) basis kotor: 100·laju_mulai_aktif_sampai_2025_ha_thn / hutan_mulai_aktif_ha (NULL bila hutan_mulai ≤ 0).", "100·laju/hutan_mulai", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "n_tahun_dari_mulai_aktif_sampai_2021", "Panjang jendela bersih: 2021 − mulai + 1 tahun (NULL bila mulai > 2021 — batas peta Descals).", "2021 − mulai + 1", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "Loss BERSIH sawit di jendela [mulai, 2021]: Σ max(0, loss − sawit_tol2th) per tahun. Basis UTAMA (Descals first-class).", "Σ max(0, loss − sawit)", "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2021_tanpa_sawit_ha_thn", "Metrik (a) basis bersih: loss_bersih / n_tahun_dari_mulai_aktif_sampai_2021.", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha / n_tahun_dari_mulai_aktif_sampai_2021", "scripts/build_laju_izin.py"),
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn", "Metrik (b) basis bersih: 100·laju_mulai_aktif_sampai_2021_tanpa_sawit_ha_thn / hutan_mulai_aktif_ha.", "100·laju/hutan_mulai", "scripts/build_laju_izin.py"),

    # ── kolom jendela era Minerba di wiup_loss (Fase B — dual-window) ───────────
    ("wiup_loss", "loss_2001_2008_ha", "Kehilangan pra-jendela (2001-2008) — konteks, di luar era Minerba.", "Σ loss per-tahun 2001-2008 (kolom CSV batch)", "batch_KALIMANTAN_t30_wide.csv"),
    ("wiup_loss", "hutan_2009_ha", "Hutan yang masih berdiri awal 2009: forest_2000 − loss 2001-2008 (identitas eksak).", "forest_2000 − loss_2001_2008", "wiup_loss"),
    ("wiup_loss", "loss_2009_2025_ha", "Kehilangan tutupan pohon di jendela era Minerba 2009-2025 — kolom utama Fase B.", "Σ loss per-tahun 2009-2025", "batch_KALIMANTAN_t30_wide.csv"),
    ("wiup_loss", "loss_2009_2025_pct_hutan2009", "Persen kehilangan 2009-2025 terhadap hutan-2009 (NULL bila hutan_2009 ≤ 0; eks loss_pct_hutan2009 — jendela pembilang masuk nama).", "100·loss_2009_2025/hutan_2009", "wiup_loss"),

    # ── laju_izin_ringkas: distribusi laju per basis × dimensi × kelompok ───────
    ("laju_izin_ringkas", "basis", "Basis hitung: 'bersih' (Hansen − sawit, ≤2021; UTAMA) atau 'kotor' (Hansen penuh, ≤2025).", None, "scripts/build_laju_izin.py"),
    ("laju_izin_ringkas", "dimensi", "Cara mengiris populasi: 'semua', 'kelas' (klasifikasi izin), 'kohort' (kohort tahun-terbit-SK P1-P3 — eks nilai 'periode', koreksi Fase T 16 Agu: isinya memang kohort SK n 239/262/284, tertinggal dari rename Fase G).", None, "scripts/build_laju_izin.py"),
    ("laju_izin_ringkas", "kelompok", "Nilai irisan: SEMUA / IZIN_PERTAMA / PERPANJANGAN / TAK_DINILAI / P1 / P2 / P3.", None, "laju_izin_konsesi"),
    ("laju_izin_ringkas", "n", "Jumlah konsesi kelompok ini yang lajunya terdefinisi di basis tsb.", "count", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "n_pct", "Subset n yang laju %/thn-nya terdefinisi (hutan_mulai > 0).", "count pct NOT NULL", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "total_loss_ha", "Σ loss basis tsb utk kelompok ini.", "Σ loss_basis_ha", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "median_ha_thn", "Median laju ha/tahun.", "persentil-50 interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "mean_ha_thn", "Rata-rata laju ha/tahun.", "mean", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "p25_ha_thn", "Persentil-25 laju ha/tahun.", "persentil interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "p75_ha_thn", "Persentil-75 laju ha/tahun.", "persentil interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "p90_ha_thn", "Persentil-90 laju ha/tahun.", "persentil interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "median_pct_thn", "Median laju %/tahun (atas subset n_pct).", "persentil-50 interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "mean_pct_thn", "Rata-rata laju %/tahun.", "mean", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "p25_pct_thn", "Persentil-25 laju %/tahun.", "persentil interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "p75_pct_thn", "Persentil-75 laju %/tahun.", "persentil interpolasi linier", "laju_izin_konsesi"),
    ("laju_izin_ringkas", "p90_pct_thn", "Persentil-90 laju %/tahun.", "persentil interpolasi linier", "laju_izin_konsesi"),

    # ── konsesi_aktif_tahunan: BERAPA konsesi aktif tiap tahun (pendamping baseline_tahunan) ──
    ("konsesi_aktif_tahunan", "year", "Tahun kalender 2001-2025.", None, None),
    ("konsesi_aktif_tahunan", "n_mulai_aktif",
     "Jumlah KUMULATIF konsesi yang tahun mulai aktifnya (aturan E, metode Deteksi Hansen "
     "— codename DB: CITRA) sudah "
     "tercapai pada tahun itu. NULL utk tahun < 2009 — BUKAN nol: aturan mulai-aktif hanya "
     "berlaku sejak 2009 (jendela era Minerba), jadi angka nol di 2005 akan terbaca sebagai "
     "klaim 'tak ada konsesi aktif', padahal itu cuma batas aturan.",
     "count(mulai <= year)", "laju_izin_konsesi.mulai"),
    ("konsesi_aktif_tahunan", "n_sk_terbit",
     "Jumlah KUMULATIF konsesi yang SK-nya sudah terbit pada tahun itu (iup_year <= year). "
     "Ditulis sejak 2001 karena 29 konsesi ber-iup_year pra-2009.",
     "count(iup_year <= year)", "wiup_geoportal.iup_year"),
    ("konsesi_aktif_tahunan", "n_aktif_sebelum_sk",
     "Jumlah konsesi yang pada tahun itu SUDAH aktif menurut Deteksi Hansen TAPI SK-nya belum "
     "terbit (atau tak tercatat) — inilah besaran 'backtrack' yang terlihat. Dihitung "
     "LANGSUNG per konsesi, bukan selisih dua agregat (konsesi tanpa iup_year akan bikin "
     "selisih menyesatkan).",
     "count(mulai <= year AND (iup_year IS NULL OR iup_year > year))",
     "laju_izin_konsesi × wiup_geoportal"),

    # ── backtrack_*: pembanding 3 metode penentuan tahun mulai (kunci kolom `aturan`) ──
    # Penanda jangkar kolom `..._mulai_...` = tahun mulai VERSI `aturan` di baris
    # yang sama: CITRA=laju_izin_konsesi.mulai (label UI "Deteksi Hansen", UTAMA), INDIKASI/
    # POLOS=atribusi_izin_aktif.mulai baris aturan yang sama. Baris CITRA diikat
    # invarian cek_backtrack agar identik dgn tabel utama.
    ("backtrack_tahunan", "aturan", "Metode penentuan tahun mulai: CITRA (codename internal; label UI & narasi tesis = 'Deteksi Hansen' — tahun pertama produk Hansen GFC mencatat tree-cover loss non-sawit ≥ 1 ha di poligon; metode UTAMA) / INDIKASI (kelas izin; perpanjangan → 2009) / POLOS (tanpa backtrack — murni max(2009, tahun SK)). Kode 'CITRA' sengaja DIPERTAHANKAN di DB (keputusan igoen 15 Agu) walau labelnya berganti — bukan berarti kami menafsirkan citra satelit sendiri. Aturan C/PERKIRAAN diarsipkan 15 Agu: cara baca aditif membuatnya ≡ INDIKASI. Tabel ini juga memuat baris BEBAS METODE aturan='SEMUA' (Fase T 16 Agu): seluruh 825 konsesi dianggap aktif sejak 2009 tanpa atribusi apa pun — PENYEBUT bersama slide Temuan (1.228.077 ha, angka yang tak bergantung metode). 'SEMUA' BUKAN metode keempat; seluruh konsumen UI memfilter aturan = metode terpilih ∈ {CITRA, INDIKASI, POLOS}.", None, "scripts/build_laju_izin.py"),
    ("backtrack_tahunan", "year", "Tahun kalender 2001-2025.", None, None),
    ("backtrack_tahunan", "n_aktif", "Kumulatif konsesi yang tahun mulainya (versi `aturan`) <= tahun ini. NULL utk tahun < 2009 (jendela hitung era Minerba — bukan nol).", "count(mulai_aturan <= year)", "scripts/build_laju_izin.py"),
    ("backtrack_tahunan", "n_sk_terbit", "Kumulatif konsesi ber-iup_year <= tahun ini (sama utk semua aturan).", "count(iup_year <= year)", "wiup_geoportal.iup_year"),
    ("backtrack_tahunan", "n_aktif_sebelum_sk", "Konsesi aktif (versi `aturan`) yang SK-nya belum terbit/tak tercatat pada tahun ini — dihitung per konsesi, bukan selisih agregat.", "count(mulai<=year AND (iup_year IS NULL OR iup_year>year))", "scripts/build_laju_izin.py"),
    ("backtrack_tahunan", "loss_ha", "Loss Hansen tahun ini dari konsesi yang SUDAH aktif versi `aturan` (flow). NULL utk tahun < 2009.", "Σ loss_ha konsesi aktif", "wiup_loss_yearly"),
    ("backtrack_tahunan", "loss_tanpa_sawit_ha", "Sama, dikurangi bagian sawit per tahun (max(0, loss−sawit)); NULL utk tahun > 2021 (batas Descals) atau < 2009.", "Σ max(0, loss−sawit)", "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("backtrack_tahunan", "hutan_awal_tahun_ha", "Stok hutan yang masih berdiri AWAL tahun ini di konsesi yang sudah aktif (versi aturan): Σ (forest_2000 − loss 2001..thn−1). Penyebut laju %/tahun. NULL utk tahun < 2009.", "Σ (forest_2000 − Σ loss<y)", "wiup_loss × wiup_loss_yearly"),
    ("backtrack_tahunan", "pct_hutan_per_thn", "LAJU tahun itu: 100 · loss_ha / hutan_awal_tahun_ha — persen hutan-berdiri yang hilang dalam setahun. Ditambah Fase T (16 Agu) supaya grafik irama tahunan tak perlu membagi di browser (Konvensi #6). Pakai kolom ini, BUKAN loss_ha, saat membandingkan tahun/periode: deret hektare mentah ikut naik hanya karena jumlah & luas konsesi aktif bertambah. NULL utk tahun < 2009 atau stok 0.", "100·loss_ha/hutan_awal_tahun_ha", None),
    # backtrack_periode_kalender — REDEFINISI periode (igoen 15 Agu): P1/P2/P3 =
    # jendela TAHUN KALENDER murni, bukan kohort tahun-terbit-SK. Loss jendela =
    # Σ flow backtrack_tahunan tahun-tahun itu dari konsesi AKTIF versi aturan.
    ("backtrack_periode_kalender", "aturan", "Metode penentuan tahun mulai (CITRA/INDIKASI/POLOS) — lihat backtrack_tahunan.aturan. Tabel ini juga memuat baris BEBAS METODE aturan='SEMUA' (Fase T 16 Agu): seluruh 825 konsesi dianggap aktif sejak 2009 tanpa atribusi, dipakai sbg PENYEBUT bersama slide Temuan. 'SEMUA' bukan metode keempat — konsumen UI memfilter aturan = metode terpilih.", None, None),
    ("backtrack_periode_kalender", "periode", "Jendela TAHUN KALENDER (P1 2009-2014 / P2 2015-2019 / P3 2020-2025) — redefinisi 15 Agu: BUKAN kohort tahun-terbit-SK; statistik kohort-SK tetap di backtrack_kohort (eks backtrack_periode).", None, None),
    ("backtrack_periode_kalender", "tahun_awal", "Tahun kalender pertama jendela (P1=2009, P2=2015, P3=2020).", None, None),
    ("backtrack_periode_kalender", "tahun_akhir", "Tahun kalender terakhir jendela (P1=2014, P2=2019, P3=2025).", None, None),
    ("backtrack_periode_kalender", "loss_ha", "Loss Hansen PADA tahun-tahun jendela ini dari konsesi yang sudah AKTIF versi `aturan` (flow, bukan kumulatif-sejak-mulai).", "Σ backtrack_tahunan.loss_ha, year ∈ [tahun_awal, tahun_akhir]", "backtrack_tahunan"),
    ("backtrack_periode_kalender", "loss_tanpa_sawit_sampai_2021_ha", "Varian tanpa-sawit (eks loss_tanpa_sawit_ha — batas 2021 masuk nama): Σ max(0, loss−sawit) hanya utk tahun TERPERIKSA [tahun_awal, min(tahun_akhir, 2021)] — peta Descals berhenti 2021, jadi P3 hanya terperiksa 2020-2021 (sisanya di loss_2022_2025_belum_terperiksa_ha); P1/P2 terperiksa penuh. NULL bila lapisan sawit absen atau seluruh jendela > 2021.", "Σ backtrack_tahunan.loss_tanpa_sawit_ha, year ∈ [awal, min(akhir, 2021)]", "backtrack_tahunan"),
    ("backtrack_periode_kalender", "loss_2022_2025_belum_terperiksa_ha", "Loss Hansen bagian jendela DI ATAS batas Descals (2022-2025) — tak bisa diperiksa sawit (eks loss_belum_terperiksa_ha — jendelanya masuk nama). 0 utk P1/P2 (seluruh rentangnya ≤ 2021), > 0 hanya mungkin di P3.", "Σ backtrack_tahunan.loss_ha, year ∈ [max(awal, 2022), akhir]", "backtrack_tahunan"),
    ("backtrack_periode_kalender", "n_aktif_akhir", "KUMULATIF s.d. tahun_akhir: jumlah konsesi yang sudah aktif (versi `aturan`) kapan pun sampai tahun_akhir jendela — snapshot backtrack_tahunan.n_aktif di year = tahun_akhir, BUKAN cacah per-jendela.", "backtrack_tahunan.n_aktif @ year=tahun_akhir", "backtrack_tahunan"),
    ("backtrack_periode_kalender", "luas_aktif_total_ha", "KUMULATIF s.d. tahun_akhir: total luas SK WILAYAH AKTIF — himpunan {konsesi dgn mulai versi `aturan` <= tahun_akhir}, aktif kapan pun s.d. akhir jendela, bukan hanya SK yang terbit pada rentang. Di POLOS himpunan ini ≈ kohort SK; di CITRA hampir identik antar jendela (hampir semua aktif sejak 2009) — temuan, bukan bug.", "Σ luas_sk atas {mulai <= tahun_akhir}", "wiup_geoportal.luas_sk"),
    ("backtrack_periode_kalender", "mean_luas_aktif_ha", "KUMULATIF s.d. tahun_akhir: rata-rata luas SK konsesi wilayah aktif (himpunan kumulatif yang sama dgn luas_aktif_total_ha; n = n_aktif_akhir).", "luas_aktif_total_ha / n_aktif_akhir", "wiup_geoportal.luas_sk"),
    ("backtrack_periode_kalender", "median_luas_aktif_ha", "KUMULATIF s.d. tahun_akhir: median luas SK konsesi wilayah aktif (himpunan kumulatif yang sama).", "median luas_sk atas {mulai <= tahun_akhir}", "wiup_geoportal.luas_sk"),
    ("backtrack_periode_kalender", "gini_luas_aktif", "KUMULATIF s.d. tahun_akhir: indeks Gini luas SK wilayah aktif (0 = merata, 1 = terkonsentrasi penuh); rumus selisih-berpasangan, NULL bila n<2 atau Σ=0.", "(2Σi·xᵢ)/(nΣx) − (n+1)/n atas luas_sk terurut, himpunan {mulai <= tahun_akhir}", "scripts/build_laju_izin.py"),
    # backtrack_kohort (eks backtrack_periode — rename Fase G 15 Agu): kolom
    # `kohort` = KOHORT tahun-terbit-SK, dipisah tegas dari `periode` milik
    # backtrack_periode_kalender (jendela kalender murni).
    ("backtrack_kohort", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS) — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_kohort", "kohort", "Kohort tahun-terbit-SK menurut iup_year (Pra-2009/P1/P2/P3; TANPA_PERIODE = iup_year kosong/di luar 1998-2025 — ember rekonsiliasi, UI tak merendernya). Eks kolom `periode` — rename Fase G supaya tak tabrakan makna dgn jendela kalender.", None, None),
    ("backtrack_kohort", "n", "Konsesi kohort ini (ber-iup_year 1998-2025).", None, None),
    ("backtrack_kohort", "n_mulai", "Subset n yang tahun mulainya terdefinisi (<= 2025) di aturan ini (INDIKASI/POLOS butuh iup_year).", None, None),
    ("backtrack_kohort", "loss_mulai_aktif_sampai_2025_ha", "Σ loss Hansen per konsesi pada jendela [mulai aktif versi aturan, 2025] (penanda mulai_aktif — DECISIONS 13 Agu; eks loss_mulai_sampai_2025_ha).", "Σ loss [mulai,2025]", "wiup_loss_yearly"),
    ("backtrack_kohort", "loss_mulai_aktif_sampai_2021_ha", "Σ loss Hansen (kotor) jendela [mulai aktif versi aturan, 2021] — pembilang dekomposisi kartu: loss_mulai_aktif_sampai_2025_ha − ini = loss 2022-2025 (tak terperiksa sawit); ini − varian tanpa_sawit = bagian berujung sawit.", "Σ loss [mulai,2021]", "wiup_loss_yearly"),
    ("backtrack_kohort", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "Σ max(0, loss−sawit) per tahun pada [mulai aktif, 2021] (batas Descals).", "Σ max(0, loss−sawit) [mulai,2021]", "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("backtrack_kohort", "polygon_ha", "Σ luas poligon seluruh konsesi kohort (penyebut pct).", None, "wiup_loss.polygon_area_ha"),
    ("backtrack_kohort", "pct_poligon_mulai_aktif_sampai_2025", "100 · loss_mulai_aktif_sampai_2025_ha / polygon_ha (eks pct_poligon_mulai_2025 — 'mulai_2025' terbaca 'mulai tahun 2025').", "100·loss/polygon", None),
    ("backtrack_kohort", "r_luas_loss", "Pearson luas_sk vs loss jendela [mulai aktif, 2025] (konsesi ber-mulai).", "pearson(luas_sk, loss)", None),
    ("backtrack_komoditas", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_komoditas", "kohort", "Kohort tahun-terbit-SK (Pra-2009/P1/P2/P3/TANPA_PERIODE) — eks kolom `periode`, rename Fase G.", None, None),
    ("backtrack_komoditas", "grup_komoditas", "BATUBARA vs MINERAL LOGAM (aturan sama dgn periode_komoditas).", None, None),
    ("backtrack_komoditas", "n", "Konsesi sel ini yang tahun mulainya terdefinisi.", None, None),
    ("backtrack_komoditas", "loss_mulai_aktif_sampai_2025_ha", "Σ loss Hansen jendela [mulai aktif versi aturan, 2025] sel ini.", "Σ loss [mulai,2025]", "wiup_loss_yearly"),
    ("backtrack_komoditas", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "Σ max(0, loss−sawit) [mulai aktif, 2021] sel ini.", None, "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("backtrack_komoditas", "hutan_2009_ha", "Σ hutan acuan 2009 sel ini — penyebut intensitas kerangka era Minerba (Fase C 16 Agu; sebelumnya intensitas komoditas berpenyebut hutan 2000). NULL bila wiup_loss.hutan_2009_ha absen.", "Σ hutan_2009_ha", "wiup_loss.hutan_2009_ha"),
    ("backtrack_komoditas", "pct_hutan2009_mulai_aktif_sampai_2025", "100 · loss_mulai_aktif_sampai_2025_ha / hutan_2009_ha sel ini; NULL bila penyebut 0/absen.", "100·loss/hutan_2009", None),
    ("backtrack_klasifikasi", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_klasifikasi", "kohort", "Kohort tahun-terbit-SK (Pra-2009/P1/P2/P3/TANPA_PERIODE) — eks kolom `periode`, rename Fase G.", None, None),
    ("backtrack_klasifikasi", "kelas", "Kelas izin (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI).", None, "klasifikasi_izin.kelas"),
    ("backtrack_klasifikasi", "n", "Konsesi sel ini yang tahun mulainya terdefinisi.", None, None),
    ("backtrack_klasifikasi", "loss_mulai_aktif_sampai_2025_ha", "Σ loss Hansen jendela [mulai aktif versi aturan, 2025] sel ini.", "Σ loss [mulai,2025]", "wiup_loss_yearly"),
    ("backtrack_stok", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_stok", "grup_tipe", "'kohort' (P1/P2/P3/Pra-2009 via iup_year — eks nilai 'periode', rename Fase G) atau 'penerbit' (pejabat).", None, None),
    ("backtrack_stok", "grup", "Nilai grup: kode kohort SK atau nama pejabat penerbit.", None, None),
    ("backtrack_stok", "year", "Tahun kalender 2009-2025.", None, None),
    ("backtrack_stok", "n_aktif", "KUMULATIF s.d. tahun ini: konsesi grup yang tahun mulainya (versi `aturan`) <= tahun ini.", "count(mulai <= year)", None),
    ("backtrack_stok", "luas_aktif_ha", "KUMULATIF s.d. tahun ini: Σ luas_sk konsesi aktif grup.", None, "wiup_geoportal.luas_sk"),
    ("backtrack_stok", "forest_aktif_ha", "KUMULATIF s.d. tahun ini: Σ hutan-2000 konsesi aktif grup.", None, "wiup_loss.forest_2000_ha"),
    ("backtrack_stok", "loss_ha", "Loss tahun ini dari konsesi aktif grup (flow).", None, "wiup_loss_yearly"),
    ("backtrack_stok", "loss_kumulatif_sejak_2009_ha", "Akumulasi loss konsesi-aktif sejak 2009 s/d tahun ini (stok; eks loss_kumulatif_ha — awal akumulasi masuk nama).", "Σ loss_ha 2009..year", None),
    ("backtrack_sawit", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_sawit", "kohort", "Kohort tahun-terbit-SK (Pra-2009/P1/P2/P3/TANPA_PERIODE) — eks kolom `periode`, rename Fase G.", None, None),
    ("backtrack_sawit", "n", "Konsesi kohort yang tahun mulainya terdefinisi.", None, None),
    ("backtrack_sawit", "loss_mulai_aktif_sampai_2021_ha", "Σ loss Hansen jendela [mulai aktif versi aturan, 2021] — penyebut pangsa sawit; berhenti 2021 (batas Descals).", "Σ loss [mulai,2021]", "wiup_loss_yearly"),
    ("backtrack_sawit", "loss_sawit_mulai_aktif_sampai_2021_ha", "Bagian yang bertepatan jadi sawit (tol2th) pada jendela yang sama.", "Σ sawit [mulai,2021]", "atribusi_sawit_yearly"),
    ("backtrack_sawit", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "Σ max(0, loss−sawit) per tahun pada [mulai aktif, 2021].", None, None),
    ("backtrack_sawit", "persen_sawit_mulai_aktif_sampai_2021", "100 · loss_sawit / loss jendela [mulai aktif, 2021]; NULL bila penyebut 0 (eks persen_sawit_mulai_2021).", "100·sawit/loss", None),
    ("backtrack_laju_ringkas", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_laju_ringkas", "basis", "Basis hitung: 'kotor' (Hansen, [mulai,2025]) atau 'bersih' (Hansen−sawit, [mulai,2021]).", None, None),
    ("backtrack_laju_ringkas", "dimensi", "Pengelompokan baris: 'semua' / 'kelas' (klasifikasi izin) / 'kohort' (kohort tahun-terbit-SK P1-P3). Nilai 'kohort' eks 'periode' (koreksi Fase T 16 Agu) — baris ini mengelompokkan menurut KAPAN SK TERBIT (n 239/262/284), bukan menurut jendela kalender; satu kata satu makna, 'periode' kini selalu berarti jendela kalender.", None, None),
    ("backtrack_laju_ringkas", "kelompok", "Nilai kelompok (SEMUA, kelas izin, atau kohort SK P1-P3).", None, None),
    ("backtrack_laju_ringkas", "n", "Konsesi kelompok yang lajunya terdefinisi di basis+aturan ini.", None, None),
    ("backtrack_laju_ringkas", "n_pct", "Subset n yang laju %/thn-nya terdefinisi (hutan saat mulai > 0).", None, None),
    ("backtrack_laju_ringkas", "total_loss_ha", "Σ loss basis tsb, jendela [mulai versi aturan, 2025 (kotor) / 2021 (bersih)].", None, None),
    ("backtrack_laju_ringkas", "median_ha_thn", "Median laju ha/tahun (jendela = kolom basis + aturan).", None, None),
    ("backtrack_laju_ringkas", "mean_ha_thn", "Rata-rata laju ha/tahun.", None, None),
    ("backtrack_laju_ringkas", "p25_ha_thn", "Persentil-25 laju ha/tahun.", None, None),
    ("backtrack_laju_ringkas", "p75_ha_thn", "Persentil-75 laju ha/tahun.", None, None),
    ("backtrack_laju_ringkas", "p90_ha_thn", "Persentil-90 laju ha/tahun.", None, None),
    ("backtrack_laju_ringkas", "median_pct_thn", "Median laju %/tahun (dari hutan saat mulai).", None, None),
    ("backtrack_laju_ringkas", "mean_pct_thn", "Rata-rata laju %/tahun.", None, None),
    ("backtrack_laju_ringkas", "p25_pct_thn", "Persentil-25 laju %/tahun.", None, None),
    ("backtrack_laju_ringkas", "p75_pct_thn", "Persentil-75 laju %/tahun.", None, None),
    ("backtrack_laju_ringkas", "p90_pct_thn", "Persentil-90 laju %/tahun.", None, None),
    ("backtrack_distribusi", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS) — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_distribusi", "metrik", "luas_sk (luas SK, ha — fakta administratif poligon; TIDAK punya varian dikurangi-sawit, keputusan igoen 16 Agu) / ditambang / ditambang_tanpa_sawit (hanya bila lapisan sawit ada). Jendela ditambang IKUT kelompok (redefinisi 15 Agu), DIKLEM per konsesi ke tahun mulainya: P1/P2/P3 = Σ loss tahun ∈ [max(tahun_awal, mulai versi aturan), tahun_akhir] — konsesi yang baru mulai di tengah jendela hanya dihitung sejak mulainya, BUKAN sejak tahun_awal (tanpa-sawit: tahun ∈ [max(tahun_awal, mulai), min(tahun_akhir, 2021)], per tahun max(0, loss−sawit)); SEMUA = sejak-mulai ([mulai, 2025] / tanpa-sawit [mulai, 2021]).", None, None),
    ("backtrack_distribusi", "kelompok", "SEMUA atau P1/P2/P3. Redefinisi 15 Agu: P1/P2/P3 BUKAN lagi kohort iup_year — keanggotaan = KUMULATIF aktif s.d. akhir jendela (mulai versi `aturan` <= 2014/2019/2025), konsisten dgn backtrack_periode_kalender. Himpunan P3 == SEMUA (keduanya mulai <= 2025); yang beda jendela metrik ditambang (lihat `metrik`). Framing kohort-SK murni tersedia via aturan POLOS.", None, None),
    ("backtrack_distribusi", "n", "Konsesi anggota kelompok: mulai versi `aturan` <= akhir jendela kelompok (SEMUA: <= 2025).", None, None),
    ("backtrack_distribusi", "total_ha", "Total metrik (ha) — Σ nilai seluruh anggota kelompok (ditambah 16 Agu: konteks skala utk mean/median/gini). Jendela nilai per konsesi IKUT aturan kolom `metrik`.", "Σ nilai per konsesi", None),
    ("backtrack_distribusi", "mean_ha", "Rata-rata metrik (ha). Jendela nilai per konsesi IKUT aturan kolom `metrik`: ditambang P1/P2/P3 = [max(tahun_awal, mulai versi aturan), tahun_akhir]; SEMUA = [mulai, 2025] (tanpa-sawit dipotong 2021).", None, None),
    ("backtrack_distribusi", "median_ha", "Median metrik (ha). Jendela nilai per konsesi IKUT aturan kolom `metrik` (klem [max(tahun_awal, mulai), tahun_akhir]; SEMUA sejak-mulai).", None, None),
    ("backtrack_distribusi", "gini", "Indeks Gini metrik (0 = merata, 1 = terkonsentrasi penuh); rumus selisih-berpasangan, NULL bila n<2 atau Σ=0. Jendela nilai per konsesi IKUT aturan kolom `metrik` (klem [max(tahun_awal, mulai), tahun_akhir]; SEMUA sejak-mulai).", "(2Σi·xᵢ)/(nΣx) − (n+1)/n atas x terurut", "scripts/build_laju_izin.py"),
    ("backtrack_signifikansi", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_signifikansi", "metrik", "'loss' (Σ [mulai,2025] per konsesi) atau 'laju_pct' (%/thn dari hutan saat mulai).", None, None),
    ("backtrack_signifikansi", "uji", "kruskal_wallis (lintas P1|P2|P3) atau mann_whitney_holm (pairwise, koreksi Holm).", None, None),
    ("backtrack_signifikansi", "grup_a", "Grup pembanding pertama (atau 'P1|P2|P3' utk uji lintas).", None, None),
    ("backtrack_signifikansi", "grup_b", "Grup pembanding kedua ('-' utk uji lintas).", None, None),
    ("backtrack_signifikansi", "n_a", "Ukuran sampel grup A (total utk uji lintas).", None, None),
    ("backtrack_signifikansi", "n_b", "Ukuran sampel grup B (0 utk uji lintas).", None, None),
    ("backtrack_signifikansi", "statistik", "Nilai statistik uji (H utk Kruskal, U utk Mann-Whitney).", None, None),
    ("backtrack_signifikansi", "p_value", "p mentah dua-sisi.", None, None),
    ("backtrack_signifikansi", "p_adjusted", "p terkoreksi Holm (NULL utk baris Kruskal).", None, None),
    ("backtrack_signifikansi", "signifikan_005", "1 bila p (terkoreksi bila ada) < 0,05.", None, None),
    ("backtrack_signifikansi", "besar_efek_r", "BESAR EFEK rank-biserial untuk baris mann_whitney_holm — menjawab 'seberapa besar bedanya', bukan 'seberapa yakin ada beda'. Tanda positif = grup A ber-nilai lebih RENDAH daripada grup B. Rujukan kasar: |r| 0,1 kecil · 0,3 sedang · 0,5 besar. NULL untuk baris kruskal_wallis (uji lintas 3 grup, tak punya U berpasangan). Ditambah Fase T 16 Agu: dgn n≈250 per grup, p kecil bisa muncul dari selisih yang tak berarti — jangan kutip p tanpa kolom ini.", "1 − 2·U/(n_a·n_b)", "scripts/build_laju_izin.py"),

    # ── Irisan halaman Statistik (Fase C 16 Agu): geografi · komoditas rinci ·
    #    aktor · keparahan · zona bebas — semuanya per metode backtrack,
    #    jendela [mulai aktif versi aturan, 2025], penyebut hutan_2009_ha.
    ("backtrack_wilayah", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS) — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_wilayah", "tingkat", "Tingkat agregasi baris: 'total' (satu baris rekonsiliasi, wilayah='SEMUA') / 'provinsi' / 'kabupaten'.", None, None),
    ("backtrack_wilayah", "wilayah", "Nama wilayah pada tingkat itu ('SEMUA' utk tingkat total). Provinsi = bagian PERTAMA nama_prov (konsesi lintas-provinsi dihitung utuh di provinsi pertama); kabupaten = pecahan kab_normalized.", None, "wiup_geoportal.nama_prov / kab_normalized"),
    ("backtrack_wilayah", "n_konsesi", "Jumlah konsesi yang tahun mulainya (versi `aturan`) <= 2025 di wilayah ini. AWAS tingkat kabupaten: konsesi lintas-kabupaten dihitung SATU KALI DI TIAP kabupatennya, jadi Σ-nya > jumlah konsesi (hektarnya tidak — lihat kolom loss).", "count", "wiup_geoportal"),
    ("backtrack_wilayah", "luas_sk_ha", "Σ luas SK konsesi wilayah ini; tingkat kabupaten DIBAGI RATA antar kabupaten konsesi lintas-kabupaten.", "Σ luas_sk (kabupaten: /jumlah kabupaten)", "wiup_geoportal.luas_sk"),
    ("backtrack_wilayah", "hutan_2009_ha", "Σ hutan acuan 2009 konsesi wilayah ini (penyebut intensitas kerangka era Minerba); dibagi rata sama seperti luas. NULL bila wiup_loss.hutan_2009_ha belum ada.", "Σ hutan_2009_ha", "wiup_loss.hutan_2009_ha"),
    ("backtrack_wilayah", "loss_mulai_aktif_sampai_2025_ha", "Σ loss Hansen jendela [mulai aktif versi `aturan`, 2025] konsesi wilayah ini; kabupaten dibagi rata (Σ seluruh kabupaten = Σ seluruh provinsi = baris tingkat total, diikat invarian).", "Σ loss [mulai,2025]", "wiup_loss_yearly"),
    ("backtrack_wilayah", "loss_mulai_aktif_sampai_2021_ha", "Σ loss Hansen (KOTOR) jendela [mulai aktif versi `aturan`, 2021] — batas peta Descals. Dipakai dekomposisi slide sawit: bagian berujung sawit = kolom ini − varian tanpa_sawit; bagian belum terperiksa = loss_mulai_aktif_sampai_2025_ha − kolom ini.", "Σ loss [mulai,2021]", "wiup_loss_yearly"),
    ("backtrack_wilayah", "loss_sawit_mulai_aktif_sampai_2021_ha", "Bagian kehilangan yang BERTEPATAN jadi kebun sawit (varian tol2th peta Descals) pada [mulai aktif, 2021]. Bertepatan ≠ disebabkan. NULL bila lapisan sawit absen.", "Σ sawit_tol2th [mulai,2021]", "atribusi_sawit_yearly"),
    ("backtrack_wilayah", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "Varian tanpa-sawit: Σ max(0, loss−sawit) pada [mulai aktif, 2021] (batas peta Descals). NULL bila lapisan sawit absen.", "Σ max(0, loss−sawit) [mulai,2021]", "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("backtrack_wilayah", "persen_sawit_mulai_aktif_sampai_2021", "100 · loss_sawit / loss_mulai_aktif_sampai_2021_ha — pangsa yang bertepatan sawit DI DALAM jendela terperiksa. Penyebutnya sengaja BUKAN loss s.d. 2025: 2022-2025 di luar jangkauan peta Descals.", "100·sawit/loss [mulai,2021]", None),
    ("backtrack_wilayah", "loss_2022_2025_belum_terperiksa_ha", "Kehilangan 2022-2025 yang TAK BISA diperiksa sawit (peta Descals berhenti 2021) = loss_mulai_aktif_sampai_2025_ha − loss_mulai_aktif_sampai_2021_ha. Jangan pernah masuk penyebut persen sawit.", "loss[mulai,2025] − loss[mulai,2021]", None),
    ("backtrack_wilayah", "pct_hutan2009_mulai_aktif_sampai_2025", "Intensitas: 100 · loss_mulai_aktif_sampai_2025_ha / hutan_2009_ha. NULL bila penyebut 0/absen. Bisa > 100 (loss diukur di seluruh poligon, bukan hanya bagian berhutan 2009).", "100·loss/hutan_2009", None),
    ("backtrack_komoditas_rinci", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_komoditas_rinci", "komoditas", "Nama komoditas APA ADANYA dari Geoportal (BATUBARA, BAUKSIT, ZIRKON, …) — beda dari backtrack_komoditas yang cuma 2 grup (BATUBARA vs MINERAL LOGAM).", None, "wiup_geoportal.komoditas"),
    ("backtrack_komoditas_rinci", "n_konsesi", "Konsesi komoditas ini yang tahun mulainya (versi `aturan`) <= 2025.", "count", None),
    ("backtrack_komoditas_rinci", "luas_sk_ha", "Σ luas SK konsesi komoditas ini.", None, "wiup_geoportal.luas_sk"),
    ("backtrack_komoditas_rinci", "hutan_2009_ha", "Σ hutan acuan 2009 komoditas ini (penyebut intensitas). NULL bila kolom sumber absen.", None, "wiup_loss.hutan_2009_ha"),
    ("backtrack_komoditas_rinci", "loss_mulai_aktif_sampai_2025_ha", "Σ loss Hansen jendela [mulai aktif versi `aturan`, 2025].", "Σ loss [mulai,2025]", "wiup_loss_yearly"),
    ("backtrack_komoditas_rinci", "loss_mulai_aktif_sampai_2021_tanpa_sawit_ha", "Varian tanpa-sawit [mulai aktif, 2021]; NULL bila lapisan sawit absen.", None, "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("backtrack_komoditas_rinci", "pct_hutan2009_mulai_aktif_sampai_2025", "Intensitas komoditas: 100 · loss / hutan_2009_ha.", "100·loss/hutan_2009", None),
    ("backtrack_konsesi_top", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_konsesi_top", "peringkat", "Peringkat 1-25 menurut loss_mulai_aktif_sampai_2025_ha (menurun) DI DALAM aturan ini — peringkat disimpan supaya klien tak mengurut ulang. Diperluas dari 10 ke 25 (Fase T 16 Agu) agar konsesi yang jatuh KELUAR sepuluh besar di metode lain tetap terbaca; UI Statistik tetap merender 10 teratas.", None, None),
    ("backtrack_konsesi_top", "kode_wiup", "Kode WIUP konsesi (kunci ke wiup_geoportal).", None, "wiup_geoportal.kode_wiup"),
    ("backtrack_konsesi_top", "nama_usaha", "Nama badan usaha pemegang konsesi (didenormalisasi utk label chart).", None, "wiup_geoportal.nama_usaha"),
    ("backtrack_konsesi_top", "komoditas", "Komoditas konsesi.", None, "wiup_geoportal.komoditas"),
    ("backtrack_konsesi_top", "nama_prov", "Provinsi konsesi (bagian pertama bila lintas-provinsi).", None, "wiup_geoportal.nama_prov"),
    ("backtrack_konsesi_top", "mulai_aktif", "Tahun mulai aktif konsesi ini menurut `aturan`.", None, "laju_izin_konsesi.mulai / atribusi_izin_aktif.mulai"),
    ("backtrack_konsesi_top", "luas_sk_ha", "Luas SK konsesi (ha).", None, "wiup_geoportal.luas_sk"),
    ("backtrack_konsesi_top", "hutan_2009_ha", "Hutan acuan 2009 di poligon konsesi (penyebut intensitas).", None, "wiup_loss.hutan_2009_ha"),
    ("backtrack_konsesi_top", "loss_mulai_aktif_sampai_2025_ha", "Loss Hansen jendela [mulai aktif versi `aturan`, 2025] konsesi ini.", "Σ loss [mulai,2025]", "wiup_loss_yearly"),
    ("backtrack_konsesi_top", "pct_hutan2009_mulai_aktif_sampai_2025", "100 · loss / hutan_2009_ha konsesi ini.", "100·loss/hutan_2009", None),
    ("backtrack_keparahan", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_keparahan", "ember", "Label ember keparahan ('0–10%' … '75%+') atas % hutan-2009 yang hilang sejak konsesi aktif.", None, None),
    ("backtrack_keparahan", "urutan", "Urutan tampil ember (1..5) — supaya klien tak mengurut label teks.", None, None),
    ("backtrack_keparahan", "batas_bawah_pct", "Batas bawah ember (inklusif), dalam % hutan 2009.", None, None),
    ("backtrack_keparahan", "batas_atas_pct", "Batas atas ember (eksklusif); NULL = terbuka ke atas (loss bisa > 100% hutan 2009).", None, None),
    ("backtrack_keparahan", "n_konsesi", "Jumlah konsesi (mulai versi `aturan` <= 2025, hutan_2009_ha > 0) yang jatuh di ember ini.", "count", None),
    ("backtrack_keparahan", "n_tanpa_penyebut", "Konsesi kohort yang TAK bisa diember karena hutan_2009_ha = 0/absen — sama di tiap baris satu aturan (rekonsiliasi: Σ n_konsesi + n_tanpa_penyebut = kohort aturan itu).", "count", None),
    ("backtrack_zona_bebas", "aturan", "Metode tahun mulai — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_zona_bebas", "year", "Tahun potret, 2009-2025 (jendela era Minerba).", None, None),
    ("backtrack_zona_bebas", "n_kab_total", "Jumlah kab/kota master Kalimantan (56, Kemendagri 2024) — konstan.", None, "konstanta MASTER_KABKOTA (scripts/build_laju_izin.py)"),
    ("backtrack_zona_bebas", "n_kab_ada_konsesi", "Kab/kota yang sudah dimasuki minimal satu konsesi AKTIF versi `aturan` pada tahun itu (jam metode, bukan tahun terbit SK).", "count(master ∈ ∪ kab konsesi ber-mulai <= year)", "wiup_geoportal.kab_normalized"),
    ("backtrack_zona_bebas", "n_kab_bersih", "Kab/kota tanpa satu pun konsesi aktif pada tahun itu = n_kab_total − n_kab_ada_konsesi. Monoton tak naik terhadap tahun (himpunan aktif hanya bertambah) — diikat invarian.", "n_kab_total − n_kab_ada_konsesi", None),
    ("backtrack_zona_bebas", "kab_bersih", "Daftar nama KABUPATEN yang masih bebas konsesi pada tahun itu, dipisah koma.", None, None),
    ("backtrack_zona_bebas", "kota_bersih", "Daftar nama KOTA yang masih bebas konsesi pada tahun itu, dipisah koma (dipisah dari kabupaten karena kota memang jarang jadi wilayah izin).", None, None),

    # ── backtrack_periode_kalender: 4 kolom penopang slide "naik atau turun" ──
    # (Fase T 16 Agu) Deret hektare mentah versi tanggal SK NAIK sementara versi
    # Deteksi Hansen TURUN. Penjelasnya bukan hutan yang makin cepat hilang,
    # melainkan portofolio izin yang tumbuh — jadi jendela butuh penyebut hutan
    # berdiri (kolom hutan_*) dan ukuran portofolio di AWAL jendela.
    ("backtrack_periode_kalender", "hutan_awal_periode_ha", "Stok hutan yang masih berdiri pada AWAL jendela (tahun_awal) di konsesi yang sudah aktif versi `aturan` — snapshot backtrack_tahunan.hutan_awal_tahun_ha @ year=tahun_awal. Konteks, bukan penyebut: penyebut laju memakai hutan_tahun_total_ha.", "backtrack_tahunan.hutan_awal_tahun_ha @ year=tahun_awal", "backtrack_tahunan"),
    ("backtrack_periode_kalender", "hutan_tahun_total_ha", "Σ stok hutan-berdiri AWAL TIAP TAHUN dalam jendela (bukan rata-rata × jumlah tahun) — penyebut pct_hutan_per_thn. Bentuk 'jumlah tahun-hektare' ini membuat jendela 6 tahun tak otomatis kalah dari jendela 5 tahun, dan menyerap fakta bahwa stok menyusut tiap tahun.", "Σ backtrack_tahunan.hutan_awal_tahun_ha, year ∈ [tahun_awal, tahun_akhir]", "backtrack_tahunan"),
    ("backtrack_periode_kalender", "pct_hutan_per_thn", "LAJU jendela ini: 100 · loss_ha / hutan_tahun_total_ha, satuan % hutan-berdiri per tahun. Inilah angka yang boleh dibandingkan ANTAR jendela — hektare mentah tidak, karena jumlah & luas konsesi aktifnya berbeda jauh antar jendela (lihat luas_aktif_awal_ha). NULL bila penyebut 0/absen.", "100·loss_ha/hutan_tahun_total_ha", None),
    ("backtrack_periode_kalender", "luas_aktif_awal_ha", "Σ luas SK konsesi yang sudah aktif versi `aturan` pada AWAL jendela ({mulai <= tahun_awal}) — ukuran PORTOFOLIO saat jendela dimulai. Bandingkan dgn luas_aktif_total_ha (akhir jendela): di POLOS angka ini tumbuh ~9,5× dari P1 ke P3, di CITRA praktis rata. Itu sebab deret hektare mentah kedua metode bergerak berlawanan arah.", "Σ luas_sk atas {mulai versi aturan <= tahun_awal}", "wiup_geoportal.luas_sk"),

    # ══ Fase T (16 Agu): tabel penopang bagian "Temuan" halaman Statistik ═════
    ("backtrack_tak_terlihat", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS) — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_tak_terlihat", "kohort", "Kohort tahun-terbit-SK: Pra-2009/P1/P2/P3 (menurut iup_year), TANPA_TAHUN_SK (iup_year kosong — 7 konsesi), SK_LUAR_JENDELA (iup_year di luar 1998-2025, mis. 2026 — 4 konsesi), SEMUA (baris total seluruh 825). Dua ember terakhir dulu menumpuk jadi satu 'TANPA_PERIODE'; dipisah Fase T supaya kalimat 'sekian ha di konsesi tanpa tahun SK' bisa dibuktikan dari DB.", None, "wiup_geoportal.iup_year"),
    ("backtrack_tak_terlihat", "urutan", "Urutan tampil kohort (1..7) — supaya klien tak mengurut label teks.", None, None),
    ("backtrack_tak_terlihat", "n_konsesi", "Jumlah konsesi kohort ini (seluruhnya, tak peduli tahun mulainya terdefinisi atau tidak).", "count", None),
    ("backtrack_tak_terlihat", "n_ber_tahun_sk", "Subset n_konsesi yang PUNYA iup_year — penyebut kolom n_prask_*.", "count(iup_year IS NOT NULL)", "wiup_geoportal.iup_year"),
    ("backtrack_tak_terlihat", "n_tak_terlihat_ge1", "Konsesi kohort ini yang tak_terlihat-nya >= 1 ha (ambang yang sama dgn AMBANG_BUKTI_HA).", "count", None),
    ("backtrack_tak_terlihat", "n_tak_terlihat_gt100", "Konsesi kohort ini yang tak_terlihat-nya > 100 ha — bukan sekadar derau piksel.", "count", None),
    ("backtrack_tak_terlihat", "tak_terlihat_ha", "PEMBILANG: Σ loss Hansen pada [2009, mulai aktif versi `aturan` − 1] — kehilangan di dalam jendela era Minerba yang metode ini TIDAK bebankan ke izin mana pun karena jamnya belum jalan. Konsesi yang tahun mulainya tak terdefinisi/di luar jendela menyumbang SELURUH loss 2009-2025-nya. BUKAN klaim tambang ilegal: iup_year adalah tahun dokumen izin yang berlaku sekarang, dan 525 konsesi terklasifikasi PERPANJANGAN (izin pendahulunya tak terdata).", "Σ loss [2009, mulai−1]", "wiup_loss_yearly"),
    ("backtrack_tak_terlihat", "loss_2009_2025_ha", "PENYEBUT, sengaja BEBAS METODE: Σ loss Hansen 2009-2025 seluruh konsesi kohort ini, tanpa atribusi apa pun. Sama di ketiga aturan — itulah gunanya, supaya persen ketiga metode dibagi angka yang sama dan bisa dibandingkan.", "Σ loss [2009, 2025]", "wiup_loss_yearly"),
    ("backtrack_tak_terlihat", "loss_terhitung_ha", "loss_2009_2025_ha − tak_terlihat_ha = bagian yang metode ini bebankan ke izin yang sudah berjalan (identik dgn Σ loss [mulai, 2025] kohort itu). Rekonsiliasi diikat invarian backtrack-tak-terlihat-rekonsil.", "loss_2009_2025_ha − tak_terlihat_ha", None),
    ("backtrack_tak_terlihat", "pct_tak_terlihat", "100 · tak_terlihat_ha / loss_2009_2025_ha; NULL bila penyebut 0.", "100·tak_terlihat/loss_2009_2025", None),
    ("backtrack_tak_terlihat", "n_prask_2001_ge1", "BEBAS METODE (nilainya sama di tiap `aturan`, sengaja diulang seperti n_kab_total): konsesi kohort ini yang punya jejak kehilangan >= 1 ha pada [2001, iup_year − 1] — jendela penuh Hansen, BUKAN mulai 2009, karena pertanyaannya 'apakah lahan ini sudah dibuka sebelum SK terbit' dan pembukaan 2003 sama sahihnya dgn 2012. NULL bila kohort tak punya konsesi ber-iup_year.", "count(Σ loss [2001, iup_year−1] >= 1 ha)", "wiup_loss_yearly × wiup_geoportal.iup_year"),
    ("backtrack_tak_terlihat", "n_prask_2001_gt100", "Sama, ambang > 100 ha (menyingkirkan tafsir 'cuma derau piksel'). NULL bila kohort tak punya konsesi ber-iup_year.", "count(Σ loss [2001, iup_year−1] > 100 ha)", "wiup_loss_yearly × wiup_geoportal.iup_year"),
    ("backtrack_selisih", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS) — lihat backtrack_tahunan.aturan.", None, None),
    ("backtrack_selisih", "jenis", "Blok isi tabel: 'selisih' (sebaran iup_year − mulai, 6 ember, Σ = jumlah konsesi) · 'selisih_ringkas' (satu baris persentil untuk selisih > 0 saja) · 'klem' (berapa konsesi yang tahun mulainya jatuh persis di 2009, batas bawah jendela) · 'tahun_bukti' (sebaran tahun bukti MENTAH sebelum klem — HANYA untuk aturan CITRA, satu-satunya metode yang punya konsep bukti).", None, None),
    ("backtrack_selisih", "ember", "Label ember dalam blok `jenis` (mis. '6–10', 'selisih > 0', 'mulai aktif = 2009', '2001–2008', '2012').", None, None),
    ("backtrack_selisih", "urutan", "Urutan tampil ember di dalam satu (aturan, jenis).", None, None),
    ("backtrack_selisih", "n_konsesi", "Jumlah konsesi di ember ini.", "count", None),
    ("backtrack_selisih", "p25", "Persentil-25 selisih (tahun) — hanya blok 'selisih_ringkas'; NULL di blok lain.", "pctl(selisih>0, 25)", None),
    ("backtrack_selisih", "median", "Median selisih (tahun) — hanya blok 'selisih_ringkas'; NULL di blok lain.", "pctl(selisih>0, 50)", None),
    ("backtrack_selisih", "p75", "Persentil-75 selisih (tahun) — hanya blok 'selisih_ringkas'; NULL di blok lain.", "pctl(selisih>0, 75)", None),
    ("backtrack_selisih", "maks", "Selisih terbesar (tahun) — hanya blok 'selisih_ringkas'; NULL di blok lain.", "max(selisih>0)", None),
    ("backtrack_kesepakatan", "aturan_a", "Metode pertama pasangan (urutan tetap CITRA < INDIKASI < POLOS, jadi tiap pasangan muncul sekali).", None, None),
    ("backtrack_kesepakatan", "aturan_b", "Metode kedua pasangan.", None, None),
    ("backtrack_kesepakatan", "metrik", "Deret yang dikorelasikan: 'loss_ha' (hektare mentah per tahun) atau 'pct_thn' (100 · loss tahun itu / stok hutan berdiri awal tahun itu). Keduanya SENGAJA dilaporkan: kesepakatan tinggi hanya muncul di pct_thn, sedangkan loss_ha justru berbeda tajam — itu temuan (deret hektare mentah versi tanggal SK mengukur pertumbuhan portofolio izin, bukan laju deforestasi).", None, None),
    ("backtrack_kesepakatan", "n_tahun", "Jumlah titik tahun yang dikorelasikan (2009-2025 = 17).", None, None),
    ("backtrack_kesepakatan", "pearson", "Korelasi Pearson deret tahunan kedua metode (kemiripan bentuk & besaran, peka pada pencilan).", "Σ(x−x̄)(y−ȳ)/√(Σ(x−x̄)²Σ(y−ȳ)²)", "backtrack_tahunan"),
    ("backtrack_kesepakatan", "spearman", "Korelasi Spearman = Pearson atas PERINGKAT (peringkat seri dirata-ratakan) — kemiripan URUTAN tahun ramai/sepi, tahan pencilan.", "pearson(rank(x), rank(y))", "backtrack_tahunan"),
    ("backtrack_kesepakatan", "n_irisan_top10", "Berapa konsesi yang sama-sama masuk 10 besar KEDUA metode pasangan ini (dari 10). Sifat PASANGAN, bukan sifat metrik — nilainya sengaja diulang di kedua baris metrik. NULL bila backtrack_konsesi_top kosong (kolom wilayah absen).", "|top10(a) ∩ top10(b)|", "backtrack_konsesi_top"),
    ("backtrack_tahun_ekstrem", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS).", None, None),
    ("backtrack_tahun_ekstrem", "metrik", "'loss_ha' atau 'pct_thn' — deret yang diperingkat (definisi sama dgn backtrack_kesepakatan.metrik).", None, None),
    ("backtrack_tahun_ekstrem", "jenis", "'puncak' (3 tahun tertinggi) atau 'palung' (3 tahun terendah).", None, None),
    ("backtrack_tahun_ekstrem", "urutan", "Peringkat di dalam jenis-nya: 1 = paling tinggi (puncak) / paling rendah (palung).", None, None),
    ("backtrack_tahun_ekstrem", "year", "Tahun kalender 2009-2025.", None, None),
    ("backtrack_tahun_ekstrem", "nilai", "Nilai deret pada tahun itu (ha untuk loss_ha; %/tahun untuk pct_thn).", None, "backtrack_tahunan"),
    ("backtrack_lorenz", "aturan", "Metode tahun mulai (CITRA/INDIKASI/POLOS).", None, None),
    ("backtrack_lorenz", "persentil", "Titik kurva: 0, 10, 20, … 100 (persen konsesi TERATAS menurut besar kehilangan/luas).", None, None),
    ("backtrack_lorenz", "n_konsesi", "Berapa konsesi yang masuk potongan teratas itu = ⌈persentil% × n⌉ (pembulatan KE ATAS — kurva tak boleh melompati konsesi yang persentilnya tepat di batas).", "ceil(persentil/100 · n)", None),
    ("backtrack_lorenz", "pangsa_loss_teratas_pct", "Pangsa kehilangan yang ditanggung n_konsesi teratas itu, dari total kehilangan metode ini (%). Monoton naik, berakhir 100 pada persentil 100.", "100 · Σ loss teratas / Σ loss", "wiup_loss_yearly"),
    ("backtrack_lorenz", "pangsa_luas_teratas_pct", "Pembanding: pangsa LUAS SK yang dipegang konsesi terluas dalam jumlah yang sama (diurut menurut luas, bukan menurut loss). Menjawab 'apakah pemusatan kerusakan cuma cerminan pemusatan luas izin'.", "100 · Σ luas_sk terluas / Σ luas_sk", "wiup_geoportal.luas_sk"),
    ("backtrack_lorenz", "gini_loss", "Indeks Gini kehilangan per konsesi (0 merata, 1 terpusat penuh) — sama di tiap baris satu aturan (sifat sebaran, bukan sifat titik kurva).", "(2Σi·xᵢ)/(nΣx) − (n+1)/n atas loss terurut", "scripts/build_laju_izin.py"),
    ("backtrack_lorenz", "gini_luas", "Indeks Gini luas SK per konsesi — pembanding gini_loss. Kalau gini_loss > gini_luas, kerusakan lebih terpusat daripada luas izin: 'yang besar merusak besar' tak cukup menjelaskan.", "(2Σi·xᵢ)/(nΣx) − (n+1)/n atas luas_sk terurut", "scripts/build_laju_izin.py"),
    ("backtrack_top_union", "kode_wiup", "Kode WIUP konsesi (kunci ke wiup_geoportal).", None, "wiup_geoportal.kode_wiup"),
    ("backtrack_top_union", "urutan", "Urutan tampil 1..N, menurun menurut loss_citra_ha (metode utama) — supaya klien tak mengurut ulang.", None, None),
    ("backtrack_top_union", "nama_usaha", "Nama badan usaha pemegang konsesi (didenormalisasi utk label tabel).", None, "wiup_geoportal.nama_usaha"),
    ("backtrack_top_union", "komoditas", "Komoditas konsesi.", None, "wiup_geoportal.komoditas"),
    ("backtrack_top_union", "nama_prov", "Provinsi konsesi (bagian pertama bila lintas-provinsi).", None, "wiup_geoportal.nama_prov"),
    ("backtrack_top_union", "iup_year", "Tahun terbit SK yang tercatat Geoportal — tahun dokumen izin yang berlaku sekarang, BUKAN tentu tahun pemberian pertama.", None, "wiup_geoportal.iup_year"),
    ("backtrack_top_union", "kelas", "Kelas izin (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI).", None, "klasifikasi_izin.kelas"),
    ("backtrack_top_union", "durasi_sk", "Masa berlaku SK dalam tahun. Bukti internal yang berguna: IUP Operasi Produksi PEMBERIAN PERTAMA menurut UU 4/2009 Ps. 47 berjangka 20 tahun, jadi SK berjangka 10-11 tahun hampir pasti bukan pemberian pertama. NULL bila tak bisa dihitung.", None, "klasifikasi_izin.durasi_sk"),
    ("backtrack_top_union", "loss_2009_2025_ha", "Kehilangan Hansen 2009-2025 di poligon ini, BEBAS METODE (tanpa atribusi) — pembanding netral untuk ketiga kolom loss_*_ha.", "Σ loss [2009, 2025]", "wiup_loss_yearly"),
    ("backtrack_top_union", "n_top10_metode", "Di berapa metode (0-3) konsesi ini masuk 10 besar. 3 = disepakati semua metode; 1 = hanya satu metode yang menyorotinya.", "count(peringkat_* <= 10)", None),
    ("backtrack_top_union", "peringkat_citra", "Peringkat PENUH (1..n seluruh konsesi metode itu, bukan 1..10) menurut kehilangan jendela [mulai aktif Deteksi Hansen, 2025]. Peringkat penuh sengaja disimpan: 'peringkat 1 di Hansen, peringkat 94 di Polos' jauh lebih informatif daripada sel kosong. NULL bila metode ini tak punya tahun mulai untuk konsesi tsb.", None, "scripts/build_laju_izin.py"),
    ("backtrack_top_union", "mulai_citra", "Tahun mulai aktif versi Deteksi Hansen.", None, "laju_izin_konsesi.mulai"),
    ("backtrack_top_union", "loss_citra_ha", "Kehilangan jendela [mulai aktif Deteksi Hansen, 2025] konsesi ini.", "Σ loss [mulai, 2025]", "wiup_loss_yearly"),
    ("backtrack_top_union", "peringkat_indikasi", "Peringkat penuh versi Indikasi kelas izin (lihat peringkat_citra).", None, None),
    ("backtrack_top_union", "mulai_indikasi", "Tahun mulai aktif versi Indikasi kelas izin.", None, "atribusi_izin_aktif.mulai"),
    ("backtrack_top_union", "loss_indikasi_ha", "Kehilangan jendela [mulai aktif Indikasi, 2025] konsesi ini.", "Σ loss [mulai, 2025]", "wiup_loss_yearly"),
    ("backtrack_top_union", "peringkat_polos", "Peringkat penuh versi Polos — tahun SK saja (lihat peringkat_citra).", None, None),
    ("backtrack_top_union", "mulai_polos", "Tahun mulai aktif versi Polos = max(2009, tahun SK).", None, "atribusi_izin_aktif.mulai"),
    ("backtrack_top_union", "loss_polos_ha", "Kehilangan jendela [max(2009, tahun SK), 2025] konsesi ini.", "Σ loss [mulai, 2025]", "wiup_loss_yearly"),

    # ── laju_izin_eventstudy: loss per tahun-relatif-izin ───────────────────────
    ("laju_izin_eventstudy", "kelas", "Kelas izin (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI) + rollup 'SEMUA'.", None, "atribusi_izin_aktif.kelas"),
    ("laju_izin_eventstudy", "rel_year", "Tahun relatif terbit izin: tahun kalender − iup_year, jangkauan −10..+16. t=0 PERPANJANGAN = SK perpanjangan (sisi pra tercemar).", "year − iup_year", "scripts/build_laju_izin.py"),
    ("laju_izin_eventstudy", "n", "Konsesi kohort (iup_year 2009-2025) yang tahun kalendernya masuk 2001-2025 di rel ini (at risk).", "count", "scripts/build_laju_izin.py"),
    ("laju_izin_eventstudy", "sum_loss_ha", "Σ loss Hansen (kotor) di rel ini.", "Σ loss_ha", "wiup_loss_yearly"),
    ("laju_izin_eventstudy", "mean_loss_ha", "Rata-rata loss per konsesi at-risk (kotor).", "sum/n", "scripts/build_laju_izin.py"),
    ("laju_izin_eventstudy", "n_tanpa_sawit_sampai_2021", "Konsesi at-risk yang tahun kalendernya ≤ 2021 (jangkauan Descals; eks n_bersih — 'bersih' pensiun, DECISIONS 13 Agu).", "count year ≤ 2021", "scripts/build_laju_izin.py"),
    ("laju_izin_eventstudy", "sum_tanpa_sawit_sampai_2021_ha", "Σ max(0, loss − sawit) di rel ini (tahun ≤ 2021; eks sum_bersih_ha).", "Σ max(0, loss − sawit)", "wiup_loss_yearly × atribusi_sawit_yearly"),
    ("laju_izin_eventstudy", "mean_tanpa_sawit_sampai_2021_ha", "Rata-rata loss tanpa-sawit per konsesi at-risk ≤2021 (eks mean_bersih_ha).", "sum_tanpa_sawit_sampai_2021_ha / n_tanpa_sawit_sampai_2021", "scripts/build_laju_izin.py"),

    # ── periode_signifikansi: uji beda antar periode P1/P2/P3 ───────────────────
    ("periode_signifikansi", "metrik", "Metrik yang diuji beda antar-periode: rate_tahun_izin_sampai_2025_ha_per_year, loss_2001_2025_ha (varian _bersih: loss_2001_2021_tanpa_sawit_ha — jendelanya memang beda), atau luas_sk.",
     None, "wiup_temporal / wiup_loss / wiup_geoportal"),
    ("periode_signifikansi", "uji", "Nama uji statistik: 'kruskal-wallis' (P1 vs P2 vs P3 sekaligus) atau 'mann-whitney-u' (pairwise).",
     None, "scipy.stats.kruskal / scipy.stats.mannwhitneyu"),
    ("periode_signifikansi", "grup_a", "Kode periode/grup A. Untuk baris kruskal-wallis bernilai 'P1|P2|P3' (uji gabungan 3 periode); untuk baris mann-whitney-u salah satu dari P1/P2/P3.",
     None, None),
    ("periode_signifikansi", "grup_b", "Kode periode/grup B pembanding. Untuk baris kruskal-wallis bernilai '-' (tidak dipakai); untuk baris mann-whitney-u salah satu dari P1/P2/P3.",
     None, None),
    ("periode_signifikansi", "n_a", "Ukuran sampel grup A (jumlah konsesi dgn nilai metrik non-null). Untuk baris kruskal-wallis: total sampel gabungan 3 periode.",
     None, None),
    ("periode_signifikansi", "n_b", "Ukuran sampel grup B. Untuk baris kruskal-wallis selalu 0 (tidak dipakai).", None, None),
    ("periode_signifikansi", "statistik", "Nilai statistik uji: H (Kruskal-Wallis) atau U (Mann-Whitney).", None, None),
    ("periode_signifikansi", "p_value", "p-value mentah uji, SEBELUM koreksi multiple-comparison.", None, None),
    ("periode_signifikansi", "p_adjusted", "p-value terkoreksi Holm (baris mann-whitney-u); sama dengan p_value pada baris kruskal-wallis.",
     "koreksi Holm: urutkan p naik, p_adj = max((m−rank)·p, p_adj_sebelumnya), dibatasi ≤1", None),
    ("periode_signifikansi", "signifikan_005", "1 jika p_adjusted < 0,05 (signifikan pada α=5%), else 0.", "p_adjusted < 0.05", None),

    # ── wiup_loss: kehilangan tutupan pohon TOTAL per konsesi 2001-2025 ─────────
    ("wiup_loss", "kode_wiup", "Kode unik WIUP (kunci utama konsesi, dipakai join ke semua tabel lain).", None, None),
    ("wiup_loss", "polygon_area_ha", "Luas poligon konsesi hasil overlay ke grid raster Hansen (piksel di dalam poligon, latitude-corrected) — independen status hutan; bisa beda tipis dari luas_sk (dokumen SK).",
     "Σ luas piksel raster (30m) di dalam poligon", "Hansen GFC v1.13 × poligon WIUP (scripts/batch_analyze.py)"),
    ("wiup_loss", "forest_2000_ha", "Luas tutupan pohon tahun 2000 di dalam poligon (definisi 'hutan' = kanopi ≥ threshold, default 30%).",
     "Σ luas piksel dengan treecover2000 ≥ threshold, di dalam poligon", "Hansen treecover2000 × poligon WIUP"),
    ("wiup_loss", "loss_2001_2025_ha", "Total kehilangan tutupan pohon 2001-2025 di dalam poligon (hanya piksel hutan 2000) — dasar angka headline tesis. (Eks total_loss_ha — penamaan jendela eksplisit, Fase B.)",
     "Σ_tahun loss_ha (2001-2025)", "Hansen lossyear × poligon WIUP (= Σ wiup_loss_yearly.loss_ha)"),
    ("wiup_loss", "loss_pct_poligon_2001_2025", "Persen luas POLIGON (bukan hutan) yang hilang tutupan pohonnya.",
     "100 · loss_2001_2025_ha / polygon_area_ha", "wiup_loss"),
    ("wiup_loss", "loss_2001_2025_pct_hutan2000", "Persen tutupan pohon 2000 yang hilang 2001-2025 (eks loss_pct_hutan2000, eks-eks loss_pct_of_forest — jendela pembilang masuk nama) — headline 40,7% dihitung dari agregat kolom ini.",
     "100 · loss_2001_2025_ha / forest_2000_ha", "wiup_loss"),
    ("wiup_loss", "tiles", "Daftar tile Hansen (grid 10°×10°) yang overlap poligon konsesi (dipisah '|'; >1 tile jika konsesi lintas-tile).",
     None, "Hansen GFC v1.13 tiling"),
    ("wiup_loss", "threshold", "Ambang tutupan kanopi (%) dipakai mendefinisikan 'hutan' tahun 2000 (default 30).", None, "scripts/batch_analyze.py --threshold"),
    ("wiup_loss", "hansen_version", "Versi dataset Hansen Global Forest Change dipakai (GFC-2025-v1.13).", None, None),

    # ── wiup_loss_yearly: kehilangan tutupan pohon per konsesi PER TAHUN ────────
    ("wiup_loss_yearly", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal).", None, None),
    ("wiup_loss_yearly", "year", "Tahun kalender kehilangan tutupan pohon (2001-2025).", None, "band 'lossyear' Hansen GFC"),
    ("wiup_loss_yearly", "loss_ha", "Kehilangan tutupan pohon (ha) konsesi itu pada tahun itu (hanya piksel hutan-2000).",
     "Σ luas piksel forest_2000 dengan lossyear = tahun", "Hansen lossyear × treecover2000 × poligon WIUP"),

    # ── wiup_temporal: laju deforestasi pra- vs pasca-terbit izin per konsesi ──
    ("wiup_temporal", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal).", None, None),
    ("wiup_temporal", "iup_year", "Tahun terbit izin dipakai sbg titik potong pra/pasca (disalin dari wiup_geoportal.iup_year).",
     None, "wiup_geoportal.iup_year"),
    ("wiup_temporal", "loss_2001_sampai_tahun_izin_ha", "Total loss (ha) jendela PRA-izin: 2001 s/d tahun sebelum izin terbit (iup_year−1).", "Σ loss_ha, tahun < iup_year", "wiup_loss_yearly"),
    ("wiup_temporal", "loss_tahun_izin_sampai_2025_ha", "Total loss (ha) jendela PASCA-izin: tahun izin terbit (iup_year, inklusif) s/d 2025.", "Σ loss_ha, tahun ≥ iup_year", "wiup_loss_yearly"),
    ("wiup_temporal", "n_tahun_dari_2001_sampai_tahun_izin", "Jumlah tahun observasi sebelum iup_year (dalam jendela 2001-2025).",
     "count(tahun < iup_year), tahun ∈ [2001,2025]", None),
    ("wiup_temporal", "n_tahun_dari_tahun_izin_sampai_2025", "Jumlah tahun observasi sejak iup_year (dalam jendela 2001-2025).",
     "count(tahun ≥ iup_year), tahun ∈ [2001,2025]", None),
    ("wiup_temporal", "loss_2009_sampai_tahun_izin_ha", "Loss pra-izin diklip era Minerba: Σ loss 2009..iup_year−1 (identitas dari wiup_loss_yearly; 0 bila iup ≤ 2009).", "Σ loss_ha 2009..iup−1", "wiup_loss_yearly"),
    ("wiup_temporal", "n_tahun_dari_2009_sampai_tahun_izin", "Panjang jendela pra-izin era Minerba: max(0, iup_year − 2009) (eks n_years_pre_2009 — pola nama 'dari X sampai Y' + jangkar tahun_izin, DECISIONS 13 Agu).", "iup_year − 2009", "wiup_geoportal.iup_year"),
    ("wiup_temporal", "rate_2009_sampai_tahun_izin_ha_per_year", "Laju pra-izin era Minerba: loss_2009_sampai_tahun_izin_ha / n_tahun_dari_2009_sampai_tahun_izin (NULL bila jendela kosong).", "loss/n", "wiup_temporal"),
    ("wiup_temporal", "rate_2001_sampai_tahun_izin_ha_per_year", "Laju deforestasi rata-rata SEBELUM izin (ha/tahun).",
     "loss_2001_sampai_tahun_izin_ha / n_tahun_dari_2001_sampai_tahun_izin", None),
    ("wiup_temporal", "rate_tahun_izin_sampai_2025_ha_per_year", "Laju deforestasi rata-rata SETELAH izin (ha/tahun) — metrik utama analisis periode & Komparasi.",
     "loss_tahun_izin_sampai_2025_ha / n_tahun_dari_tahun_izin_sampai_2025", None),
    ("wiup_temporal", "ratio_laju_sesudah_vs_sebelum_tahun_izin", "Rasio laju pasca:pra-izin (>1 = akselerasi pasca-izin).",
     "rate_tahun_izin_sampai_2025_ha_per_year / rate_2001_sampai_tahun_izin_ha_per_year (∞ jika pre=0 & post>0)", None),
    ("wiup_temporal", "verdict", "Kategori pola temporal per konsesi: accelerated_post_iup ('Dipercepat setelah izin', ratio>1,5), loss_only_after_iup ('Kerusakan hanya setelah izin', pre=0 & post>0), decelerated_post_iup ('Melambat setelah izin', ratio<0,67), stable ('stabil'), no_loss_either, no_iup_date_or_out_of_range.",
     "aturan ambang atas ratio_laju_sesudah_vs_sebelum_tahun_izin (lihat scripts/temporal_iup.py)", "scripts/temporal_iup.py"),

    # ── wiup_geoportal: atribut & poligon 825 WIUP dari Geoportal ESDM ──────────
    ("wiup_geoportal", "kode_wiup", "Kode unik WIUP (kunci utama konsesi, dipakai join ke semua tabel).", None, None),
    ("wiup_geoportal", "nama_usaha", "Nama badan usaha pemegang izin (dari Geoportal; bisa beda ejaan dgn MinerbaOne — lihat wiup_match).", None, None),
    ("wiup_geoportal", "sk_iup", "Nomor SK (surat keputusan) izin usaha pertambangan.", None, None),
    ("wiup_geoportal", "komoditas", "Jenis komoditas tambang (mis. BATUBARA, BAUKSIT, EMAS).",
     None, "layer Geoportal WIUP_Publish, dinormalkan UPPER saat ingest agar GROUP BY tak terpecah ('Batubara' vs 'BATUBARA')"),
    ("wiup_geoportal", "nama_prov", "Nama provinsi lokasi konsesi (mentah dari Geoportal).", None, None),
    ("wiup_geoportal", "nama_kab", "Nama kabupaten/kota lokasi konsesi (mentah dari Geoportal, ejaan bisa bervariasi).", None, None),
    ("wiup_geoportal", "kab_normalized", "Nama kabupaten/kota versi baku, dipakai join ke kepadatan_penduduk.",
     None, "normalisasi nama_kab saat ingest"),
    ("wiup_geoportal", "luas_sk", "Luas konsesi menurut dokumen SK (ha) — beda dari polygon_area_ha (hasil overlay raster GIS).", None, None),
    ("wiup_geoportal", "tgl_berlak_ms", "Tanggal mulai berlaku izin, format epoch milidetik (field mentah layer Geoportal).", None, None),
    ("wiup_geoportal", "tgl_akhir_ms", "Tanggal akhir berlaku izin, format epoch milidetik (field mentah layer Geoportal).", None, None),
    ("wiup_geoportal", "iup_year", "Tahun terbit/berlaku izin — dasar pengelompokan periode kewenangan (P1/P2/P3/Pra-2009).",
     "tahun dari field 'tgl_berlaku' (ISO 'YYYY-MM-DD') jika ada, jika tidak dari 'tgl_berlak' (epoch ms)",
     "layer Geoportal WIUP_Publish (scripts/build_combined_db.py step_geoportal)"),
    ("wiup_geoportal", "cnc", "Status Clean and Clear (kode administratif kelayakan izin, mis. 'CNC-7').", None, None),
    ("wiup_geoportal", "jenis_izin", "Jenis izin usaha pertambangan (IUP/IUPK/PKP2B/KK/WIUP/IPR).", None, None),
    ("wiup_geoportal", "kegiatan", "Tahap kegiatan izin saat data diambil (mis. OPERASI PRODUKSI, EKSPLORASI, SANKSI).", None, None),
    ("wiup_geoportal", "pulau", "Pulau lokasi konsesi (selalu 'KALIMANTAN' pada dataset ini — filter cakupan analisis).", None, None),
    ("wiup_geoportal", "lokasi", "Deskripsi lokasi administratif konsesi (teks bebas dari Geoportal, mis. kecamatan/kabupaten).", None, None),
    ("wiup_geoportal", "kode_prov", "Kode provinsi mentah dari Geoportal (umumnya kosong pada layer WIUP_Publish).", None, None),
    ("wiup_geoportal", "kode_golon", "Kode golongan komoditas mentah dari Geoportal (umumnya kosong).", None, None),
    ("wiup_geoportal", "kode_jnsko", "Kode jenis komoditas mentah dari Geoportal (umumnya kosong).", None, None),
    ("wiup_geoportal", "badan_usah", "Nama badan usaha versi field Geoportal alternatif (umumnya kosong; pakai nama_usaha).", None, None),
    ("wiup_geoportal", "pejabat", "Pejabat penerbit izin: BUPATI (kewenangan kabupaten), GUBERNUR (provinsi), atau MENTERI (pusat) — mencerminkan 3 periode kewenangan.",
     None, "layer Geoportal WIUP_Publish, dinormalkan UPPER saat ingest"),
    ("wiup_geoportal", "bbox_min_lon", "Batas kotak pembatas (bounding box) geometri konsesi — sisi longitude minimum; dipakai memilih tile Hansen yang overlap.",
     None, "dihitung dari geometry_geojson"),
    ("wiup_geoportal", "bbox_min_lat", "Batas kotak pembatas (bounding box) geometri konsesi — sisi latitude minimum.", None, "dihitung dari geometry_geojson"),
    ("wiup_geoportal", "bbox_max_lon", "Batas kotak pembatas (bounding box) geometri konsesi — sisi longitude maksimum.", None, "dihitung dari geometry_geojson"),
    ("wiup_geoportal", "bbox_max_lat", "Batas kotak pembatas (bounding box) geometri konsesi — sisi latitude maksimum.", None, "dihitung dari geometry_geojson"),
    ("wiup_geoportal", "tiles", "Daftar tile Hansen (grid 10°×10°) yang overlap bounding box konsesi.", None, "dihitung dari bbox"),
    ("wiup_geoportal", "geometry_type", "Tipe geometri poligon (Polygon atau MultiPolygon).", None, None),
    ("wiup_geoportal", "geometry_geojson", "Geometri poligon konsesi, format GeoJSON (dipakai peta web).", None, "data/wiup/kalimantan_unique.geojson"),

    # ── wiup_match: pencocokan WIUP ↔ MinerbaOne (badan usaha) ──────────────────
    ("wiup_match", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal).", None, None),
    ("wiup_match", "sk_iup", "Nomor SK konsesi, dipakai sbg kunci pencocokan ke perizinan.nomor_izin.", None, None),
    ("wiup_match", "id_perizinan", "ID izin MinerbaOne hasil pencocokan (NULL jika tak cocok).", None, None),
    ("wiup_match", "id_badan_usaha", "ID badan usaha MinerbaOne hasil pencocokan (NULL jika tak cocok).", None, None),
    ("wiup_match", "db_match", "'yes' jika konsesi berhasil dicocokkan ke suatu izin MinerbaOne, else 'no' (768/825 cocok, 93,1%).",
     "'yes' jika sk_iup (atau strategi match_strategy) ditemukan padanannya di perizinan.nomor_izin, else 'no'",
     "scripts/build_combined_db.py step_match + scripts/match_harder.py"),
    ("wiup_match", "minerbaone_url", "Tautan halaman detail badan usaha di MinerbaOne publik (terisi bila db_match='yes' & id_badan_usaha ada).", None, None),
    ("wiup_match", "match_strategy", "Strategi pencocokan yang berhasil: T0_exact (SK identik persis), T1_norm_sk (SK dinormalkan), T2_fuzzy_name (kecocokan nama badan usaha), T3_digits (SK dibandingkan digit saja); NULL jika tak cocok.",
     None, "scripts/match_harder.py"),

    # ── atribusi_sawit: kehilangan tutupan pohon per konsesi diatribusi ke sawit ──
    # LAPISAN opsional (Descals dkk. 2024) — mengaudit apakah kehilangan di dalam
    # batas WIUP (administratif) sebetulnya konversi kelapa sawit, bukan tambang.
    ("atribusi_sawit", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal; kunci utama tabel).", None, None),
    ("atribusi_sawit", "loss_2001_2021_ha",
     "Kehilangan tutupan pohon 2001-2021 di dalam konsesi yang BISA diperiksa "
     "terhadap peta sawit Descals (peta berhenti di tahun 2021) — subset dari "
     "wiup_loss.loss_2001_2025_ha, threshold kanopi & sumber piksel SAMA PERSIS.",
     "Σ luas piksel hutan-2000 (kanopi≥30%) dgn lossyear 2001-2021",
     "Hansen lossyear × treecover2000 × poligon WIUP (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_tol2th_2001_2021_ha",
     "Kehilangan 2001-2021 yang piksel-nya juga menjadi sawit menurut Descals dkk. "
     "(2024), varian TOLERAN (UTAMA/patokan): tahun tanam sawit (YoP) boleh "
     "mendahului tahun kehilangan hingga 2 tahun — mengakomodasi RMSE deteksi "
     "tahun tanam Descals (2,02 th perkebunan industri / 4,89 th rakyat).",
     "Σ luas piksel dgn YoP ≥ tahun_loss − 2, dari subset loss_2001_2021_ha",
     "Descals dkk. (2024) tahun-tanam sawit × Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_jeda5th_2001_2021_ha",
     "Idem loss_sawit_tol2th_2001_2021_ha, varian PALING KETAT (BATAS BAWAH sensitivitas): tahun "
     "tanam (YoP) tak boleh mendahului tahun kehilangan sama sekali, dan maksimal 5 "
     "tahun sesudahnya — subset dari loss_sawit_tahunsama_2001_2021_ha (jeda5th ⊆ tahunsama ⊆ tol2th).",
     "Σ luas piksel dgn tahun_loss ≤ YoP ≤ tahun_loss + 5",
     "Descals dkk. (2024) × Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_tahunsama_2001_2021_ha",
     "Idem loss_sawit_tol2th_2001_2021_ha, varian TANPA TOLERANSI MUNDUR (TENGAH): tahun tanam "
     "(YoP) harus ≥ tahun kehilangan, tak boleh mendahului sama sekali (tanpa batas atas).",
     "Σ luas piksel dgn YoP ≥ tahun_loss",
     "Descals dkk. (2024) × Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_2022_2025_ha",
     "Kehilangan tutupan pohon 2022-2025 — TAK BISA diperiksa terhadap sawit sama "
     "sekali (Descals berhenti 2021); disimpan terpisah, BUKAN digabung diam-diam "
     "ke penyebut pangsa sawit (persen_sawit_2001_2021).",
     "Σ luas piksel hutan-2000 dgn lossyear 2022-2025", "Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "n_tile_hansen",
     "Jumlah tile Hansen (grid 10°×10°) yang disentuh konsesi ini (>1 bila konsesi "
     "lintas-tile — 16/825 konsesi begini; ditangani via clip-per-tile, bukan dilewati).",
     None, "scripts/attribution_sawit.py (_geo_common.pick_tile)"),

    # ── Task F15: silang dua sumbu pra/pasca-izin × sawit (jendela eksplisit) ──
    ("atribusi_sawit", "loss_sawit_2001_sampai_tahun_izin_ha",
     "Kehilangan yang teratribusi ke sawit (varian tol2th/UTAMA) pada jendela "
     "PRA-izin: tahun kalender 2001 s/d min(iup_year−1, 2021) — batas atas 2021 "
     "krn Descals berhenti di situ, jadi jendela ini bisa jadi PENUH 2001-2021 "
     "kalau iup_year > 2022 (lihat kolom bersih di wiup_master). NULL kalau "
     "iup_year konsesi ini NULL (bukan 0 — beda makna dgn 'sawit=0 tapi "
     "iup_year diketahui').",
     "Σ atribusi_sawit_yearly.loss_sawit_tol2th_ha utk tahun ∈ [2001, min(iup_year−1,2021)]",
     "atribusi_sawit_yearly × wiup_geoportal.iup_year (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_tahun_izin_sampai_2021_ha",
     "Kehilangan yang teratribusi ke sawit (varian tol2th/UTAMA) pada jendela "
     "PASCA-izin YANG BISA DIPERIKSA: tahun izin terbit (iup_year, inklusif) s/d "
     "2021 — BUKAN s/d 2025 (Descals berhenti 2021; sisa 2022-2025 tetap di "
     "loss_2022_2025_ha, tak masuk sini). NULL kalau iup_year NULL; 0 (bukan "
     "NULL) kalau iup_year > 2021 (jendela ini kosong, bukan tak diketahui).",
     "Σ atribusi_sawit_yearly.loss_sawit_tol2th_ha utk tahun ∈ [max(iup_year,2001), 2021]",
     "atribusi_sawit_yearly × wiup_geoportal.iup_year (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_2009_2021_ha", "Loss Hansen di irisan era Minerba × jangkauan Descals (2009-2021) — penyebut sawit versi jendela 2009.", "Σ loss_ha 2009-2021", "wiup_loss_yearly"),
    ("atribusi_sawit", "loss_sawit_2009_2021_ha", "Bagian loss 2009-2021 yang bertepatan jadi sawit (varian tol2th).", "Σ loss_sawit_tol2th_2001_2021_ha 2009-2021", "atribusi_sawit_yearly"),
    ("atribusi_sawit", "loss_tahun_izin_sampai_2021_ha",
     "Total kehilangan tutupan pohon Hansen (BUKAN teratribusi sawit — jendela "
     "SAMA PERSIS dgn loss_sawit_tahun_izin_sampai_2021_ha, iup_year..2021) — PENYEBUT "
     "'bersih pasca-izin s/d 2021' (lihat wiup_master.loss_tahun_izin_sampai_2021_tanpa_sawit_ha). "
     "Dihitung dari wiup_loss_yearly (TANPA pemindaian raster baru). NULL kalau "
     "iup_year NULL.",
     "Σ wiup_loss_yearly.loss_ha utk tahun ∈ [max(iup_year,2001), 2021]",
     "wiup_loss_yearly × wiup_geoportal.iup_year (scripts/attribution_sawit.py)"),

    # ── klasifikasi_izin: vonis izin PERTAMA vs PERPANJANGAN per konsesi ────────
    # LAPISAN opsional — mengaudit apakah iup_year benar berarti "tahun izin
    # pertama terbit", dasar sahnya pengelompokan 3-periode.
    ("klasifikasi_izin", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal; kunci utama tabel).", None, None),
    ("klasifikasi_izin", "kelas",
     "Vonis apakah iup_year konsesi ini adalah izin PERTAMA atau PERPANJANGAN, tiga nilai: "
     "PERPANJANGAN — payung 'bukan pemberian pertama'; bentuk persisnya (perpanjangan "
     "keberapa? pendaftaran ulang?) tak terpastikan dari registri. "
     "IZIN_PERTAMA — konsisten sebagai pemberian pertama; konsisten, bukan terbukti "
     "(registri tak menyimpan sejarah izin, jadi tetap bisa saja perpanjangan yang "
     "kebetulan berjangka panjang). "
     "TAK_DINILAI — tak bisa divonis (tahap eksplorasi berjangka legal pendek, "
     "tanggal berlaku/berakhir tak lengkap, atau jenis izin lain). "
     "Lihat kolom `bukti` utk kekuatan tiap vonis (kecuali TAK_DINILAI).",
     "vonis(): PKP2B/KK ber-iup_year≥2009 → PERPANJANGAN; lalu durasi SK Operasi "
     "Produksi vs 20 th (UU 4/2009 Ps.47) → PERPANJANGAN/IZIN_PERTAMA; selainnya → TAK_DINILAI",
     "wiup_master (jenis_izin, iup_year, nama_tahap_kegiatan, durasi_sk) — scripts/klasifikasi_perpanjangan.py"),
    ("klasifikasi_izin", "bukti",
     "Kekuatan bukti vonis `kelas` (NULL bila kelas=TAK_DINILAI), dua nilai: "
     "KUAT — kemustahilan logis: jenis izin PKP2B/KK (sistem kontrak karya UU "
     "11/1967) tapi iup_year≥2009, padahal sistem itu sudah BERHENTI terbit sejak "
     "UU 4/2009, jadi tahun tercatat pasti bukan pemberian pertama (bukan dugaan, "
     "melainkan kemustahilan logis). "
     "INDIKASI — inferensi dari durasi SK Operasi Produksi dibandingkan jangka 20 "
     "tahun pemberian pertama (UU 4/2009 Ps.47): petunjuk kuat, tapi tetap inferensi "
     "hukum, bukan dokumen yang menyatakan langsung 'ini perpanjangan'.",
     None, "scripts/klasifikasi_perpanjangan.py (vonis())"),
    ("klasifikasi_izin", "dasar", "Penjelasan 1-2 kalimat alasan vonis `kelas`+`bukti` baris ini (teks bebas).",
     None, "scripts/klasifikasi_perpanjangan.py (vonis())"),
    ("klasifikasi_izin", "durasi_sk",
     "Jangka waktu SK (tahun) = tahun tanggal_berakhir − tahun tanggal_berlaku "
     "(MinerbaOne); NULL bila salah satu tanggal tak lengkap.",
     "tahun(tanggal_berakhir) − tahun(tanggal_berlaku)", "wiup_master.tanggal_berlaku/tanggal_berakhir"),
    ("klasifikasi_izin", "masa_berlaku_diwarisi",
     "Bendera PELENGKAP (tidak menentukan `kelas`): 1 jika tahun mulai berlaku "
     "(tanggal_berlaku) LEBIH AWAL dari iup_year — izin 'baru' yang membawa masa "
     "berlaku pendahulunya.",
     "1 jika tahun(tanggal_berlaku) < iup_year, else 0", "wiup_master.tanggal_berlaku, iup_year"),
    ("klasifikasi_izin", "pra_izin_dominan",
     "Bendera PELENGKAP (tidak menentukan `kelas`): 1 jika >50% kehilangan Hansen "
     "konsesi ini terjadi SEBELUM iup_year — satelit menguatkan kegiatan sudah "
     "berjalan lebih dulu. NULL bila konsesi tak punya kehilangan sama sekali.",
     "1 jika loss_2001_sampai_tahun_izin_ha/(loss_2001_sampai_tahun_izin_ha+loss_tahun_izin_sampai_2025_ha) > 0,5, else 0; NULL bila penyebut=0",
     "wiup_master.loss_2001_sampai_tahun_izin_ha, loss_tahun_izin_sampai_2025_ha"),

    # ── periode_sawit: agregasi atribusi_sawit per periode kewenangan izin ──────
    ("periode_sawit", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_sawit", "n_konsesi", "Jumlah konsesi periode yang punya data atribusi sawit (baris di atribusi_sawit).",
     "count(kode_wiup) dari atribusi_sawit dengan to_periode(iup_year)=periode", "atribusi_sawit × wiup_geoportal.iup_year"),
    ("periode_sawit", "loss_2001_2021_ha", "Total kehilangan tutupan pohon 2001-2021 (bisa diperiksa thd sawit) seluruh konsesi periode.",
     "Σ atribusi_sawit.loss_2001_2021_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_sawit_tol2th_2001_2021_ha",
     "Total kehilangan 2001-2021 periode yang teratribusi ke sawit, varian TOLERAN (UTAMA/patokan, YoP ≥ tahun_loss−2).",
     "Σ atribusi_sawit.loss_sawit_tol2th_2001_2021_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_sawit_jeda5th_2001_2021_ha",
     "Idem, varian PALING KETAT/batas bawah (tahun_loss ≤ YoP ≤ tahun_loss+5).",
     "Σ atribusi_sawit.loss_sawit_jeda5th_2001_2021_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_sawit_tahunsama_2001_2021_ha",
     "Idem, varian tanpa toleransi mundur/tengah (YoP ≥ tahun_loss).",
     "Σ atribusi_sawit.loss_sawit_tahunsama_2001_2021_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_2001_2021_tanpa_sawit_ha",
     "Kehilangan 2001-2021 periode SETELAH dikurangi bagian teratribusi ke sawit "
     "(varian tol2th) — perkiraan kehilangan 'murni non-sawit' periode itu.",
     "loss_2001_2021_ha − loss_sawit_tol2th_2001_2021_ha", "kolom pada tabel ini"),
    ("periode_sawit", "persen_sawit_2001_2021",
     "Persen kehilangan periode yang teratribusi ke sawit (varian tol2th/UTAMA). "
     "PENYEBUT: loss_2001_2021_ha (kehilangan 2001-2021 yang bisa diperiksa thd "
     "sawit) — BUKAN luas konsesi, BUKAN hutan tahun 2000. NULL bila loss_2001_2021_ha periode itu = 0.",
     "100 · loss_sawit_tol2th_2001_2021_ha / loss_2001_2021_ha", "kolom pada tabel ini"),
    ("periode_sawit", "loss_2022_2025_ha",
     "Total kehilangan 2022-2025 periode — TAK TERPERIKSA thd sawit sama sekali "
     "(Descals berhenti 2021); disajikan terpisah, tidak masuk penyebut persen_sawit_2001_2021.",
     "Σ atribusi_sawit.loss_2022_2025_ha per periode", "atribusi_sawit"),

    # ── atribusi_sawit_yearly: pecahan PER TAHUN dari loss_sawit_tol2th_2001_2021_ha ──────
    # LAPISAN opsional (Task F1/FASE F) — dasar rumus "loss bersih dari sawit"
    # per tahun, dipakai periode_tahunan_aktif_bersih. Sparse spt wiup_loss_yearly
    # (tahun tanpa loss-sawit tak disimpan, tersirat 0 via COALESCE pemakainya).
    ("atribusi_sawit_yearly", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal).", None, None),
    ("atribusi_sawit_yearly", "year", "Tahun kalender kehilangan (2001-2021 — dibatasi jendela Descals; 2022-2025 tak disimpan di sini sama sekali).",
     None, "band 'lossyear' Hansen GFC, dibatasi ≤2021"),
    ("atribusi_sawit_yearly", "loss_sawit_tol2th_ha",
     "Kehilangan tahun itu yang teratribusi ke sawit, varian TOLERAN/tol2th (UTAMA/patokan, "
     "YoP ≥ tahun_loss−2) — SUM per kode_wiup atas seluruh tahun HARUS = atribusi_sawit.loss_sawit_tol2th_2001_2021_ha "
     "(window sum), diverifikasi (ambang 0,5 ha) sebelum ditulis (lihat cek_konsistensi_tahunan()).",
     "Σ luas piksel dgn YoP ≥ tahun_loss−2, dikelompokkan per tahun_loss",
     "Descals dkk. (2024) × Hansen lossyear (scripts/attribution_sawit.py)"),

    # ── wiup_master (VIEW): gabungan wiup_geoportal × wiup_loss × wiup_temporal × wiup_match ──
    ("wiup_master", "kode_wiup", "Kode unik WIUP (kunci utama konsesi).", None, "wiup_geoportal.kode_wiup"),
    ("wiup_master", "nama_usaha", "Nama badan usaha pemegang izin.", None, "wiup_geoportal.nama_usaha"),
    ("wiup_master", "sk_iup", "Nomor SK izin usaha pertambangan.", None, "wiup_geoportal.sk_iup"),
    ("wiup_master", "komoditas", "Jenis komoditas tambang.", None, "wiup_geoportal.komoditas"),
    ("wiup_master", "nama_prov", "Nama provinsi lokasi konsesi.", None, "wiup_geoportal.nama_prov"),
    ("wiup_master", "nama_kab", "Nama kabupaten/kota lokasi konsesi.", None, "wiup_geoportal.nama_kab"),
    ("wiup_master", "kab_normalized", "Nama kabupaten/kota versi baku (dipakai join Zona).", None, "wiup_geoportal.kab_normalized"),
    ("wiup_master", "luas_sk", "Luas konsesi menurut dokumen SK (ha).", None, "wiup_geoportal.luas_sk"),
    ("wiup_master", "iup_year", "Tahun terbit/berlaku izin — dasar periode kewenangan.", None, "wiup_geoportal.iup_year"),
    ("wiup_master", "cnc", "Status Clean and Clear konsesi.", None, "wiup_geoportal.cnc"),
    ("wiup_master", "jenis_izin", "Jenis izin usaha pertambangan (IUP/IUPK/PKP2B/KK/WIUP/IPR).", None, "wiup_geoportal.jenis_izin"),
    ("wiup_master", "lokasi", "Deskripsi lokasi administratif konsesi.", None, "wiup_geoportal.lokasi"),
    ("wiup_master", "polygon_area_ha", "Luas poligon konsesi hasil overlay raster Hansen.", None, "wiup_loss.polygon_area_ha"),
    ("wiup_master", "forest_2000_ha", "Luas tutupan pohon tahun 2000 di dalam poligon.", None, "wiup_loss.forest_2000_ha"),
    ("wiup_master", "loss_2001_2025_ha", "Total kehilangan tutupan pohon 2001-2025 di dalam poligon — dasar angka headline tesis (eks total_loss_ha).", None, "wiup_loss.loss_2001_2025_ha"),
    ("wiup_master", "loss_2001_2008_ha", "Kehilangan pra-jendela 2001-2008 (konteks, di luar era Minerba).", None, "wiup_loss.loss_2001_2008_ha"),
    ("wiup_master", "hutan_2009_ha", "Hutan yang masih berdiri awal 2009 (= forest_2000 − loss 2001-2008, identitas eksak).", None, "wiup_loss.hutan_2009_ha"),
    ("wiup_master", "loss_2009_2025_ha", "Kehilangan di jendela era Minerba 2009-2025 — kolom utama Fase B.", None, "wiup_loss.loss_2009_2025_ha"),
    ("wiup_master", "loss_2009_2025_pct_hutan2009", "Persen kehilangan 2009-2025 terhadap hutan-2009 (eks loss_pct_hutan2009).", None, "wiup_loss.loss_2009_2025_pct_hutan2009"),
    ("wiup_master", "loss_pct_poligon_2001_2025", "Persen luas poligon yang hilang tutupan pohonnya 2001-2025.", None, "wiup_loss.loss_pct_poligon_2001_2025"),
    ("wiup_master", "loss_2001_2025_pct_hutan2000", "Persen tutupan pohon 2000 yang hilang 2001-2025 (eks loss_pct_hutan2000, eks-eks loss_pct_of_forest) — metrik utama tesis per konsesi.", None, "wiup_loss.loss_2001_2025_pct_hutan2000"),
    ("wiup_master", "hansen_tiles", "Daftar tile Hansen yang overlap poligon konsesi.", None, "wiup_loss.tiles (alias hansen_tiles)"),
    ("wiup_master", "loss_2009_sampai_tahun_izin_ha", "Loss PRA-izin diklip ke era Minerba (Σ 2009..iup_year−1; 0 bila iup ≤ 2009, NULL bila iup NULL).", None, "wiup_temporal.loss_2009_sampai_tahun_izin_ha"),
    ("wiup_master", "rate_2009_sampai_tahun_izin_ha_per_year", "Laju pra-izin versi era Minerba: loss_pre_iup_2009 / (iup_year − 2009); NULL bila jendela kosong.", None, "wiup_temporal.rate_2009_sampai_tahun_izin_ha_per_year"),
    ("wiup_master", "loss_2001_sampai_tahun_izin_ha", "Total loss (ha) jendela PRA-izin: 2001 s/d tahun sebelum izin terbit (iup_year−1) — BUKAN bagian dari loss_2001_2025_ha yang terjadi di bawah izin ini.", None, "wiup_temporal.loss_2001_sampai_tahun_izin_ha"),
    ("wiup_master", "loss_tahun_izin_sampai_2025_ha", "Total loss (ha) jendela PASCA-izin: tahun izin terbit (iup_year, inklusif) s/d 2025 — beda dgn loss_2001_2025_ha yang mencakup 2001–2025 penuh termasuk pra-izin.", None, "wiup_temporal.loss_tahun_izin_sampai_2025_ha"),
    ("wiup_master", "rate_2001_sampai_tahun_izin_ha_per_year", "Laju deforestasi rata-rata sebelum izin (ha/tahun).", None, "wiup_temporal.rate_2001_sampai_tahun_izin_ha_per_year"),
    ("wiup_master", "rate_tahun_izin_sampai_2025_ha_per_year", "Laju deforestasi rata-rata setelah izin (ha/tahun) — metrik utama Komparasi & peta.", None, "wiup_temporal.rate_tahun_izin_sampai_2025_ha_per_year"),
    ("wiup_master", "ratio_laju_sesudah_vs_sebelum_tahun_izin", "Rasio laju pasca:pra-izin (>1 = akselerasi pasca-izin).", None, "wiup_temporal.ratio_laju_sesudah_vs_sebelum_tahun_izin"),
    ("wiup_master", "temporal_verdict", "Kategori pola temporal konsesi (accelerated_post_iup/loss_only_after_iup/decelerated_post_iup/stable/dst).",
     None, "wiup_temporal.verdict (alias temporal_verdict)"),
    ("wiup_master", "db_match", "'yes'/'no' — apakah konsesi cocok ke suatu izin MinerbaOne.", None, "wiup_match.db_match"),
    ("wiup_master", "minerbaone_url", "Tautan halaman detail badan usaha di MinerbaOne publik (bila db_match='yes').", None, "wiup_match.minerbaone_url"),
    ("wiup_master", "id_badan_usaha", "ID badan usaha MinerbaOne hasil pencocokan.", None, "wiup_match.id_badan_usaha"),
    ("wiup_master", "nama_badan_usaha", "Nama badan usaha versi MinerbaOne (kosong jika db_match='no').", None, "badan_usaha.nama_badan_usaha"),
    ("wiup_master", "nib", "Nomor Induk Berusaha badan usaha (kosong jika db_match='no').", None, "badan_usaha.nib"),
    ("wiup_master", "npwp_badan_usaha", "NPWP badan usaha (kosong jika db_match='no').", None, "badan_usaha.npwp_badan_usaha"),
    ("wiup_master", "alamat", "Alamat badan usaha (kosong jika db_match='no').", None, "badan_usaha.alamat"),
    ("wiup_master", "kode_pos", "Kode pos alamat badan usaha (kosong jika db_match='no').", None, "badan_usaha.kode_pos"),
    ("wiup_master", "no_telp", "Nomor telepon badan usaha (kosong jika db_match='no').", None, "badan_usaha.no_telp"),
    ("wiup_master", "email", "Email badan usaha (kosong jika db_match='no').", None, "badan_usaha.email"),
    ("wiup_master", "jenis_badan_usaha", "Jenis badan usaha (mis. PT, Koperasi) (kosong jika db_match='no').", None, "badan_usaha.jenis_badan_usaha"),
    ("wiup_master", "tanggal_berlaku", "Tanggal mulai berlaku izin versi MinerbaOne (kosong jika db_match='no').", None, "perizinan.tanggal_berlaku"),
    ("wiup_master", "tanggal_berakhir", "Tanggal akhir berlaku izin versi MinerbaOne (kosong jika db_match='no').", None, "perizinan.tanggal_berakhir"),
    ("wiup_master", "tanggal_penetapan", "Tanggal penetapan izin versi MinerbaOne (kosong jika db_match='no').", None, "perizinan.tanggal_penetapan"),
    ("wiup_master", "nama_tahap_kegiatan", "Tahap kegiatan izin versi MinerbaOne (kosong jika db_match='no').", None, "perizinan.nama_tahap_kegiatan"),
    ("wiup_master", "status_cnc", "Status Clean and Clear versi MinerbaOne (kosong jika db_match='no').", None, "perizinan.status_cnc"),
    ("wiup_master", "loss_2001_2021_ha", "Kehilangan tutupan pohon 2001-2021 konsesi ini yang bisa diperiksa terhadap peta sawit Descals (kosong bila lapisan atribusi_sawit belum dibangun).",
     None, "atribusi_sawit.loss_2001_2021_ha"),
    ("wiup_master", "loss_sawit_tol2th_2001_2021_ha", "Kehilangan 2001-2021 konsesi ini yang teratribusi ke sawit, varian TOLERAN (UTAMA/patokan, YoP ≥ tahun_loss−2).",
     None, "atribusi_sawit.loss_sawit_tol2th_2001_2021_ha"),
    ("wiup_master", "loss_sawit_jeda5th_2001_2021_ha", "Idem, varian PALING KETAT/batas bawah (tahun_loss ≤ YoP ≤ tahun_loss+5).",
     None, "atribusi_sawit.loss_sawit_jeda5th_2001_2021_ha"),
    ("wiup_master", "loss_sawit_tahunsama_2001_2021_ha", "Idem, varian tanpa toleransi mundur/tengah (YoP ≥ tahun_loss).",
     None, "atribusi_sawit.loss_sawit_tahunsama_2001_2021_ha"),
    ("wiup_master", "loss_2022_2025_ha", "Kehilangan 2022-2025 konsesi ini — tak terperiksa thd sawit sama sekali (Descals berhenti 2021).",
     None, "atribusi_sawit.loss_2022_2025_ha"),
    ("wiup_master", "loss_2001_2021_tanpa_sawit_ha", "Kehilangan 2001-2021 konsesi ini dikurangi bagian teratribusi ke sawit (varian tol2th).",
     "loss_2001_2021_ha − loss_sawit_tol2th_2001_2021_ha", "view wiup_master (dihitung di CREATE VIEW)"),
    ("wiup_master", "persen_sawit_2001_2021",
     "Persen kehilangan konsesi ini yang teratribusi ke sawit (varian tol2th/UTAMA). "
     "PENYEBUT: loss_2001_2021_ha konsesi ini — BUKAN luas konsesi (luas_sk), BUKAN "
     "hutan 2000 (forest_2000_ha). NULL bila loss_2001_2021_ha=0.",
     "100 · loss_sawit_tol2th_2001_2021_ha / loss_2001_2021_ha", "view wiup_master (dihitung di CREATE VIEW)"),
    ("wiup_master", "loss_2009_2021_ha",
     "Kehilangan konsesi ini pada irisan era Minerba x jangkauan Descals (2009-2021) — "
     "penyebut pangsa sawit versi jendela 2009.",
     "passthrough atribusi_sawit.loss_2009_2021_ha", "atribusi_sawit"),
    ("wiup_master", "loss_sawit_2009_2021_ha",
     "Bagian kehilangan 2009-2021 konsesi ini yang bertepatan jadi sawit (varian tol2th/UTAMA).",
     "passthrough atribusi_sawit.loss_sawit_2009_2021_ha", "atribusi_sawit"),
    ("wiup_master", "persen_sawit_2009_2021",
     "Persen kehilangan 2009-2021 konsesi ini yang teratribusi ke sawit — versi jendela era "
     "Minerba dari persen_sawit_2001_2021. PENYEBUT: loss_2009_2021_ha (BUKAN luas konsesi, "
     "BUKAN hutan 2009). Berhenti 2021 karena peta Descals berhenti 2021: kehilangan 2022-2025 "
     "TAK terperiksa dan tak masuk penyebut. NULL bila loss_2009_2021_ha=0.",
     "100 · loss_sawit_2009_2021_ha / loss_2009_2021_ha", "view wiup_master (dihitung di CREATE VIEW)"),

    # ── Task F15: silang dua sumbu pra/pasca-izin × sawit ───────────────────────
    ("wiup_master", "loss_sawit_2001_sampai_tahun_izin_ha",
     "Alias atribusi_sawit.loss_sawit_2001_sampai_tahun_izin_ha — kehilangan teratribusi sawit "
     "(varian tol2th) pd jendela PRA-izin: 2001 s/d min(iup_year−1, 2021).",
     None, "atribusi_sawit.loss_sawit_2001_sampai_tahun_izin_ha"),
    ("wiup_master", "loss_sawit_tahun_izin_sampai_2021_ha",
     "Alias atribusi_sawit.loss_sawit_tahun_izin_sampai_2021_ha — kehilangan teratribusi "
     "sawit (varian tol2th) pd jendela PASCA-izin yg bisa diperiksa: iup_year..2021 "
     "(BUKAN s/d 2025 — Descals berhenti 2021).",
     None, "atribusi_sawit.loss_sawit_tahun_izin_sampai_2021_ha"),
    ("wiup_master", "loss_tahun_izin_sampai_2021_ha",
     "Alias atribusi_sawit.loss_tahun_izin_sampai_2021_ha — total kehilangan Hansen "
     "(BUKAN teratribusi sawit) pd jendela iup_year..2021, PENYEBUT kolom bersih "
     "di bawah.",
     None, "atribusi_sawit.loss_tahun_izin_sampai_2021_ha"),
    ("wiup_master", "loss_2001_sampai_tahun_izin_tanpa_sawit_ha",
     "Kehilangan jendela PRA-izin (loss_2001_sampai_tahun_izin_ha, F14: 2001 s/d iup_year−1, "
     "TANPA dipotong 2021) dikurangi bagian teratribusi sawit "
     "(loss_sawit_2001_sampai_tahun_izin_ha, dipotong 2021). NULL bila iup_year > 2022 — "
     "jendela pra melewati batas Descals (2021) sehingga TAK SEPENUHNYA "
     "terperiksa thd sawit (bukan 0% sawit, melainkan tak diketahui); NULL "
     "juga bila iup_year konsesi ini NULL.",
     "loss_2001_sampai_tahun_izin_ha − loss_sawit_2001_sampai_tahun_izin_ha (NULL bila iup_year>2022 atau iup_year NULL)",
     "view wiup_master (dihitung di CREATE VIEW; wiup_temporal.loss_2001_sampai_tahun_izin_ha × atribusi_sawit.loss_sawit_2001_sampai_tahun_izin_ha)"),
    ("wiup_master", "loss_tahun_izin_sampai_2021_tanpa_sawit_ha",
     "Kehilangan jendela PASCA-izin s/d 2021 (loss_tahun_izin_sampai_2021_ha, Hansen, "
     "BUKAN loss_tahun_izin_sampai_2025_ha F14 yang s/d 2025 penuh) dikurangi bagian "
     "teratribusi sawit (loss_sawit_tahun_izin_sampai_2021_ha) — keduanya jendela yang "
     "SAMA (iup_year..2021), jadi TAK butuh syarat iup_year≤2022 seperti sisi "
     "pra. Sisa 2022-2025 tetap tak terperiksa, lihat loss_2022_2025_ha.",
     "loss_tahun_izin_sampai_2021_ha − loss_sawit_tahun_izin_sampai_2021_ha",
     "view wiup_master (dihitung di CREATE VIEW)"),

    ("wiup_master", "kelas_izin",
     "Alias klasifikasi_izin.kelas — vonis apakah iup_year konsesi ini izin PERTAMA "
     "atau PERPANJANGAN, tiga nilai: PERPANJANGAN (payung 'bukan pemberian pertama'; "
     "bentuk persis tak terpastikan dari registri), IZIN_PERTAMA (konsisten sebagai "
     "pemberian pertama — konsisten, bukan terbukti), TAK_DINILAI (tak bisa divonis). "
     "Lihat klasifikasi_izin.kelas utk penjelasan lengkap.",
     None, "klasifikasi_izin.kelas"),
    ("wiup_master", "bukti_izin",
     "Alias klasifikasi_izin.bukti — kekuatan vonis kelas_izin: KUAT (kemustahilan "
     "logis PKP2B/KK ber-iup_year≥2009) vs INDIKASI (inferensi dari durasi SK Operasi Produksi).",
     None, "klasifikasi_izin.bukti"),
    ("wiup_master", "dasar_kelas", "Alias klasifikasi_izin.dasar — penjelasan teks 1-2 kalimat vonis kelas_izin.",
     None, "klasifikasi_izin.dasar"),
    ("wiup_master", "durasi_sk", "Alias klasifikasi_izin.durasi_sk — jangka waktu SK (tahun) dari MinerbaOne.",
     None, "klasifikasi_izin.durasi_sk"),
    ("wiup_master", "masa_berlaku_diwarisi", "Alias klasifikasi_izin.masa_berlaku_diwarisi — bendera pelengkap, tak menentukan kelas_izin.",
     None, "klasifikasi_izin.masa_berlaku_diwarisi"),
    ("wiup_master", "pra_izin_dominan", "Alias klasifikasi_izin.pra_izin_dominan — bendera pelengkap, tak menentukan kelas_izin.",
     None, "klasifikasi_izin.pra_izin_dominan"),

    # ── badan_usaha: registry perusahaan MinerbaOne (nasional, 7.572 baris) ────
    # Disalin apa adanya dari scrape publik MinerbaOne (scripts/_asisten/minerba_scraper.py),
    # lalu di-copy ke kalimantan.db oleh build_combined_db.py step_copy_minerba.
    ("badan_usaha", "id_badan_usaha", "ID unik badan usaha di MinerbaOne (kunci utama, dipakai join ke wiup_match & perizinan).",
     None, "API publik MinerbaOne (/badan-usaha)"),
    ("badan_usaha", "id_jenis_badan_usaha", "Kode jenis badan usaha (referensi internal MinerbaOne, mis. kode utk PT/Koperasi).",
     None, "MinerbaOne"),
    ("badan_usaha", "nib", "Nomor Induk Berusaha (NIB) badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "nama_badan_usaha", "Nama resmi badan usaha (versi registry MinerbaOne).", None, "MinerbaOne"),
    ("badan_usaha", "kode_badan_usaha", "Kode internal badan usaha di sistem MinerbaOne.", None, "MinerbaOne"),
    ("badan_usaha", "no_telp", "Nomor telepon badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "email", "Alamat email badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "fax", "Nomor faksimile badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "npwp_badan_usaha", "NPWP (Nomor Pokok Wajib Pajak) badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "rt", "Rukun Tetangga alamat badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "rw", "Rukun Warga alamat badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "alamat", "Alamat lengkap badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "kode_pos", "Kode pos alamat badan usaha.", None, "MinerbaOne"),
    ("badan_usaha", "kode_desa", "Kode desa/kelurahan alamat badan usaha (kode wilayah administratif BPS/Kemendagri).",
     None, "MinerbaOne"),
    ("badan_usaha", "jenis_badan_usaha", "Jenis badan usaha (mis. PT, Koperasi, Perseorangan).", None, "MinerbaOne"),
    ("badan_usaha", "deskripsi_jenis_badan_usaha", "Deskripsi teks jenis badan usaha (label lengkap dari jenis_badan_usaha).",
     None, "MinerbaOne"),
    ("badan_usaha", "minerbaone_url", "Tautan halaman detail badan usaha di MinerbaOne publik.",
     "https://minerbaone.esdm.go.id/publik/badan-usaha/detail/{id_badan_usaha}", "dibangun dari id_badan_usaha saat ingest"),
    ("badan_usaha", "created_at", "Timestamp saat baris ini disalin/diperbarui ke database (bukan tanggal pendirian usaha).",
     None, "proses ingest scripts/build_combined_db.py"),

    # ── perizinan: daftar izin MinerbaOne (nasional, 8.461 baris) ───────────────
    ("perizinan", "id_perizinan", "ID unik izin di MinerbaOne (kunci utama).", None, "API publik MinerbaOne (/badan-usaha/{id}/list-perizinan)"),
    ("perizinan", "id_badan_usaha", "ID badan usaha pemegang izin (fk ke badan_usaha).", None, "MinerbaOne"),
    ("perizinan", "id_komoditas", "Kode komoditas izin (referensi internal MinerbaOne).", None, "MinerbaOne"),
    ("perizinan", "id_golongan", "Kode golongan komoditas izin (referensi internal MinerbaOne).", None, "MinerbaOne"),
    ("perizinan", "id_jenis_perizinan", "Kode jenis perizinan (referensi internal MinerbaOne).", None, "MinerbaOne"),
    ("perizinan", "id_tahap_kegiatan", "Kode tahap kegiatan izin (referensi internal MinerbaOne).", None, "MinerbaOne"),
    ("perizinan", "id_wiup", "Kode WIUP terkait izin ini menurut MinerbaOne (bukan kode_wiup Geoportal — pencocokan lintas-sumber lihat wiup_match).",
     None, "MinerbaOne"),
    ("perizinan", "id_status_cnc", "Kode status Clean and Clear izin (referensi internal MinerbaOne).", None, "MinerbaOne"),
    ("perizinan", "nomor_izin", "Nomor SK izin — dipakai sbg kunci pencocokan ke wiup_geoportal.sk_iup (lihat wiup_match).",
     None, "MinerbaOne"),
    ("perizinan", "luas_ha", "Luas izin (ha) menurut MinerbaOne (teks, belum tentu numerik bersih).", None, "MinerbaOne"),
    ("perizinan", "tanggal_penetapan", "Tanggal penetapan izin.", None, "MinerbaOne"),
    ("perizinan", "tanggal_berlaku", "Tanggal mulai berlaku izin.", None, "MinerbaOne"),
    ("perizinan", "tanggal_berakhir", "Tanggal akhir berlaku izin.", None, "MinerbaOne"),
    ("perizinan", "lokasi_perizinan", "Deskripsi lokasi administratif izin (teks bebas).", None, "MinerbaOne"),
    ("perizinan", "nama_komoditas", "Nama komoditas izin (mis. Batubara, Bijih Besi).", None, "MinerbaOne"),
    ("perizinan", "nama_golongan", "Nama golongan komoditas izin.", None, "MinerbaOne"),
    ("perizinan", "nama_tahap_kegiatan", "Tahap kegiatan izin (mis. Eksplorasi, Operasi Produksi).", None, "MinerbaOne"),
    ("perizinan", "jenis_perizinan", "Jenis perizinan (mis. IUP, IUPK).", None, "MinerbaOne"),
    ("perizinan", "status_cnc", "Status Clean and Clear izin.", None, "MinerbaOne"),
    ("perizinan", "created_at", "Timestamp saat baris ini disalin/diperbarui ke database.", None, "proses ingest scripts/build_combined_db.py"),

    # ── kepadatan_penduduk: kepadatan penduduk BPS per kab/kota 2015-2024 ───────
    # Bentuk LONG (Fase G butir 8, 15 Agu): 1 baris per (kode_kabkot, tahun) —
    # eks kolom lebar d2015..d2024.
    ("kepadatan_penduduk", "kode_kabkot", "Kode kabupaten/kota (BPS), bagian kunci utama bersama tahun.",
     None, "BPS"),
    ("kepadatan_penduduk", "provinsi", "Nama provinsi kabupaten/kota.", None, "BPS"),
    ("kepadatan_penduduk", "kabupaten", "Nama kabupaten/kota (versi BPS).", None, "BPS"),
    ("kepadatan_penduduk", "kab_normalized", "Nama kabupaten/kota versi baku — kanonik dipakai join lintas-tabel (wiup_geoportal).",
     None, "normalisasi nama BPS saat ingest"),
    ("kepadatan_penduduk", "tahun", "Tahun data kepadatan (2015-2024), bagian kunci utama bersama kode_kabkot — eks kolom lebar d2015..d2024 (unpivot Fase G 15 Agu).", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "kepadatan", "Kepadatan penduduk kab/kota pada `tahun` baris ini (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "satuan", "Satuan nilai kolom kepadatan (jiwa/km²).", None, "BPS"),
    ("kepadatan_penduduk", "sumber", "Sitasi sumber data baris ini (nama publikasi BPS).", None, "BPS"),

    # ── analysis_meta: PROVENANCE tiap tabel analisis (dokumentasi, bukan data) ─
    ("analysis_meta", "nama_tabel", "Nama tabel/view yang didokumentasikan provenance-nya (kunci utama).", None, None),
    ("analysis_meta", "deskripsi", "Ringkasan 1 kalimat isi tabel tsb.", None, None),
    ("analysis_meta", "sumber", "Berkas/tabel input yang jadi bahan tabel tsb.", None, None),
    ("analysis_meta", "metode", "Cara tabel tsb dihitung (rumus/agregasi) — sumber utama isi COLUMN_META di halaman ini.",
     None, None),
    ("analysis_meta", "script", "Nama skrip Python yang membangun tabel tsb.", None, None),
    ("analysis_meta", "status", "Status siklus-hidup tabel (Fase G 15 Agu): AKTIF = dikonsumsi UI/stats saat ini; ARSIP = generasi lama yang UI-nya sudah dihapus tapi tabel dipertahankan utk audit/naskah; PROYEKSI = ≈ proyeksi metode POLOS dgn jendela lebih lebar (periode/penerbit_tahunan_aktif).", None, "konstanta ANALYSIS_STATUS (scripts/build_periode_tables.py)"),

    # ── column_meta: kamus kolom (tabel ini sendiri — dokumentasi meta) ────────
    ("column_meta", "nama_tabel", "Nama tabel/view pemilik kolom (bagian kunci utama, bersama nama_kolom).", None, None),
    ("column_meta", "nama_kolom", "Nama kolom yang didokumentasikan.", None, None),
    ("column_meta", "deskripsi", "Penjelasan 1 kalimat arti kolom tsb.", None, None),
    ("column_meta", "rumus", "Rumus/formula turunan kolom tsb (NULL bila kolom mentah/jelas-sendiri).", None, None),
    ("column_meta", "sumber", "Asal data kolom tsb (tabel/kolom sumber, atau nama dataset mentah).", None, None),
]

# ── column_meta varian BERSIH (Task F1/FASE F): diturunkan PROGRAMATIK dari
# baris kolom tabel asli (bukan copy-paste manual ~40 baris) — kolom & rumus
# tiap tabel _bersih PERSIS sama dgn tabel asli (lihat build_periode_*()),
# HANYA sumber datanya beda (loss_of/loss_lookup bersih, bukan wiup_loss/
# wiup_loss_yearly mentah), jadi deskripsi cukup diberi catatan awalan yg
# menegaskan jendela 2001-2021 + aturan tol2th + 2022-2025 di luar cakupan.
_BERSIH_TABLES = ("periode_ringkasan", "periode_tahunan_aktif",
                   "periode_komoditas", "periode_signifikansi")
_BERSIH_CATATAN = (
    "[Varian BERSIH] Sama dgn tabel aslinya, TAPI kolom loss di sini sudah "
    "dipotong perkiraan konversi sawit (atribusi_sawit, varian tol2th/UTAMA: "
    "YoP piksel ≥ tahun_loss−2) dan dibatasi jendela 2001-2021 — Descals dkk. "
    "(2024) berhenti 2021, jadi tahun 2022-2025 TAK BISA diperiksa thd sawit "
    "dan DIBUANG SELURUHNYA dari varian ini (bukan cuma sawit-nya yg "
    "diabaikan). Konsesi tanpa baris atribusi_sawit dianggap sawit=0 (tetap "
    "ikut dihitung, bukan dibuang). "
)


# Kolom loss varian _bersih BEDA NAMA dari tabel asli (rename 15 Agu — jendela
# masuk nama kolom, dan jendela _bersih memang berbeda: 2001-2021 tanpa sawit).
# Tanpa peta ini baris turunan memakai nama kolom asli → dibuang sbg yatim oleh
# build_column_meta → kolom loss _bersih tak berdokumen (cek_metadata FAIL).
_BERSIH_KOLOM_RENAME = {
    "loss_2001_2025_ha": "loss_2001_2021_tanpa_sawit_ha",
    "pct_poligon_2001_2025": "pct_poligon_2001_2021_tanpa_sawit",
    "r_luas_loss_2001_2025": "r_luas_loss_2001_2021_tanpa_sawit",
    "loss_kumulatif_sejak_2001_ha": "loss_kumulatif_2001_sampai_2021_ha",
}
# Kolom asli yang TIDAK ADA di varian _bersih (loss_2009_2025_ha dihapus dari
# _bersih — dulu NULL semua; barisnya jangan ikut diturunkan).
_BERSIH_KOLOM_HAPUS = {"loss_2009_2025_ha"}


def _bersih_column_meta_rows():
    rows = []
    for tabel, kolom, deskripsi, rumus, sumber in COLUMN_META:
        if tabel not in _BERSIH_TABLES:
            continue
        if kolom in _BERSIH_KOLOM_HAPUS:
            continue
        kolom_bersih = _BERSIH_KOLOM_RENAME.get(kolom, kolom)
        sumber_bersih = ("atribusi_sawit(_yearly) × " + sumber) if sumber else "atribusi_sawit(_yearly)"
        rows.append((tabel + BERSIH_SUFFIX, kolom_bersih, _BERSIH_CATATAN + deskripsi, rumus, sumber_bersih))
    return rows


COLUMN_META = COLUMN_META + _bersih_column_meta_rows()


# ── Sufiks JENDELA WAKTU per (tabel, kolom) — feedback igoen 12 Agu r5:
# setiap deskripsi kolom yang datanya terikat waktu HARUS menyebut tahunnya,
# supaya pembaca halaman Database tak menebak "ini jendela yang mana".
# Diterapkan build_column_meta() saat insert (satu titik — bukan menyunting
# 70+ string COLUMN_META yang melipat baris).
JENDELA_DESKRIPSI = {
    # wiup_loss / wiup_master — % poligon: pembilangnya loss 2001-2025
    ("wiup_loss", "loss_pct_poligon_2001_2025"): "Jendela pembilang: kehilangan 2001-2025; penyebut: luas poligon (tak berjendela).",
    ("wiup_master", "loss_pct_poligon_2001_2025"): "Jendela pembilang: kehilangan 2001-2025; penyebut: luas poligon (tak berjendela).",
    # temporal pra/pasca-izin
    ("wiup_temporal", "rate_2001_sampai_tahun_izin_ha_per_year"): "Jendela pra-izin: 2001 s.d. iup_year−1.",
    ("wiup_temporal", "rate_tahun_izin_sampai_2025_ha_per_year"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("wiup_temporal", "ratio_laju_sesudah_vs_sebelum_tahun_izin"): "Pra = 2001..iup_year−1; pasca = iup_year..2025.",
    ("wiup_temporal", "verdict"): "Dari perbandingan laju 2001..iup_year−1 vs iup_year..2025.",
    ("wiup_master", "rate_2001_sampai_tahun_izin_ha_per_year"): "Jendela pra-izin: 2001 s.d. iup_year−1.",
    ("wiup_master", "rate_tahun_izin_sampai_2025_ha_per_year"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("wiup_master", "ratio_laju_sesudah_vs_sebelum_tahun_izin"): "Pra = 2001..iup_year−1; pasca = iup_year..2025.",
    ("wiup_master", "temporal_verdict"): "Dari perbandingan laju 2001..iup_year−1 vs iup_year..2025.",
    # sawit varian (jendela peta Descals)
    ("atribusi_sawit", "loss_sawit_jeda5th_2001_2021_ha"): "Jendela sama dgn tol2th: kehilangan 2001-2021 (batas peta Descals).",
    ("atribusi_sawit", "loss_sawit_tahunsama_2001_2021_ha"): "Jendela sama dgn tol2th: kehilangan 2001-2021 (batas peta Descals).",
    ("atribusi_sawit_yearly", "loss_sawit_tol2th_ha"): "Deret tahun 2001-2021 (batas peta Descals).",
    ("wiup_master", "loss_sawit_jeda5th_2001_2021_ha"): "Jendela: kehilangan 2001-2021 (batas peta Descals).",
    ("wiup_master", "loss_sawit_tahunsama_2001_2021_ha"): "Jendela: kehilangan 2001-2021 (batas peta Descals).",
    ("periode_sawit", "loss_sawit_jeda5th_2001_2021_ha"): "Jendela: kehilangan 2001-2021 (batas peta Descals).",
    ("periode_sawit", "loss_sawit_tahunsama_2001_2021_ha"): "Jendela: kehilangan 2001-2021 (batas peta Descals).",
    ("periode_sawit", "n_konsesi"): "Data sawit menjangkau kehilangan 2001-2021.",
    ("klasifikasi_izin", "pra_izin_dominan"): "Kehilangan yang dibandingkan: 2001 s.d. iup_year−1 vs iup_year s.d. 2025.",
    # atribusi izin aktif — jendela era Minerba
    ("atribusi_izin_aktif", "loss_mulai_sampai_2025_ha"): "Jendela era Minerba 2009-2025, sejak `mulai` versi aturan baris ini.",
    # laju per tahun-mulai-aktif (aturan E)
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2025_ha_thn"): "Jendela: tahun mulai aktif s.d. 2025 (Hansen penuh).",
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2025_pct_thn"): "Jendela: tahun mulai aktif s.d. 2025 (Hansen penuh).",
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2021_tanpa_sawit_ha_thn"): "Jendela: tahun mulai aktif s.d. 2021 (dikurangi sawit; batas Descals).",
    ("laju_izin_konsesi", "laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn"): "Jendela: tahun mulai aktif s.d. 2021 (dikurangi sawit; batas Descals).",
    ("laju_izin_ringkas", "total_loss_ha"): "Jendela: mulai-aktif s.d. 2025 (kotor) / s.d. 2021 (bersih).",
    ("laju_izin_ringkas", "n"): "Basis kotor: mulai s.d. 2025; bersih: mulai s.d. 2021.",
    ("laju_izin_ringkas", "n_pct"): "Basis kotor: mulai s.d. 2025; bersih: mulai s.d. 2021.",
    ("laju_izin_ringkas", "median_ha_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "mean_ha_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "p25_ha_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "p75_ha_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "p90_ha_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "median_pct_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "mean_pct_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "p25_pct_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "p75_pct_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_ringkas", "p90_pct_thn"): "Jendela: mulai-aktif s.d. 2025 (kotor) / 2021 (bersih).",
    ("laju_izin_eventstudy", "sum_loss_ha"): "Tahun kalender 2001-2025.",
    ("laju_izin_eventstudy", "mean_loss_ha"): "Tahun kalender 2001-2025.",
    ("laju_izin_eventstudy", "mean_tanpa_sawit_sampai_2021_ha"): "Tahun kalender 2001-2021 (dikurangi sawit; batas Descals).",
    # periode (kohort & pasca-izin)
    ("periode_ringkasan", "pct_poligon_2001_2025"): "Pembilang: kehilangan 2001-2025.",
    ("periode_ringkasan", "pct_akselerasi"): "Dari perbandingan laju 2001..iup−1 vs iup..2025 per konsesi.",
    ("periode_ringkasan", "rate_tahun_izin_sampai_2025_mean"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("periode_ringkasan", "rate_tahun_izin_sampai_2025_median"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("periode_ringkasan", "r_luas_loss_2001_2025"): "Loss = total 2001-2025.",
    ("periode_ringkasan", "r_luas_rate_tahun_izin_sampai_2025"): "Laju pasca-izin = iup_year s.d. 2025.",
    ("periode_komoditas", "loss_2001_2025_ha"): "Jendela: 2001-2025.",
    ("periode_komoditas", "pct_poligon_2001_2025"): "Pembilang: kehilangan 2001-2025.",
    ("periode_komoditas", "pct_akselerasi"): "Dari perbandingan laju 2001..iup−1 vs iup..2025.",
    ("periode_komoditas", "rate_tahun_izin_sampai_2025_median"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("periode_klasifikasi", "n_akselerasi"): "Dari perbandingan laju 2001..iup−1 vs iup..2025.",
    ("periode_klasifikasi", "rate_tahun_izin_sampai_2025_mean"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("periode_klasifikasi", "rate_tahun_izin_sampai_2025_median"): "Jendela pasca-izin: iup_year s.d. 2025.",
    ("periode_slope", "slope_ha_per_year"): "Deret since-permit: iup_year kohort s.d. 2025.",
    ("periode_slope", "r2"): "Deret since-permit: iup_year kohort s.d. 2025.",
    ("periode_slope", "peak_year"): "Dicari dalam deret since-permit s.d. 2025.",
    ("periode_slope", "peak_loss_ha"): "Dicari dalam deret since-permit s.d. 2025.",
    ("periode_tahunan_aktif", "loss_ha"): "Deret tahun 2001-2025 (varian _bersih: s.d. 2021).",
    ("periode_tahunan_aktif", "loss_kumulatif_sejak_2001_ha"): "Akumulasi sejak 2001 s.d. 2025 (varian _bersih: kolom loss_kumulatif_2001_sampai_2021_ha, berhenti 2021).",
    ("periode_tahunan_aktif", "n_konsesi_aktif"): "Deret tahun 2001-2025.",
    ("periode_tahunan_aktif", "luas_aktif_ha"): "Deret tahun 2001-2025.",
    ("penerbit_tahunan_aktif", "loss_ha"): "Deret tahun 2001-2025.",
    ("penerbit_tahunan_aktif", "n_konsesi_aktif"): "Deret tahun 2001-2025.",
    ("penerbit_tahunan_aktif", "luas_aktif_ha"): "Deret tahun 2001-2025.",
    ("baseline_tahunan", "loss_ha"): "Deret tahun 2001-2025.",
    ("baseline_tahunan", "n_konsesi"): "Deret tahun 2001-2025.",
    ("periode_signifikansi", "metrik"): "loss_2001_2025_ha = jendela 2001-2025 (varian _bersih: loss_2001_2021_tanpa_sawit_ha, 2001-2021); rate_tahun_izin_sampai_2025 = iup_year s.d. 2025.",
}


def build_column_meta(con):
    """Bangun column_meta (kamus kolom); buang baris yatim (tabel/kolom tak wujud)."""
    con.execute("DROP TABLE IF EXISTS column_meta")
    con.execute(
        """CREATE TABLE column_meta (
            nama_tabel TEXT, nama_kolom TEXT, deskripsi TEXT, rumus TEXT, sumber TEXT,
            PRIMARY KEY (nama_tabel, nama_kolom))"""
    )
    real = {}
    for (tname,) in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')"):
        real[tname] = {r[1] for r in con.execute(f'PRAGMA table_info("{tname}")')}
    kept, skipped = [], []
    for row in COLUMN_META:
        if row[0] in real and row[1] in real[row[0]]:
            tambahan = JENDELA_DESKRIPSI.get((row[0], row[1]))
            if tambahan and tambahan not in (row[2] or ""):
                row = (row[0], row[1], (row[2] or "").rstrip() + " " + tambahan,
                       row[3], row[4])
            kept.append(row)
        else:
            skipped.append((row[0], row[1]))
    con.executemany("INSERT INTO column_meta VALUES (?,?,?,?,?)", kept)
    if skipped:
        print(f"  column_meta: {len(skipped)} baris yatim di-skip: {skipped[:5]}")
    return len(kept)


def pearson(pairs):
    pts = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((a - mx) * (b - my) for a, b in pts)
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    return num / (dx * dy) if dx and dy else None


def ols_slope(years, losses):
    """slope, intercept, r2 dari regresi linear loss ~ year."""
    n = len(years)
    if n < 2:
        return None, None, None
    mx, my = sum(years) / n, sum(losses) / n
    sxx = sum((x - mx) ** 2 for x in years)
    sxy = sum((x - mx) * (y - my) for x, y in zip(years, losses))
    if sxx == 0:
        return None, None, None
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in losses)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(years, losses))
    r2 = 1 - ss_res / ss_tot if ss_tot else None
    return slope, intercept, r2


def since_permit_loss_series(concessions, loss_lookup, year_min=YEAR_MIN, year_max=YEAR_MAX):
    """Deret loss tahunan per periode berbasis IZIN-AKTIF (since-permit).

    Tiap konsesi dihitung HANYA sejak iup_year-nya sendiri — loss sebelum izin
    terbit tidak pernah masuk. Ini basis yang adil untuk slope "tren tahunan"
    (regresi jendela-penuh 2001–2025 terkontaminasi tahun pra-izin; lihat audit).

    concessions : iterable (kode_wiup, iup_year)
    loss_lookup : {(kode_wiup, year): loss_ha}
    return      : {periode: {year: loss_ha}} — hanya tahun sejak iup_year (0 bila tak ada loss)
    """
    series = {r: {} for r in PERIODES}
    for kode, iy in concessions:
        r = to_periode(iy)
        if r is None or iy is None:
            continue
        for y in range(max(iy, year_min), year_max + 1):
            series[r][y] = series[r].get(y, 0.0) + loss_lookup.get((kode, y), 0)
    return series


def tabel_ada_berisi(con, nama_tabel):
    """True bila `nama_tabel` ADA sbg tabel real (bukan view) DAN berisi >=1 baris.

    Guard utk tabel LAPISAN opsional (atribusi_sawit/klasifikasi_izin): tabel bisa
    ADA sbg cangkang kosong (LAPISAN_SHELLS di build_combined_db.py, dibuat lebih
    dulu agar wiup_master selalu valid) tapi BELUM diisi skrip lapisannya — guard
    'ada di sqlite_master' saja tak cukup, harus dicek isinya juga.
    """
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nama_tabel,)
    ).fetchone()
    if row is None:
        return False
    (n,) = con.execute(f'SELECT COUNT(*) FROM "{nama_tabel}"').fetchone()
    return n > 0


def build_periode_sawit(con):
    """Agregasi atribusi_sawit x periode(iup_year) -> 1 baris/periode.

    Pemanggil (main()) WAJIB pakai tabel_ada_berisi(con,'atribusi_sawit') sbg
    guard sebelum memanggil fungsi ini — query di sini akan gagal (no such
    table) kalau atribusi_sawit belum ada sama sekali.

    Pengelompokan periode pakai to_periode() PYTHON (BUKAN CASE WHEN SQL):
    iup_year kosong/di luar jendela 1998-2025 harus dibuang oleh to_periode(),
    bukan diam-diam jatuh ke P3 (bug yg pernah terjadi pd periode_ringkasan).
    """
    rows = con.execute(
        """SELECT g.iup_year, a.loss_2001_2021_ha, a.loss_sawit_tol2th_2001_2021_ha,
                  a.loss_sawit_jeda5th_2001_2021_ha, a.loss_sawit_tahunsama_2001_2021_ha, a.loss_2022_2025_ha
           FROM atribusi_sawit a
           JOIN wiup_geoportal g ON g.kode_wiup = a.kode_wiup"""
    ).fetchall()
    by_per = {r: [] for r in PERIODES}
    for iy, l2101, tol2, jeda5, tahunsama, l2225 in rows:
        r = to_periode(iy)
        if r is None:
            continue
        by_per[r].append((l2101, tol2, jeda5, tahunsama, l2225))

    con.execute("DROP TABLE IF EXISTS periode_sawit")
    con.execute(
        """CREATE TABLE periode_sawit (
            periode TEXT PRIMARY KEY, n_konsesi INTEGER,
            loss_2001_2021_ha REAL, loss_sawit_tol2th_2001_2021_ha REAL,
            loss_sawit_jeda5th_2001_2021_ha REAL, loss_sawit_tahunsama_2001_2021_ha REAL,
            loss_2001_2021_tanpa_sawit_ha REAL, persen_sawit_2001_2021 REAL, loss_2022_2025_ha REAL)"""
    )
    for r in PERIODES:
        items = by_per[r]

        def total(idx):
            vals = [x[idx] for x in items if x[idx] is not None]
            return sum(vals)

        l2101, tol2, jeda5, tahunsama, l2225 = (total(i) for i in range(5))
        bersih = l2101 - tol2
        persen = round(100 * tol2 / l2101, 2) if l2101 else None
        con.execute(
            "INSERT INTO periode_sawit VALUES (?,?,?,?,?,?,?,?,?)",
            (r, len(items),
             round(l2101, 2), round(tol2, 2), round(jeda5, 2), round(tahunsama, 2),
             round(bersih, 2), persen, round(l2225, 2)),
        )


def build_periode_ringkasan(con, table_name, by_per, forest_of, loss_of, loss09_of=None,
                            loss_col="loss_2001_2025_ha",
                            pct_poligon_col="pct_poligon_2001_2025",
                            r_luas_loss_col="r_luas_loss_2001_2025"):
    """Bangun `table_name` (periode_ringkasan / periode_ringkasan_bersih).

    `loss_of`: dict kode_wiup -> loss_ha dipakai SEBAGAI GANTI wiup_loss.loss_2001_2025_ha
    (parameter Task F1 — DRY, jangan copy-paste builder tabel asli vs bersih).
    Kalau kode_wiup TAK ADA di `loss_of`, dianggap "tak ada data" (DIBUANG dari
    rata-rata/Pearson, PERSIS spt x[5] is None di versi lama) — beda dari
    "ada tapi 0.0" (yg tetap terhitung). Pemanggil (main()) yg memutuskan mana
    dari dua makna ini yg dipakai per konsesi lewat isi `loss_of`.

    Nama kolom loss/pct/r JADI PARAMETER (konvensi penamaan DECISIONS 13 Agu:
    jendela tetap masuk NAMA kolom) — tabel asli & _bersih berbagi satu builder
    tapi JENDELANYA BEDA (2001-2025 vs 2001-2021 tanpa sawit), jadi nama kolom
    yang sama utk keduanya adalah nama yang bohong utk salah satunya.
    Kolom loss_2009_2025_ha hanya ditulis bila `loss09_of` diberikan (bukan
    None): varian _bersih tak punya jendela 2009-2025, dulu kolomnya tetap
    tercipta berisi NULL semua — nama menjanjikan data yang tak pernah ada.
    """
    kolom_loss09 = loss09_of is not None
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT PRIMARY KEY, rentang_tahun TEXT, n INTEGER,
            luas_total_ha REAL, luas_mean_ha REAL, luas_median_ha REAL,
            {loss_col} REAL, {"loss_2009_2025_ha REAL," if kolom_loss09 else ""}
            polygon_total_ha REAL, forest2000_total_ha REAL,
            {pct_poligon_col} REAL,
            rate_tahun_izin_sampai_2025_mean REAL, rate_tahun_izin_sampai_2025_median REAL,
            pct_akselerasi REAL,
            {r_luas_loss_col} REAL, r_luas_rate_tahun_izin_sampai_2025 REAL,
            komposisi_otoritas TEXT)"""
    )
    n_kolom = 17 if kolom_loss09 else 16
    for r in PERIODES:
        rows = by_per[r]
        luas = [x[3] for x in rows if x[3] is not None]
        loss = [v for v in (loss_of.get(x[0]) for x in rows) if v is not None]
        poly = [x[4] for x in rows if x[4] is not None]
        ratep = [x[6] for x in rows if x[6] is not None]
        forest = [forest_of.get(x[0]) for x in rows]
        forest = [f for f in forest if f is not None]
        accel = sum(1 for x in rows if x[7] in ACCEL_VERDICTS)
        auth = {}
        for x in rows:
            auth[x[2]] = auth.get(x[2], 0) + 1
        comp = ", ".join(f"{a}:{n}" for a, n in sorted(auth.items(), key=lambda z: -z[1]))
        r_ll = pearson([(x[3], loss_of.get(x[0])) for x in rows])
        r_lr = pearson([(x[3], x[6]) for x in rows])
        # Jendela era Minerba (Fase B): Σ loss_2009_2025_ha kohort — None-safe.
        # Ditulis HANYA bila kolomnya ada (loss09_of bukan None — lihat docstring).
        loss09 = [v for v in ((loss09_of or {}).get(x[0]) for x in rows) if v is not None]
        nilai = (r, RENTANG[r], len(rows),
                 round(sum(luas), 2),
                 round(sum(luas) / len(luas), 2) if luas else None,
                 round(statistics.median(luas), 2) if luas else None,
                 round(sum(loss), 2)) \
            + ((round(sum(loss09), 2) if loss09 else None,) if kolom_loss09 else ()) \
            + (round(sum(poly), 2), round(sum(forest), 2),
               round(100 * sum(loss) / sum(poly), 2) if poly and sum(poly) else None,
               round(sum(ratep) / len(ratep), 2) if ratep else None,
               round(statistics.median(ratep), 2) if ratep else None,
               round(100 * accel / len(rows), 2) if rows else None,
               round(r_ll, 3) if r_ll is not None else None,
               round(r_lr, 3) if r_lr is not None else None,
               comp)
        con.execute(
            f"INSERT INTO {table_name} VALUES ({','.join('?' * n_kolom)})", nilai)


def build_periode_tahunan_aktif(con, table_name, by_per, loss_lookup, forest_of, year_max,
                                kumulatif_col="loss_kumulatif_sejak_2001_ha"):
    """Bangun `table_name` (periode_tahunan_aktif / periode_tahunan_aktif_bersih).

    `loss_lookup`: dict (kode_wiup, year) -> loss_ha dipakai SEBAGAI GANTI
    wiup_loss_yearly mentah (parameter Task F1). `year_max`: batas atas deret
    tahun (2025 utk tabel asli, 2021 utk bersih — Descals berhenti 2021,
    tahun 2022-2025 DIBUANG SELURUHNYA dari varian bersih, bukan cuma
    sawit-nya yg diabaikan). Konsesi ber-iup_year > year_max tak menyumbang
    baris apa pun di varian bersih (start > year_max -> range kosong).

    `kumulatif_col` PARAMETER (konvensi DECISIONS 13 Agu — ujung jendela yang
    TETAP masuk nama kolom): tabel asli mengakumulasi sejak awal jendela 2001,
    varian _bersih berhenti di 2021 (loss_kumulatif_2001_sampai_2021_ha) —
    nama tunggal 'loss_kumulatif_ha' menyembunyikan beda jendela itu.
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT, year INTEGER, loss_ha REAL, n_konsesi_aktif INTEGER,
            luas_aktif_ha REAL, forest_aktif_ha REAL, {kumulatif_col} REAL,
            PRIMARY KEY (periode, year))"""
    )
    for r in PERIODES:
        akt_loss = {}
        akt_n = {}
        akt_luas = {}
        akt_forest = {}
        for x in by_per[r]:
            kode, iy, luas = x[0], x[1], x[3]
            if iy is None:
                continue
            start = max(iy, YEAR_MIN)
            forest = forest_of.get(kode) or 0
            for y in range(start, year_max + 1):
                akt_n[y] = akt_n.get(y, 0) + 1
                akt_luas[y] = akt_luas.get(y, 0.0) + (luas or 0)
                akt_forest[y] = akt_forest.get(y, 0.0) + forest
                akt_loss[y] = akt_loss.get(y, 0.0) + loss_lookup.get((kode, y), 0)
        kum = 0.0
        for y in sorted(akt_n):
            kum += akt_loss[y]
            con.execute(f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?)",
                        (r, y, round(akt_loss[y], 2), akt_n[y],
                         round(akt_luas[y], 2), round(akt_forest[y], 2), round(kum, 2)))


def build_periode_komoditas(con, table_name, by_per, komod_of, loss_of, loss09_of=None,
                            loss_col="loss_2001_2025_ha",
                            pct_poligon_col="pct_poligon_2001_2025"):
    """Bangun `table_name` (periode_komoditas / periode_komoditas_bersih).

    `loss_of`: idem build_periode_ringkasan (parameter Task F1).
    Nama kolom loss/pct PARAMETER + kolom loss_2009_2025_ha hanya bila
    `loss09_of` bukan None — alasan sama dgn build_periode_ringkasan (jendela
    asli vs _bersih beda; kolom NULL-semua bernama jendela = nama bohong).
    """
    def kgroup(komoditas):
        return "BATUBARA" if (komoditas or "").upper().startswith("BATUBARA") else "MINERAL LOGAM"

    kolom_loss09 = loss09_of is not None
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT, grup_komoditas TEXT, n INTEGER,
            luas_total_ha REAL, luas_median_ha REAL,
            {loss_col} REAL, {"loss_2009_2025_ha REAL," if kolom_loss09 else ""}
            {pct_poligon_col} REAL,
            rate_tahun_izin_sampai_2025_median REAL, pct_akselerasi REAL,
            PRIMARY KEY (periode, grup_komoditas))"""
    )
    n_kolom = 10 if kolom_loss09 else 9
    for r in PERIODES:
        groups = {}
        for x in by_per[r]:
            groups.setdefault(kgroup(komod_of.get(x[0])), []).append(x)
        for gname, rows in sorted(groups.items()):
            luas = [x[3] for x in rows if x[3] is not None]
            loss = [v for v in (loss_of.get(x[0]) for x in rows) if v is not None]
            poly = [x[4] for x in rows if x[4] is not None]
            ratep = [x[6] for x in rows if x[6] is not None]
            accel = sum(1 for x in rows if x[7] in ACCEL_VERDICTS)
            loss09 = [v for v in ((loss09_of or {}).get(x[0]) for x in rows) if v is not None]
            nilai = (r, gname, len(rows),
                     round(sum(luas), 2),
                     round(statistics.median(luas), 2) if luas else None,
                     round(sum(loss), 2)) \
                + ((round(sum(loss09), 2) if loss09 else None,) if kolom_loss09 else ()) \
                + (round(100 * sum(loss) / sum(poly), 2) if poly and sum(poly) else None,
                   round(statistics.median(ratep), 2) if ratep else None,
                   round(100 * accel / len(rows), 2) if rows else None)
            con.execute(
                f"INSERT INTO {table_name} VALUES ({','.join('?' * n_kolom)})", nilai)


def build_periode_signifikansi(con, table_name, by_per, loss_of,
                               metrik_loss="loss_2001_2025_ha"):
    """Bangun `table_name` (periode_signifikansi / periode_signifikansi_bersih).

    HANYA metrik loss (nilai kolom `metrik` = `metrik_loss`) yg memakai
    `loss_of` (parameter Task F1); rate_tahun_izin_sampai_2025_ha_per_year &
    luas_sk tak bergantung sawit — identik antara tabel asli & bersih (dites
    tegas di test_build_periode_tables.py). `metrik_loss` PARAMETER karena
    jendelanya beda antar varian: asli = loss_2001_2025_ha, _bersih =
    loss_2001_2021_tanpa_sawit_ha — nilai lama 'total_loss_ha' tak menyebut
    jendela sama sekali (nama menyatakan hal salah utk varian bersih).
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            metrik TEXT, uji TEXT, grup_a TEXT, grup_b TEXT,
            n_a INTEGER, n_b INTEGER, statistik REAL, p_value REAL,
            p_adjusted REAL, signifikan_005 INTEGER,
            PRIMARY KEY (metrik, uji, grup_a, grup_b))"""
    )
    try:
        from scipy import stats as sps

        GETTERS = {  # metrik -> fungsi ambil nilai dari tuple konsesi `x`
            "rate_tahun_izin_sampai_2025_ha_per_year": lambda x: x[6],
            metrik_loss: lambda x: loss_of.get(x[0]),
            "luas_sk": lambda x: x[3],
        }
        TRIO = ["P1", "P2", "P3"]
        for mname, getter in GETTERS.items():
            samples = {r: [v for x in by_per[r] if (v := getter(x)) is not None] for r in TRIO}
            H, p = sps.kruskal(*[samples[r] for r in TRIO])
            con.execute(
                f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mname, "kruskal-wallis", "P1|P2|P3", "-",
                 sum(len(samples[r]) for r in TRIO), 0,
                 round(float(H), 3), float(p), float(p), 1 if p < 0.05 else 0),
            )
            pairs = [("P1", "P2"), ("P1", "P3"), ("P2", "P3")]
            raw = []
            for a, b in pairs:
                U, pu = sps.mannwhitneyu(samples[a], samples[b], alternative="two-sided")
                raw.append((a, b, float(U), float(pu)))
            # Koreksi Holm: urutkan p naik, p_adj_i = max((m-i)·p_i, p_adj_{i-1}), cap 1.
            order = sorted(range(3), key=lambda i: raw[i][3])
            padj = [0.0] * 3
            running = 0.0
            for rank, i in enumerate(order):
                running = max(running, min(1.0, (3 - rank) * raw[i][3]))
                padj[i] = running
            for (a, b, U, pu), pa in zip(raw, padj):
                con.execute(
                    f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (mname, "mann-whitney-u", a, b,
                     len(samples[a]), len(samples[b]),
                     round(U, 1), pu, pa, 1 if pa < 0.05 else 0),
                )
    except ImportError:
        print(f"PERINGATAN: scipy tak tersedia — {table_name} dilewati (tabel kosong).")


def build_periode_klasifikasi(con, table_name, by_per, kelas_of, loss_of, loss09_of=None):
    """Matriks periode × kelas izin — lapisan pemeriksa "klasifikasi izin".

    Menguji apakah urutan akselerasi P1>P2>P3 bertahan setelah jenis izin
    (perpanjangan vs izin awal) dikendalikan. Pangsa perpanjangan naik monoton
    antar periode, jadi periode & jenis izin saling membonceng.

    Konsesi TANPA baris di klasifikasi_izin dihitung TAK_DINILAI (bukan
    dibuang) supaya Σ n per periode SELALU = n periode di periode_ringkasan.
    Sel kosong ditulis dgn n=0 & pct NULL — bukan 0 — agar "tak ada data"
    tak tersamar jadi "0% akselerasi".
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT, kelas TEXT, n INTEGER, n_akselerasi INTEGER,
            pct_akselerasi REAL, rate_tahun_izin_sampai_2025_median REAL,
            rate_tahun_izin_sampai_2025_mean REAL,
            loss_2001_2025_ha REAL, loss_2009_2025_ha REAL, luas_sk_ha REAL,
            PRIMARY KEY (periode, kelas))"""
    )
    for per in PERIODES_UJI:
        for kelas in KELAS_IZIN:
            grup = [x for x in by_per[per]
                    if (kelas_of.get(x[0]) or "TAK_DINILAI") == kelas]
            n = len(grup)
            if n == 0:
                con.execute(f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (per, kelas, 0, 0, None, None, None, 0.0, 0.0, 0.0))
                continue
            n_acc = sum(1 for x in grup if x[7] in ACCEL_VERDICTS)
            rates = [x[6] for x in grup if x[6] is not None]
            con.execute(
                f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?)",
                (per, kelas, n, n_acc, round(100.0 * n_acc / n, 2),
                 round(statistics.median(rates), 2) if rates else None,
                 round(statistics.fmean(rates), 2) if rates else None,
                 round(sum(loss_of.get(x[0]) or 0 for x in grup), 2),
                 round(sum((loss09_of or {}).get(x[0]) or 0 for x in grup), 2),
                 round(sum(x[3] or 0 for x in grup), 2)),
            )


def fisher_pair(acc_a, n_a, acc_b, n_b):
    """p dua-sisi Fisher exact untuk beda proporsi akselerasi dua grup.

    Fisher (bukan chi-square) karena sel bisa sangat kecil — P3 izin awal
    hanya 8 kejadian dari 24, di mana aproksimasi chi-square tak sah.
    Mengembalikan None bila salah satu grup kosong atau scipy tak terpasang
    (pemanggil menyembunyikan bagian nilai-p, bukan gagal).
    """
    if n_a <= 0 or n_b <= 0:
        return None
    try:
        from scipy import stats as sps
    except ImportError:
        return None
    _, p = sps.fisher_exact([[acc_a, n_a - acc_a], [acc_b, n_b - acc_b]])
    return float(p)


def build_periode_klasifikasi_uji(con, table_name, by_per, kelas_of):
    """Uji beda proporsi akselerasi ANTAR PERIODE, di dalam tiap kelas izin.

    Ini pertanyaan intinya: kalau jenis izin dikendalikan (dibandingkan hanya
    sesama izin awal, atau sesama perpanjangan), apakah urutan P1>P2>P3 masih
    ada? Baris ditulis apa adanya termasuk saat p None (scipy absen) supaya
    matriks pct tetap bisa dibaca UI tanpa nilai-p.
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            kelas TEXT, periode_a TEXT, periode_b TEXT,
            n_a INTEGER, n_b INTEGER, pct_a REAL, pct_b REAL,
            p_value REAL, signifikan_005 INTEGER, metode TEXT,
            PRIMARY KEY (kelas, periode_a, periode_b))"""
    )
    try:
        import scipy  # noqa: F401
    except ImportError:
        print(f"PERINGATAN: scipy tak tersedia — {table_name} ditulis dengan p_value NULL "
              f"di semua baris (matriks pct tetap terisi; baris TIDAK di-skip — beda dari "
              f"periode_signifikansi yang tabelnya dikosongkan sepenuhnya).")
    hitung = {}
    for per in PERIODES_UJI:
        for kelas in KELAS_IZIN:
            grup = [x for x in by_per[per]
                    if (kelas_of.get(x[0]) or "TAK_DINILAI") == kelas]
            n = len(grup)
            acc = sum(1 for x in grup if x[7] in ACCEL_VERDICTS)
            hitung[(per, kelas)] = (acc, n)
    pairs = [("P1", "P2"), ("P1", "P3"), ("P2", "P3")]
    for kelas in KELAS_IZIN:
        for a, b in pairs:
            acc_a, n_a = hitung[(a, kelas)]
            acc_b, n_b = hitung[(b, kelas)]
            p = fisher_pair(acc_a, n_a, acc_b, n_b)
            con.execute(
                f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kelas, a, b, n_a, n_b,
                 round(100.0 * acc_a / n_a, 2) if n_a else None,
                 round(100.0 * acc_b / n_b, 2) if n_b else None,
                 p, (1 if (p is not None and p < 0.05) else 0),
                 "fisher_exact_two_sided"),
            )


def build_baseline_tahunan(con, table_name):
    """Deret kehilangan tahunan SELURUH konsesi — tanpa pembagian periode.

    Penyebutnya SENGAJA berbeda dari tabel periode_*: di sini seluruh konsesi
    ikut (termasuk yang tanpa iup_year dan yang iup_year-nya di luar jendela),
    karena tanpa pembagian periode tak ada alasan membuangnya. UI WAJIB
    melabeli penyebut ini supaya tak dikira bertentangan dgn angka 814.

    Tahun ditulis penuh 2001-2025 termasuk yang nol, supaya konsumen tak perlu
    menambal lubang dan sumbu-x chart tak melompat.
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            year INTEGER PRIMARY KEY, loss_ha REAL, n_konsesi INTEGER)"""
    )
    agg = dict.fromkeys(range(YEAR_MIN, YEAR_MAX + 1), 0.0)
    cnt = {y: 0 for y in range(YEAR_MIN, YEAR_MAX + 1)}
    for kode, y, loss in con.execute(
        "SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly"
    ):
        if y is None or y < YEAR_MIN or y > YEAR_MAX:
            continue
        agg[y] += loss or 0
        if (loss or 0) > 0:
            cnt[y] += 1
    for y in range(YEAR_MIN, YEAR_MAX + 1):
        con.execute(f"INSERT INTO {table_name} VALUES (?,?,?)",
                    (y, round(agg[y], 2), cnt[y]))


# ── Status siklus-hidup tabel di analysis_meta (Fase G butir 7, 15 Agu) ──────
# AKTIF (default) = dikonsumsi UI/stats saat ini. ARSIP = generasi kohort-SK
# lama: bloknya sudah dihapus dari UI (cleanup 12 Agu r3 / Fase F) tapi tabel
# sengaja dipertahankan utk audit & naskah tesis — masih dikirim /api/periode,
# tak dirender. PROYEKSI = periode/penerbit_tahunan_aktif: praktis proyeksi
# metode POLOS — deret flow/stok year>=2009-nya TERBUKTI identik (EXCEPT = 0
# baris) dgn backtrack_stok aturan POLOS; bedanya hanya jendela lebih lebar
# (baris 2001-2008 + akumulasi sejak 2001, dan kohort Pra-2009 berakumulasi
# sejak iup_year, bukan diklem 2009) — karena beda jendela itu ia TETAP tabel,
# bukan view (keputusan Fase G butir 6).
ANALYSIS_STATUS = {
    "periode_komoditas": "ARSIP",
    "periode_komoditas_bersih": "ARSIP",
    "periode_signifikansi": "ARSIP",
    "periode_signifikansi_bersih": "ARSIP",
    "periode_klasifikasi": "ARSIP",
    "periode_klasifikasi_uji": "ARSIP",
    # periode_sawit: AKTIF (default) — masih di-serve /api/periode & dirender
    # blok "uji ketahanan sawit" EraView (koreksi label 16 Agu; audit metodologi).
    "periode_ringkasan_bersih": "ARSIP",
    "periode_tahunan_aktif_bersih": "ARSIP",
    "laju_izin_eventstudy": "ARSIP",
    "periode_tahunan_aktif": "PROYEKSI",
    "penerbit_tahunan_aktif": "PROYEKSI",
}


def existing_meta_rows(meta, table_names):
    """Buang baris provenance untuk tabel yang TIDAK ada di DB.

    Mis. tabel LAPISAN opsional (atribusi_sawit, klasifikasi_izin) yang di-skip
    saat prasyaratnya tak ada → analysis_meta tak boleh mengklaim tabel yang
    absen (row[0] = nama tabel).
    """
    have = set(table_names)
    return [row for row in meta if row[0] in have]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalimantan.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    # ── Ambil atribut per konsesi (periode, luas, loss, temporal) ──────────────
    konsesi = con.execute(
        """SELECT g.kode_wiup, g.iup_year, LOWER(g.pejabat), g.luas_sk,
                  l.polygon_area_ha, l.loss_2001_2025_ha,
                  t.rate_tahun_izin_sampai_2025_ha_per_year, t.verdict
           FROM wiup_geoportal g
           LEFT JOIN wiup_loss l ON l.kode_wiup=g.kode_wiup
           LEFT JOIN wiup_temporal t ON t.kode_wiup=g.kode_wiup"""
    ).fetchall()
    # index periode → list konsesi
    by_per = {r: [] for r in PERIODES}
    iup_of = {}
    for kode, iy, pj, luas, poly, loss, ratep, verdict in konsesi:
        r = to_periode(iy)
        if r is None:
            continue
        by_per[r].append((kode, iy, pj, luas, poly, loss, ratep, verdict))
        iup_of[kode] = iy

    # Butuh forest_2000_ha juga untuk ringkasan → ambil terpisah (indeks kolom stabil).
    forest_of = dict(con.execute("SELECT kode_wiup, forest_2000_ha FROM wiup_loss"))

    # loss_of: dict kode_wiup -> loss_2001_2025_ha, sumber ASLI (identik x[5] di
    # tuple `konsesi`) — parameter Task F1 dilewatkan ke builder generik supaya
    # tabel ASLI (loss_of=loss_of_asli) & _bersih (loss_of=loss_of_bersih di
    # bawah, dibangun HANYA bila atribusi_sawit ada+berisi) berbagi 1 builder.
    loss_of_asli = {x[0]: x[5] for x in konsesi}
    # Jendela era Minerba per konsesi (Fase B) — utk kolom loss_2009_2025_ha.
    # Guard kolom (DB lama pra-migrasi / fixture minimal): absen → kolom NULL.
    _kolom_loss = {r[1] for r in con.execute("PRAGMA table_info(wiup_loss)")}
    loss09_of = (dict(con.execute(
        "SELECT kode_wiup, loss_2009_2025_ha FROM wiup_loss"))
        if "loss_2009_2025_ha" in _kolom_loss else {})

    # ── 1. periode_ringkasan ───────────────────────────────────────────────────
    build_periode_ringkasan(con, "periode_ringkasan", by_per, forest_of, loss_of_asli,
                            loss09_of=loss09_of)

    # ── 2. (eks periode_deforestasi_tahunan — dihapus) ──────────────────────────
    # loss per (periode, tahun kalender) dari wiup_loss_yearly.
    yearly = con.execute("SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly").fetchall()
    # Lookup (kode, year) → loss; dipakai tabel aktif (2b) & event-study (4).
    loss_lookup = {(kode, y): (loss or 0) for kode, y, loss in yearly}
    # (periode_deforestasi_tahunan DIHAPUS — cleanup 12 Agu r3: kurva kalender
    # per periode tak lagi dipakai UI/API; basis slope memang tahunan-AKTIF.)
    con.execute("DROP TABLE IF EXISTS periode_deforestasi_tahunan")

    # ── 2b. periode_tahunan_aktif (akuntansi IZIN-AKTIF, bukan kohort penuh) ───
    # Tiap konsesi baru dihitung sejak iup_year-nya SENDIRI terbit — loss
    # sebelum izin ada tidak pernah masuk. Garis kohort "menebal" seiring izin
    # bertambah (mis. R2@2015 hanya 21 izin ≈ 3,6 rb ha; kohort penuh 27,7 rb).
    # Kolom stok per (periode, tahun) — semua atas izin ber-iup_year <= tahun:
    #   n_konsesi_aktif   : jumlah izin terbit
    #   luas_aktif_ha     : Σ luas_sk izin aktif
    #   forest_aktif_ha   : Σ hutan-2000 di dalam izin aktif
    #   loss_ha           : loss tahun itu di izin aktif (flow)
    #   loss_kumulatif_sejak_2001_ha : akumulasi loss pasca-izin s/d tahun itu
    build_periode_tahunan_aktif(con, "periode_tahunan_aktif", by_per, loss_lookup,
                                 forest_of, YEAR_MAX)

    # ── 2c. penerbit_tahunan_aktif (lensa PENERBIT: Bupati/Gubernur/Menteri) ─
    # Akuntansi izin-aktif yang sama, tapi dikelompokkan menurut pejabat
    # penerbit — memperlihatkan kebijakan secara langsung: stok Bupati praktis
    # berhenti tumbuh setelah 2014 (UU 23/2014), Gubernur setelah 2019 (UU
    # 3/2020), Menteri terus menerbitkan (termasuk KK/PKP2B pra-2009).
    # Cakupan: SEMUA konsesi ber-iup_year 1998-2025 (termasuk kohort Pra-2009).
    con.execute("DROP TABLE IF EXISTS penerbit_tahunan_aktif")
    con.execute(
        """CREATE TABLE penerbit_tahunan_aktif (
            penerbit TEXT, year INTEGER, loss_ha REAL, n_konsesi_aktif INTEGER,
            luas_aktif_ha REAL, forest_aktif_ha REAL,
            loss_kumulatif_sejak_2001_ha REAL,
            PRIMARY KEY (penerbit, year))"""
    )
    by_penerbit = {}
    for r in PERIODES:
        for x in by_per[r]:
            pj = (x[2] or "").upper()
            if pj in ("BUPATI", "GUBERNUR", "MENTERI"):
                by_penerbit.setdefault(pj, []).append(x)
    for pj, rows_p in sorted(by_penerbit.items()):
        akt_loss = {}
        akt_n = {}
        akt_luas = {}
        akt_forest = {}
        for x in rows_p:
            kode, iy, luas = x[0], x[1], x[3]
            if iy is None:
                continue
            start = max(iy, YEAR_MIN)
            forest = forest_of.get(kode) or 0
            for y in range(start, YEAR_MAX + 1):
                akt_n[y] = akt_n.get(y, 0) + 1
                akt_luas[y] = akt_luas.get(y, 0.0) + (luas or 0)
                akt_forest[y] = akt_forest.get(y, 0.0) + forest
                akt_loss[y] = akt_loss.get(y, 0.0) + loss_lookup.get((kode, y), 0)
        kum = 0.0
        for y in sorted(akt_n):
            kum += akt_loss[y]
            con.execute("INSERT INTO penerbit_tahunan_aktif VALUES (?,?,?,?,?,?,?)",
                        (pj, y, round(akt_loss[y], 2), akt_n[y],
                         round(akt_luas[y], 2), round(akt_forest[y], 2), round(kum, 2)))

    # ── 3. periode_slope (OLS loss~year, basis IZIN-AKTIF/since-permit) ──────────
    # Regresi HANYA atas tahun sejak izin terbit — loss pra-izin bukan "tren
    # periode itu" (regresi jendela-penuh 2001–2025 dulu terkontaminasi tahun
    # pra-izin sehingga headline "tren tercepat" jadi terbalik; lihat audit).
    aktif = since_permit_loss_series(
        [(x[0], x[1]) for r in PERIODES for x in by_per[r]], loss_lookup)
    con.execute("DROP TABLE IF EXISTS periode_slope")
    con.execute(
        """CREATE TABLE periode_slope (
            periode TEXT PRIMARY KEY, slope_ha_per_year REAL, r2 REAL,
            peak_year INTEGER, peak_loss_ha REAL)"""
    )
    for r in PERIODES:
        ys = sorted(aktif[r])
        ls = [aktif[r][y] for y in ys]
        slope, _, r2 = ols_slope(ys, ls)                 # None bila <2 tahun aktif
        peak_y = max(ys, key=lambda y: aktif[r][y]) if ys else None
        con.execute("INSERT INTO periode_slope VALUES (?,?,?,?,?)",
                    (r, round(slope, 2) if slope is not None else None,
                     round(r2, 3) if r2 is not None else None,
                     peak_y, round(aktif[r][peak_y], 2) if peak_y is not None else None))

    # (periode_eventstudy DIHAPUS — cleanup 12 Agu r3: digantikan
    # laju_izin_eventstudy per kelas izin di build_laju_izin.py.)
    con.execute("DROP TABLE IF EXISTS periode_eventstudy")

    # ── 5. periode_komoditas (kontrol komoditas: batubara vs mineral logam) ────
    komod_of = dict(con.execute("SELECT kode_wiup, komoditas FROM wiup_geoportal"))
    build_periode_komoditas(con, "periode_komoditas", by_per, komod_of, loss_of_asli,
                            loss09_of=loss09_of)

    # (periode_ukuran DIHAPUS — cleanup 12 Agu r3: blok "Polarisasi ukuran"
    # dibuang dari Komparasi atas feedback igoen; tak ada konsumen tersisa.)
    con.execute("DROP TABLE IF EXISTS periode_ukuran")

    # ── 7. periode_signifikansi (Kruskal-Wallis + pairwise Mann-Whitney) ───────
    # Non-parametrik karena distribusi sangat skew. Hanya R1/R2/R3 (Pra-2009 =
    # catatan kaki). Pairwise p dikoreksi Holm.
    build_periode_signifikansi(con, "periode_signifikansi", by_per, loss_of_asli)

    # ── 7a. Varian BERSIH (Task F1, FASE F) — periode_ringkasan_bersih,
    # periode_tahunan_aktif_bersih, periode_komoditas_bersih,
    # periode_signifikansi_bersih + atribusi_sawit_yearly. Guard "ada+berisi"
    # (pola tabel_ada_berisi(), sama spt periode_sawit di bawah): dibangun
    # HANYA bila atribusi_sawit_yearly ADA & punya >=1 baris (ditulis skrip
    # attribution_sawit.py sekaligus dgn atribusi_sawit dlm 1 commit, jadi
    # cek satu tabel ini cukup mewakili keduanya).
    has_atribusi_yearly = tabel_ada_berisi(con, "atribusi_sawit_yearly")
    has_bersih = has_atribusi_yearly and tabel_ada_berisi(con, "atribusi_sawit")
    if has_bersih:
        # loss_of_bersih: SEMUA kode_wiup (825) dpt entri (default 0.0) supaya
        # konsesi TANPA baris atribusi_sawit tetap TERHITUNG dgn sawit=0 (spec
        # F1 butir c) — beda dgn loss_of_asli yg None-kan kode tanpa wiup_loss.
        loss_of_bersih = {kode: 0.0 for (kode,) in con.execute("SELECT kode_wiup FROM wiup_geoportal")}
        loss_of_bersih.update({
            kode: (l2021 or 0.0) - (tol2 or 0.0)
            for kode, l2021, tol2 in con.execute(
                "SELECT kode_wiup, loss_2001_2021_ha, loss_sawit_tol2th_2001_2021_ha FROM atribusi_sawit")
        })
        # loss_lookup_bersih: (kode,year) -> loss_ha − sawit_tol2th, klem>=0,
        # HANYA thn<=2021 (Descals berhenti 2021 -> 2022-2025 di luar cakupan).
        atribusi_yearly_of = {
            (kode, y): v for kode, y, v in con.execute(
                "SELECT kode_wiup, year, loss_sawit_tol2th_ha FROM atribusi_sawit_yearly")
        }
        loss_lookup_bersih = {
            (kode, y): max(0.0, base - (atribusi_yearly_of.get((kode, y), 0.0) or 0.0))
            for (kode, y), base in loss_lookup.items() if y <= YEAR_MAX_BERSIH
        }
        # Nama kolom loss varian bersih menyebut jendelanya sendiri (2001-2021
        # tanpa sawit) — bukan meminjam nama jendela 2001-2025 tabel asli; dan
        # loss09_of TIDAK dilewatkan (kolom loss_2009_2025_ha tak ditulis —
        # dulu NULL semua, nama menjanjikan jendela yang tak pernah dihitung).
        build_periode_ringkasan(con, "periode_ringkasan" + BERSIH_SUFFIX, by_per,
                                 forest_of, loss_of_bersih,
                                 loss_col="loss_2001_2021_tanpa_sawit_ha",
                                 pct_poligon_col="pct_poligon_2001_2021_tanpa_sawit",
                                 r_luas_loss_col="r_luas_loss_2001_2021_tanpa_sawit")
        build_periode_tahunan_aktif(con, "periode_tahunan_aktif" + BERSIH_SUFFIX, by_per,
                                     loss_lookup_bersih, forest_of, YEAR_MAX_BERSIH,
                                     kumulatif_col="loss_kumulatif_2001_sampai_2021_ha")
        build_periode_komoditas(con, "periode_komoditas" + BERSIH_SUFFIX, by_per,
                                 komod_of, loss_of_bersih,
                                 loss_col="loss_2001_2021_tanpa_sawit_ha",
                                 pct_poligon_col="pct_poligon_2001_2021_tanpa_sawit")
        build_periode_signifikansi(con, "periode_signifikansi" + BERSIH_SUFFIX, by_per,
                                    loss_of_bersih,
                                    metrik_loss="loss_2001_2021_tanpa_sawit_ha")
    else:
        print("  periode_*_bersih: dilewati (atribusi_sawit_yearly tak ada/kosong)")

    # ── 7b. periode_sawit (LAPISAN opsional — audit atribusi sawit x periode) ──
    # Dibangun HANYA bila atribusi_sawit ada & berisi; kalau tak (mis. Descals
    # blm di-fetch, atau rescrape ringan tanpa raster), dilewati bersih — TAK
    # boleh mendaftarkan provenance utk tabel yg sebetulnya tak ditulis.
    has_atribusi = tabel_ada_berisi(con, "atribusi_sawit")
    has_klasifikasi = tabel_ada_berisi(con, "klasifikasi_izin")
    if has_atribusi:
        build_periode_sawit(con)
    else:
        print("  periode_sawit: dilewati (atribusi_sawit tak ada/kosong)")

    # ── 8. periode_klasifikasi (lapisan pemeriksa: perpanjangan vs izin awal) ─
    # Lapisan OPSIONAL — klasifikasi_izin bisa absen (bundel publik / DB lama).
    kelas_of = {}
    if tabel_ada_berisi(con, "klasifikasi_izin"):
        kelas_of = dict(con.execute("SELECT kode_wiup, kelas FROM klasifikasi_izin"))
    if kelas_of:
        build_periode_klasifikasi(con, "periode_klasifikasi", by_per, kelas_of,
                              loss_of_asli, loss09_of=loss09_of)
        build_periode_klasifikasi_uji(con, "periode_klasifikasi_uji", by_per, kelas_of)

    # ── 9. baseline_tahunan (seluruh konsesi, konteks sebelum dipecah) ────────
    build_baseline_tahunan(con, "baseline_tahunan")

    # ── 8. analysis_meta (PROVENANCE) ─────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS analysis_meta")
    # Kolom status (Fase G butir 7): AKTIF / ARSIP / PROYEKSI — lihat
    # ANALYSIS_STATUS di atas. Konsumen lama (db_browser, halaman Database)
    # membaca kolom eksplisit, jadi kolom tambahan ini tak merusak.
    con.execute(
        """CREATE TABLE analysis_meta (
            nama_tabel TEXT PRIMARY KEY, deskripsi TEXT, sumber TEXT, metode TEXT,
            script TEXT, status TEXT NOT NULL DEFAULT 'AKTIF'
            CHECK (status IN ('AKTIF','ARSIP','PROYEKSI')))"""
    )
    meta = [
        # ── Tabel pengukuran INTI (dibangun scripts/build_combined_db.py) —
        #    didokumentasikan agar (i)-provenance peta/tabel/detail menemukan asal-usulnya.
        ("wiup_geoportal",
         "Atribut & poligon 825 WIUP dari Geoportal ESDM (registry izin tambang).",
         "data/wiup/kalimantan_unique.geojson (scrape layer WIUP_Publish)",
         "kode_wiup, iup_year, luas_sk, pejabat, komoditas, nama_prov/kab, geometri. "
         "pejabat & komoditas dinormalkan UPPER saat ingest agar GROUP BY tak terpecah.",
         "scripts/build_combined_db.py"),
        ("wiup_loss",
         "Kehilangan tutupan pohon TOTAL per konsesi 2001-2025 (Hansen GFC v1.13).",
         "data/analysis/batch_KALIMANTAN_t30_wide.csv (overlay poligon × raster Hansen, threshold 30%)",
         "loss_2001_2025_ha, forest_2000_ha, polygon_area_ha, loss_2001_2025_pct_hutan2000 per kode_wiup (+ kolom jendela era Minerba).",
         "scripts/build_combined_db.py"),
        ("wiup_loss_yearly",
         "Kehilangan tutupan pohon per konsesi PER TAHUN 2001-2025 (long format).",
         "data/analysis/batch_KALIMANTAN_t30_wide.csv (kolom lossyear Hansen)",
         "(kode_wiup, year, loss_ha). Sumber grafik loss/tahun & pivot properti peta.",
         "scripts/build_combined_db.py"),
        ("wiup_temporal",
         "Laju deforestasi pra- vs pasca-terbit izin per konsesi.",
         "data/analysis/temporal_iup_analysis.csv (dari scripts/temporal_iup.py)",
         "rate_pre/rate_tahun_izin_sampai_2025_ha_per_year, verdict, temporal_verdict per kode_wiup.",
         "scripts/build_combined_db.py"),
        ("wiup_match",
         "Pencocokan WIUP ↔ MinerbaOne (badan usaha) — 768/825 cocok.",
         "data/analysis/batch_KALIMANTAN_t30_enriched.csv (dari scripts/match_harder.py)",
         "db_match (yes/no) + rujukan badan usaha bila cocok.",
         "scripts/build_combined_db.py"),
        ("badan_usaha",
         "Registry perusahaan tambang dari MinerbaOne (disalin utuh) — tak dibatasi ke "
         "Kalimantan atau ke 825 konsesi terpadankan (rujukan wiup_match/wiup_master).",
         "data/minerba-kalimantan.db (hasil scrape rescrape/run.sh → rescrape.scrape_minerba, "
         "API publik MinerbaOne)",
         "Copy 1:1 tabel badan_usaha dari minerba-kalimantan.db — tanpa filter/agregasi. "
         "Dipakai via id_badan_usaha utk profil perusahaan (NIB, alamat, kontak) di detail konsesi.",
         "scripts/build_combined_db.py"),
        ("perizinan",
         "Daftar izin per badan usaha dari MinerbaOne (disalin utuh) — nomor SK, tanggal, "
         "komoditas, status Clean and Clear.",
         "data/minerba-kalimantan.db (hasil scrape rescrape/run.sh → rescrape.scrape_minerba, "
         "API publik MinerbaOne /badan-usaha/{id}/list-perizinan)",
         "Copy 1:1 tabel perizinan dari minerba-kalimantan.db. nomor_izin dipakai sbg kunci "
         "pencocokan ke wiup_geoportal.sk_iup (lihat wiup_match & scripts/match_harder.py).",
         "scripts/build_combined_db.py"),
        ("kepadatan_penduduk",
         "Kepadatan penduduk BPS per kabupaten/kota Kalimantan, 2015-2024 — bentuk LONG "
         "(1 baris per kab×tahun; unpivot Fase G 15 Agu, eks kolom lebar d2015..d2024).",
         "data/kepadatan_penduduk.csv (BPS — Kepadatan Penduduk per Kabupaten/Kota 2015-2024)",
         "Ingest dari CSV committed (formatnya tetap lebar ala BPS; di-unpivot saat ingest "
         "step_kepadatan() jadi kolom tahun+kepadatan); kab_normalized dinormalkan saat "
         "ingest agar konsisten join ke wiup_geoportal.kab_normalized.",
         "scripts/build_combined_db.py"),
        ("wiup_master",
         "VIEW siap-query: gabungan wiup_geoportal × wiup_loss × wiup_temporal × wiup_match (825 baris).",
         "join keempat tabel inti di atas via kode_wiup",
         "Dipakai peta, tabel & detail konsesi. Catatan: rincian loss per-tahun TIDAK ada "
         "di view ini — dari wiup_loss_yearly, dipivot server ke /api/polygons (agregasi "
         "tahunan Statistik) & /api/wiup/{kode}, serta ekspor GeoJSON QGIS (sync_geojson_from_db.py).",
         "scripts/build_combined_db.py"),
        ("atribusi_sawit",
         "Atribusi kehilangan tutupan pohon per konsesi ke konversi kelapa sawit "
         "(peta tahun tanam Descals dkk. 2024) — LAPISAN opsional, audit batas administratif WIUP.",
         "Hansen GFC v1.13 (lossyear, treecover2000) × Descals dkk. (2024) tahun-tanam sawit × poligon wiup_geoportal",
         "1 baris/konsesi (825): loss_2001_2021_ha (bisa diperiksa thd sawit, threshold "
         "kanopi & lintas-tile SAMA PERSIS dgn wiup_loss/batch_analyze.py) + 3 varian "
         "loss_sawit_* (tol2th/jeda5th/tahunsama — beda jendela pencocokan piksel loss×tahun-tanam) "
         "+ loss_2022_2025_ha (tak terperiksa, Descals berhenti 2021).",
         "scripts/attribution_sawit.py"),
        ("klasifikasi_izin",
         "Vonis per konsesi: iup_year adalah izin PERTAMA atau PERPANJANGAN — LAPISAN "
         "opsional, audit validitas pengelompokan 3-periode.",
         "wiup_master(jenis_izin, iup_year, nama_tahap_kegiatan, tanggal_berlaku, "
         "tanggal_berakhir, loss_2001_sampai_tahun_izin_ha, loss_tahun_izin_sampai_2025_ha)",
         "vonis() berurutan: PKP2B/KK ber-iup_year>=2009 -> PERPANJANGAN+KUAT (kemustahilan "
         "logis, sistem kontrak karya UU 11/1967 berhenti sejak UU 4/2009); SK Operasi "
         "Produksi durasi<20th (UU 4/2009 Ps.47) -> PERPANJANGAN+INDIKASI; durasi>=20th -> "
         "IZIN_PERTAMA+INDIKASI; selainnya -> TAK_DINILAI. Plus 2 bendera pelengkap "
         "(masa_berlaku_diwarisi, pra_izin_dominan) yg TIDAK menentukan kelas.",
         "scripts/klasifikasi_perpanjangan.py"),
        ("periode_sawit",
         "Agregasi atribusi_sawit per periode kewenangan izin (P1/P2/P3/Pra-2009).",
         "atribusi_sawit × wiup_geoportal.iup_year, dikelompokkan to_periode() (Python, "
         "BUKAN CASE WHEN SQL — iup_year kosong tak boleh diam-diam masuk P3)",
         "Σ tiap kolom atribusi_sawit per periode; loss_2001_2021_tanpa_sawit_ha=loss_2001_2021_ha− "
         "loss_sawit_tol2th_2001_2021_ha; persen_sawit_2001_2021=100·loss_sawit_tol2th_2001_2021_ha/loss_2001_2021_ha "
         "(penyebut = kehilangan 2001-2021, BUKAN luas konsesi, BUKAN hutan 2000). "
         "Dibangun HANYA bila atribusi_sawit ada & berisi (tabel_ada_berisi()); tak "
         "didaftarkan di analysis_meta bila dilewati.",
         "scripts/build_periode_tables.py"),
        ("periode_ringkasan",
         "Ringkasan per periode kewenangan izin (3 periode + Pra-2009).",
         "wiup_geoportal(iup_year,luas_sk,pejabat) + wiup_loss(polygon_area_ha,loss_2001_2025_ha) + wiup_temporal(verdict,rate_post)",
         "Group by periode(iup_year). luas=Σluas_sk & median; loss_2001_2025_ha=Σloss_2001_2025_ha; "
         "pct_poligon_2001_2025=100·Σloss/Σpolygon; pct_akselerasi=100·count(verdict∈{accelerated_post_iup,loss_only_after_iup})/n; "
         "r=Pearson(luas_sk vs loss_2001_2025_ha / rate_tahun_izin_sampai_2025).",
         "scripts/build_periode_tables.py"),
        ("periode_tahunan_aktif",
         "Deret stok IZIN-AKTIF per kohort-SK per tahun: tiap konsesi dihitung sejak "
         "iup_year-nya sendiri (pra-izin tak pernah masuk). STATUS PROYEKSI (Fase G): "
         "praktis proyeksi metode POLOS — deret year>=2009 identik-terbukti (EXCEPT=0) "
         "dgn backtrack_stok aturan POLOS grup_tipe='kohort'; beda hanya baris 2001-2008 "
         "+ akumulasi sejak 2001 (kohort Pra-2009 sejak iup_year, tak diklem 2009).",
         "wiup_loss_yearly × wiup_geoportal(iup_year, luas_sk) × wiup_loss(forest_2000_ha)",
         "Atas izin ber-iup_year <= tahun: n_konsesi_aktif (jumlah), luas_aktif_ha "
         "(Σ luas_sk), forest_aktif_ha (Σ hutan-2000), loss_ha (loss tahun itu), "
         "loss_kumulatif_sejak_2001_ha (akumulasi loss pasca-izin sejak awal jendela "
         "2001). BASIS periode_slope (since-permit).",
         "scripts/build_periode_tables.py"),
        ("penerbit_tahunan_aktif",
         "Deret stok izin-aktif per PENERBIT (Bupati/Gubernur/Menteri) per tahun. "
         "STATUS PROYEKSI (Fase G): ≈ backtrack_stok aturan POLOS grup_tipe='penerbit' "
         "(deret year>=2009 identik-terbukti, EXCEPT=0); beda hanya jendela 2001-2008 "
         "+ akumulasi sejak 2001.",
         "wiup_loss_yearly × wiup_geoportal(iup_year, pejabat, luas_sk) × wiup_loss(forest_2000_ha)",
         "Akuntansi sama dgn periode_tahunan_aktif tapi group by pejabat; mencakup "
         "SEMUA konsesi ber-iup_year 1998-2025 termasuk kohort Pra-2009 (Menteri "
         "KK/PKP2B). Memperlihatkan cutoff kewenangan: Bupati ~berhenti 2014, "
         "Gubernur ~berhenti 2019.",
         "scripts/build_periode_tables.py"),
        ("periode_slope",
         "Slope OLS deforestasi tahunan per periode berbasis IZIN-AKTIF (laju ha/tahun) + tahun puncak.",
         "deret since-permit (sama basis periode_tahunan_aktif): wiup_loss_yearly × wiup_geoportal(iup_year)",
         "Regresi linear loss_ha ~ year HANYA atas tahun sejak iup_year kohort "
         "(since-permit) — BUKAN jendela penuh 2001-2025 yang terkontaminasi loss "
         "pra-izin; slope, r2, tahun & nilai puncak.",
         "scripts/build_periode_tables.py"),
        ("periode_komoditas",
         "Kontrol komoditas: periode × grup (BATUBARA vs MINERAL LOGAM).",
         "wiup_geoportal(komoditas) + wiup_loss + wiup_temporal",
         "grup=BATUBARA jika komoditas diawali 'BATUBARA', selainnya MINERAL LOGAM. "
         "Metrik sama dgn periode_ringkasan (n, luas, loss, %poligon, %aksel, rate median).",
         "scripts/build_periode_tables.py"),
        ("periode_signifikansi",
         "Uji beda antar periode R1/R2/R3 (non-parametrik, distribusi skew).",
         "wiup_geoportal + wiup_loss + wiup_temporal (per konsesi)",
         "Kruskal-Wallis lintas P1|P2|P3 per metrik (rate_tahun_izin_sampai_2025, "
         "loss_2001_2025_ha, luas_sk); "
         "pairwise Mann-Whitney U two-sided, p dikoreksi Holm (p_adjusted); "
         "signifikan_005 = p_adjusted<0,05. Pra-2009 dikecualikan (catatan kaki). "
         "CAVEAT: rate_post = laju loss/tahun sejak izin, jadi jendela pasca-izin "
         "timpang antar-periode (P1 ~12-17 th vs P3 ~1-6 th) — uji atas rate_post tak "
         "sepenuhnya apple-to-apple; total_loss & luas_sk tak terpengaruh.",
         "scripts/build_periode_tables.py"),
        ("atribusi_sawit_yearly",
         "Pecahan PER TAHUN dari atribusi_sawit.loss_sawit_tol2th_2001_2021_ha (varian tol2th/UTAMA) — "
         "LAPISAN opsional, dasar rumus 'loss bersih dari sawit' per (periode,tahun).",
         "Hansen GFC v1.13 (lossyear) × Descals dkk. (2024) tahun-tanam sawit × poligon wiup_geoportal, "
         "jendela 2001-2021 (Descals berhenti 2021)",
         "Σ luas piksel dgn YoP ≥ tahun_loss−2, dikelompokkan (kode_wiup, tahun_loss). Sparse "
         "(tahun tanpa loss-sawit tak disimpan). Konsistensi SUM(per konsesi) vs "
         "atribusi_sawit.loss_sawit_tol2th_2001_2021_ha (window) diverifikasi (ambang 0,5 ha) SEBELUM "
         "atribusi_sawit MAUPUN tabel ini ditulis — galat membatalkan seluruh run. Dibangun "
         "HANYA bila ada & berisi (guard periode_*_bersih di bawah).",
         "scripts/attribution_sawit.py"),
        ("periode_ringkasan" + BERSIH_SUFFIX,
         "Varian BERSIH periode_ringkasan: loss dipotong perkiraan konversi sawit, jendela 2001-2021.",
         "periode_ringkasan (skema identik) + atribusi_sawit(_yearly)",
         "Sama dgn periode_ringkasan, TAPI kolom loss-nya bernama & berjendela sendiri: "
         "loss_2001_2021_tanpa_sawit_ha per konsesi = loss_2001_2021_ha − "
         "loss_sawit_tol2th_2001_2021_ha (atribusi_sawit, varian tol2th/UTAMA); pct_poligon_2001_2021_tanpa_sawit "
         "& r_luas_loss_2001_2021_tanpa_sawit mengikuti; kolom loss_2009_2025_ha TIDAK ada di varian ini "
         "(jendela 2009-2025 tak dihitung utk basis tanpa-sawit). Konsesi tanpa baris "
         "atribusi_sawit dianggap sawit=0 (tetap ikut, bukan dibuang). Tahun 2022-2025 DI LUAR "
         "cakupan varian ini (Descals berhenti 2021). Kolom tak-terkait loss (rate_post, "
         "pct_akselerasi, komposisi_otoritas, dst.) IDENTIK dgn tabel asli. Dibangun HANYA bila "
         "atribusi_sawit_yearly ada & berisi (tabel_ada_berisi()).",
         "scripts/build_periode_tables.py"),
        ("periode_tahunan_aktif" + BERSIH_SUFFIX,
         "Varian BERSIH periode_tahunan_aktif: deret since-permit dgn loss dipotong sawit, DIBATASI tahun ≤2021.",
         "periode_tahunan_aktif (skema identik) + atribusi_sawit_yearly",
         "Sama dgn periode_tahunan_aktif, TAPI loss_ha tahun itu = wiup_loss_yearly.loss_ha − "
         "atribusi_sawit_yearly.loss_sawit_tol2th_2001_2021_ha (COALESCE 0 bila tak ada baris), diklem "
         "≥0. Deret BERHENTI di tahun 2021 (bukan 2025) — Descals tak bisa memeriksa 2022-2025 "
         "sama sekali, jadi tahun itu DIBUANG SELURUHNYA (bukan cuma sawitnya diabaikan). "
         "Dibangun HANYA bila atribusi_sawit_yearly ada & berisi.",
         "scripts/build_periode_tables.py"),
        ("periode_komoditas" + BERSIH_SUFFIX,
         "Varian BERSIH periode_komoditas: kontrol komoditas dgn loss dipotong sawit, jendela 2001-2021.",
         "periode_komoditas (skema identik) + atribusi_sawit",
         "Sama dgn periode_komoditas, TAPI kolom loss bernama loss_2001_2021_tanpa_sawit_ha = "
         "loss_2001_2021_ha − loss_sawit_tol2th_2001_2021_ha per konsesi (idem periode_ringkasan_bersih; "
         "kolom loss_2009_2025_ha tak ada di varian ini). Dibangun HANYA bila atribusi_sawit_yearly "
         "ada & berisi.",
         "scripts/build_periode_tables.py"),
        ("periode_signifikansi" + BERSIH_SUFFIX,
         "Varian BERSIH periode_signifikansi: uji beda antar-periode dgn metrik loss dipotong sawit.",
         "periode_signifikansi (skema identik) + atribusi_sawit",
         "Sama dgn periode_signifikansi, TAPI metrik loss bernilai 'loss_2001_2021_tanpa_sawit_ha' "
         "(bukan 'loss_2001_2025_ha' — jendelanya memang beda) dan memakai loss_2001_2021_ha − "
         "loss_sawit_tol2th_2001_2021_ha per konsesi; metrik rate_tahun_izin_sampai_2025_ha_per_year & luas_sk TAK berubah "
         "(tak bergantung sawit) — nilainya identik dgn tabel asli. Dibangun HANYA bila "
         "atribusi_sawit_yearly ada & berisi.",
         "scripts/build_periode_tables.py"),
        ("periode_klasifikasi",
         "Matriks periode kewenangan × kelas izin (perpanjangan vs izin awal): n, akselerasi, laju pasca, loss, luas.",
         "wiup_geoportal × wiup_loss × wiup_temporal × klasifikasi_izin",
         "Kelompokkan konsesi jendela 1998-2025 (Pra-2009 dikecualikan) menurut to_periode(iup_year) × klasifikasi_izin.kelas; "
         "konsesi tanpa baris klasifikasi dihitung TAK_DINILAI agar Σ sel = n periode.",
         "scripts/build_periode_tables.py"),
        ("periode_klasifikasi_uji",
         "Uji beda proporsi akselerasi antar periode DI DALAM tiap kelas izin (uji ketahanan temuan utama terhadap perancu jenis izin).",
         "periode_klasifikasi (turunan wiup_temporal.verdict)",
         "Fisher exact dua-sisi tiap pasangan periode (P1P2/P1P3/P2P3) per kelas. Fisher, bukan chi-square: sel terkecil 8 kejadian dari 24.",
         "scripts/build_periode_tables.py"),
        ("baseline_tahunan",
         "Deret kehilangan tutupan pohon tahunan 2001-2025 SELURUH konsesi, tanpa pembagian periode (konteks sebelum data dipecah).",
         "wiup_loss_yearly",
         "Σ loss_ha per tahun atas SELURUH konsesi — TANPA filter jendela izin, jadi penyebutnya beda dari tabel periode_*.",
         "scripts/build_periode_tables.py"),
        ("column_meta",
         "Kamus kolom: arti + rumus + sumber tiap kolom (untuk halaman Database).",
         "ditulis manual di build_periode_tables.py (COLUMN_META), divalidasi anti-yatim",
         "1 baris/kolom terdokumentasi; kolom turunan diisi rumus+sumber, kolom mentah cukup deskripsi.",
         "scripts/build_periode_tables.py"),
        ("atribusi_izin_aktif",
         "Atribusi loss ke IZIN AKTIF, jendela era Minerba 2009-2025 — bentuk BARIS "
         "(unpivot Fase G 15 Agu): 1 baris per (konsesi, aturan TANPA_ATRIBUSI/INDIKASI/POLOS).",
         "wiup_geoportal × klasifikasi_izin × wiup_loss_yearly",
         "TANPA_ATRIBUSI (eks X0): semua loss 2009-2025, mulai=2009. INDIKASI (eks B): "
         "PERPANJANGAN aktif sepanjang jendela; IZIN_PERTAMA/TAK_DINILAI sejak max(2009, iup_year). "
         "POLOS (eks D): semua sejak max(2009, iup_year). Aturan C/PERKIRAAN "
         "(iup_year+durasi_sk-20, Ps. 47) DIARSIPKAN 15 Agu & setop ditulis — data lama di riwayat git.",
         "scripts/build_atribusi_izin.py"),
        ("atribusi_izin_aktif_ringkas",
         "Ringkasan 1 baris/aturan — SUMBER TUNGGAL angka atribusi (loss, % hutan-2009, n kohort).",
         "atribusi_izin_aktif",
         "Σ loss per aturan (TANPA_ATRIBUSI/INDIKASI/POLOS — selaras kosakata backtrack_*); "
         "pct = 100·loss/(Σforest_2000 − Σloss 2001-2008).",
         "scripts/build_atribusi_izin.py"),
        # ── Pivot "laju dulu, periode belakangan" (spec 2026-08-12-laju-izin-pivot) ─
        ("laju_izin_konsesi",
         "Laju deforestasi per konsesi menurut jam BUKTI LAPANGAN (aturan E — "
         "menggantikan asumsi kelas izin), dua basis: bersih (Hansen − sawit "
         "Descals, [mulai, 2021]) & kotor (Hansen, [mulai, 2025]).",
         "wiup_geoportal × klasifikasi_izin × wiup_loss × wiup_loss_yearly × atribusi_sawit_yearly",
         "mulai = min(max(2009,tahun_bukti), max(2009,iup_year)); tahun_bukti = tahun pertama "
         "[2001, 2021] dgn loss non-sawit ≥ 1 ha (bukti pra-2009 sah; `mulai`-nya diklem "
         "ke 2009). hutan_mulai = forest_2000 − Σ loss "
         "2001..mulai−1; laju ha/thn = loss_basis/tahun_aktif; %/thn = 100·laju/hutan_mulai. "
         "Bersih NULL bila mulai > 2021 atau lapisan sawit absen.",
         "scripts/build_laju_izin.py"),
        ("laju_izin_ringkas",
         "VIEW kompatibilitas (Fase G 15 Agu): distribusi laju deforestasi "
         "(median/mean/p25/p75/p90) per basis × dimensi — baris CITRA dari "
         "backtrack_laju_ringkas (dulu tabel kembar yang dihitung terpisah).",
         "backtrack_laju_ringkas (WHERE aturan='CITRA')",
         "CREATE VIEW ... SELECT semua kolom non-aturan FROM backtrack_laju_ringkas "
         "WHERE aturan='CITRA' — terbukti EXCEPT dua arah 0 baris vs tabel lama; "
         "dimensi periode hanya P1-P3 (Pra-2009 & di-luar-jendela bukan bagian perbandingan).",
         "scripts/build_laju_izin.py"),
        ("backtrack_tahunan",
         "Pembanding 3 metode backtrack — flow loss & jumlah konsesi aktif per tahun "
         "per aturan (CITRA/INDIKASI/POLOS). CITRA = codename internal utk metode "
         "'Deteksi Hansen' (label UI & tesis).",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_loss_yearly × atribusi_sawit_yearly",
         "aturan CITRA pakai laju_izin_konsesi.mulai; INDIKASI/POLOS pakai "
         "atribusi_izin_aktif.mulai baris aturan yang sama (bentuk unpivot Fase G). "
         "Baris CITRA diikat invarian == tabel utama.",
         "scripts/build_laju_izin.py"),
        ("backtrack_kohort",
         "Loss per KOHORT tahun-terbit-SK per metode backtrack, jendela [mulai, 2025] "
         "(+varian tanpa-sawit [mulai, 2021]) — eks backtrack_periode, rename Fase G "
         "15 Agu (kolom kohort, supaya tak tabrakan makna dgn backtrack_periode_kalender).",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_loss_yearly",
         "Σ per konsesi loss jendela [mulai versi aturan, 2025], group by to_periode(iup_year).",
         "scripts/build_laju_izin.py"),
        ("backtrack_periode_kalender",
         "Breakdown flow kehilangan per JENDELA TAHUN KALENDER (P1 2009-2014 / "
         "P2 2015-2019 / P3 2020-2025) per metode backtrack + statistik luas "
         "WILAYAH AKTIF s.d. akhir jendela — redefinisi periode 15 Agu: periode "
         "= rentang kalender, bukan kohort tahun-terbit-SK.",
         "backtrack_tahunan + wiup_geoportal.luas_sk",
         "loss_ha = Σ backtrack_tahunan.loss_ha tahun-tahun jendela (konsesi aktif versi "
         "aturan); loss_tanpa_sawit_sampai_2021_ha hanya s.d. 2021 (batas Descals) sehingga P3 punya "
         "loss_2022_2025_belum_terperiksa_ha; n_aktif_akhir = n_aktif @ tahun_akhir (KUMULATIF). "
         "Kolom luas_aktif_total/mean/median/gini = statistik luas_sk atas himpunan "
         "KUMULATIF {mulai versi aturan <= tahun_akhir} (aktif kapan pun s.d. akhir "
         "jendela) — ikut metode; kohort-SK statis tetap di periode_ringkasan.",
         "scripts/build_laju_izin.py"),
        ("backtrack_komoditas",
         "Loss per (kohort SK × grup komoditas) per metode backtrack (kolom kohort — "
         "eks periode, rename Fase G).",
         "backtrack_kohort + wiup_geoportal.komoditas",
         "Sel = kohort × {BATUBARA, MINERAL LOGAM}; jendela [mulai, 2025].",
         "scripts/build_laju_izin.py"),
        ("backtrack_klasifikasi",
         "Loss per (kohort SK × kelas izin) per metode backtrack (kolom kohort — "
         "eks periode, rename Fase G).",
         "backtrack_kohort + klasifikasi_izin.kelas",
         "Sel = kohort × kelas; jendela [mulai, 2025].",
         "scripts/build_laju_izin.py"),
        ("backtrack_stok",
         "Akumulasi konsesi AKTIF per tahun per metode backtrack (grup_tipe kohort / "
         "penerbit — nilai 'kohort' eks 'periode', rename Fase G) — versi backtrack "
         "dari periode_tahunan_aktif & penerbit_tahunan_aktif.",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_geoportal × wiup_loss_yearly",
         "Aktif = mulai versi aturan <= tahun; loss flow & kumulatif sejak 2009.",
         "scripts/build_laju_izin.py"),
        ("backtrack_sawit",
         "Pangsa sawit di jendela [mulai, 2021] per kohort SK per metode backtrack "
         "(kolom kohort — eks periode, rename Fase G).",
         "atribusi_sawit_yearly × laju_izin_konsesi × atribusi_izin_aktif",
         "Penyebut = loss [mulai, 2021] (batas Descals); persen = 100·sawit/loss.",
         "scripts/build_laju_izin.py"),
        ("backtrack_laju_ringkas",
         "Persentil laju deforestasi per metode backtrack (skema laju_izin_ringkas + aturan).",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_loss_yearly × atribusi_sawit_yearly",
         "hitung_laju() per konsesi atas mulai versi aturan; persentil interpolasi linier.",
         "scripts/build_laju_izin.py"),
        ("backtrack_distribusi",
         "Distribusi ukuran per metode backtrack: total/mean/median/gini utk luas SK "
         "dan luas ditambang (± dikurangi sawit) — blok 4-5 /era. "
         "Luas SK TIDAK punya varian dikurangi-sawit (keputusan igoen 16 Agu: sawit "
         "hanya mengoreksi deforestasi, bukan luas izin — luas SK fakta poligon). "
         "Redefinisi 15 Agu: kelompok P1/P2/P3 = KUMULATIF aktif s.d. akhir jendela "
         "(mulai <= 2014/2019/2025), bukan kohort iup_year.",
         "wiup_geoportal × wiup_loss_yearly × atribusi_sawit_yearly",
         "gini rumus selisih-berpasangan; ditambang_tanpa_sawit hanya ditulis bila "
         "lapisan sawit ada — data-full tanpa Descals tak memuatnya; ditambang P1/P2/P3 = loss DALAM jendela kalender "
         "kelompok DIKLEM per konsesi ke tahun mulainya: [max(tahun_awal, mulai versi aturan), "
         "tahun_akhir] (tanpa-sawit s.d. min(tahun_akhir, 2021)) — konsesi yang baru mulai di "
         "tengah jendela dihitung sejak mulainya; ditambang SEMUA = sejak-mulai "
         "[mulai versi aturan, 2025] (tanpa-sawit [mulai, 2021]).",
         "scripts/build_laju_izin.py"),
        ("backtrack_wilayah",
         "Irisan GEOGRAFI per metode backtrack: kehilangan & intensitas per provinsi dan "
         "per kabupaten (plus satu baris tingkat 'total' utk rekonsiliasi) pada jendela "
         "[mulai aktif versi aturan, 2025] — sumber blok geografi halaman Statistik.",
         "wiup_geoportal (nama_prov, kab_normalized, luas_sk) × wiup_loss (hutan_2009_ha) × "
         "wiup_loss_yearly × atribusi_sawit_yearly",
         "Anggota = konsesi ber-mulai versi aturan <= 2025. Provinsi: konsesi lintas-provinsi "
         "dihitung UTUH di provinsi pertama. Kabupaten: kab_normalized dipecah koma, luas/hutan/"
         "loss DIBAGI RATA antar kabupaten (n_konsesi tidak dibagi — tercatat di tiap kabupaten). "
         "Σ hektar tingkat provinsi = Σ tingkat kabupaten = baris tingkat total (invarian "
         "backtrack-wilayah-rekonsil). pct = 100·loss/hutan_2009_ha.",
         "scripts/build_laju_izin.py"),
        ("backtrack_komoditas_rinci",
         "Kehilangan & intensitas per NAMA komoditas (bukan 2 grup) per metode backtrack — "
         "sumber dua slide komoditas halaman Statistik (volume & intensitas).",
         "wiup_geoportal.komoditas × wiup_loss.hutan_2009_ha × wiup_loss_yearly × atribusi_sawit_yearly",
         "Group by komoditas apa adanya atas konsesi ber-mulai <= 2025; jendela [mulai, 2025] "
         "(tanpa-sawit [mulai, 2021]); pct = 100·loss/hutan_2009_ha.",
         "scripts/build_laju_izin.py"),
        ("backtrack_konsesi_top",
         "Dua puluh lima konsesi penyumbang kehilangan terbesar per metode backtrack "
         "(peringkat disimpan) — sumber slide aktor halaman Statistik.",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_geoportal × wiup_loss × wiup_loss_yearly",
         "Urut menurun loss jendela [mulai aktif versi aturan, 2025], ambil TOP_N=25 teratas; "
         "nama_usaha/komoditas/nama_prov didenormalisasi utk label chart.",
         "scripts/build_laju_izin.py"),
        ("backtrack_keparahan",
         "Histogram keparahan per konsesi per metode backtrack: berapa konsesi kehilangan "
         "0-10% / 10-25% / 25-50% / 50-75% / >75% hutan-2009-nya sejak aktif.",
         "wiup_loss.hutan_2009_ha × wiup_loss_yearly × laju_izin_konsesi × atribusi_izin_aktif",
         "pct per konsesi = 100·loss[mulai,2025]/hutan_2009_ha; ember teratas TERBUKA ke atas "
         "(loss bisa > 100% hutan acuan). Konsesi berpenyebut 0 masuk n_tanpa_penyebut, bukan "
         "dibuang diam-diam.",
         "scripts/build_laju_izin.py"),
        ("backtrack_zona_bebas",
         "Kab/kota Kalimantan yang belum dimasuki konsesi tambang, per tahun 2009-2025 per "
         "metode backtrack — menggantikan endpoint /api/clean-kabupaten (dihapus 16 Agu) yang "
         "berjam tahun terbit SK.",
         "konstanta MASTER_KABKOTA (56 kab/kota, Kemendagri 2024) × wiup_geoportal.kab_normalized",
         "kab_normalized dinormalisasi (buang 'KAB.'/'KOTA ', koreksi ejaan, pecah koma & "
         "'HULU SUNGAI (TENGAH,SELATAN)') lalu dicocokkan ke master; sebuah kab/kota 'dimasuki' "
         "pada tahun y bila ada konsesi di wilayahnya dgn mulai versi aturan <= y. n_kab_bersih "
         "monoton tak naik (invarian backtrack-zona-monoton).",
         "scripts/build_laju_izin.py"),
        ("backtrack_signifikansi",
         "Uji beda antar-periode (Kruskal-Wallis + Mann-Whitney/Holm) per metode backtrack, "
         "atas loss & laju %/thn jendela [mulai, 2025].",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_loss_yearly",
         "Sampel = konsesi P1/P2/P3 ber-mulai; kosong bila scipy absen saat build.",
         "scripts/build_laju_izin.py"),
        # ── Fase T (16 Agu): penopang bagian "Temuan" halaman Statistik ────
        ("backtrack_tak_terlihat",
         "Berapa kehilangan 2009-2025 yang JATUH SEBELUM jam tiap metode mulai — "
         "per kohort tahun-terbit-SK, dgn penyebut yang sengaja BEBAS METODE.",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_loss_yearly × wiup_geoportal.iup_year",
         "tak_terlihat_ha = Σ loss [2009, mulai versi aturan − 1] (konsesi tanpa tahun mulai "
         "menyumbang seluruh loss 2009-2025); penyebut loss_2009_2025_ha = Σ loss 2009-2025 "
         "kohort itu tanpa atribusi apa pun, jadi persen ketiga metode sebanding. Kolom "
         "n_prask_2001_* memakai jendela penuh [2001, iup_year−1] (pertanyaannya 'sudah dibuka "
         "sebelum SK?'), bebas metode. Rekonsiliasi tak_terlihat + loss_terhitung = penyebut "
         "diikat invarian backtrack-tak-terlihat-rekonsil.",
         "scripts/build_laju_izin.py"),
        ("backtrack_selisih",
         "Sebaran jarak tahun (iup_year − tahun mulai aktif) per metode + sebaran tahun BUKTI "
         "mentah sebelum diklem ke 2009 — dasar pengakuan batas metode Deteksi Hansen.",
         "laju_izin_konsesi × atribusi_izin_aktif × wiup_geoportal.iup_year",
         "Blok 'selisih': 6 ember (tak terdefinisi / ≤0 / 1–2 / 3–5 / 6–10 / 11+), Σ = jumlah "
         "konsesi (diikat invarian). Blok 'selisih_ringkas': p25/median/p75/maks untuk selisih > 0. "
         "Blok 'klem': berapa konsesi bertahun-mulai persis 2009 (batas bawah jendela). Blok "
         "'tahun_bukti' HANYA aturan CITRA: cacah tahun_bukti apa adanya (2001-2008 digabung "
         "satu ember, lalu per tahun ≥ 2009, plus 'tanpa bukti').",
         "scripts/build_laju_izin.py"),
        ("backtrack_kesepakatan",
         "Kemiripan deret tahunan 2009-2025 antar pasangan metode (Pearson & Spearman) × dua "
         "metrik, plus jumlah irisan 10 besar konsesi — uji kekokohan kesimpulan terhadap "
         "pilihan metode.",
         "backtrack_tahunan + backtrack_konsesi_top",
         "Tiga pasangan (CITRA-INDIKASI, CITRA-POLOS, INDIKASI-POLOS) × metrik loss_ha & "
         "pct_thn (= 100·loss tahun itu / hutan_awal_tahun_ha). Spearman = Pearson atas "
         "peringkat rata-rata. Keduanya dilaporkan dgn sengaja: kesepakatan tinggi hanya di "
         "pct_thn; loss_ha justru berbeda tajam, dan itu temuannya.",
         "scripts/build_laju_izin.py"),
        ("backtrack_tahun_ekstrem",
         "Tiga tahun tertinggi (puncak) & terendah (palung) deret tahunan tiap metode × metrik "
         "— pendamping backtrack_kesepakatan.",
         "backtrack_tahunan",
         "Urut menurun/menaik atas deret 2009-2025 metode itu; disimpan 3 teratas tiap arah "
         "supaya klien tak mengurut ulang.",
         "scripts/build_laju_izin.py"),
        ("backtrack_lorenz",
         "Kurva pemusatan: pangsa kehilangan yang ditanggung X% konsesi teratas, berdampingan "
         "dgn pangsa LUAS SK yang dipegang X% konsesi terluas + Gini keduanya.",
         "wiup_loss_yearly × wiup_geoportal.luas_sk",
         "Titik 0,10,…,100; n_konsesi = ⌈persentil%·n⌉ konsesi teratas (pembulatan ke atas). "
         "Populasi = konsesi ber-tahun-mulai ≤ 2025 versi aturan; jendela loss [mulai, 2025]. "
         "gini_loss & gini_luas diulang di tiap baris (sifat sebaran, bukan sifat titik kurva); "
         "monotonisitas & ujung 100% diikat invarian backtrack-lorenz.",
         "scripts/build_laju_izin.py"),
        ("backtrack_top_union",
         "Gabungan 10 besar konsesi KETIGA metode dalam satu baris per konsesi — peringkat, "
         "tahun mulai & kehilangan tiap metode berdampingan, plus tahun/durasi SK.",
         "backtrack_konsesi_top + laju_izin_konsesi × atribusi_izin_aktif × klasifikasi_izin",
         "Anggota = konsesi yang masuk 10 besar di metode mana pun. peringkat_* adalah peringkat "
         "PENUH (1..n seluruh konsesi metode itu), bukan 1..10 — supaya 'peringkat 1 di Deteksi "
         "Hansen, peringkat 94 di Polos' terbaca. Urutan baris menurun menurut loss_citra_ha.",
         "scripts/build_laju_izin.py"),
        ("konsesi_aktif_tahunan",
         "VIEW kompatibilitas (Fase G 15 Agu): berapa konsesi yang sudah aktif tiap tahun "
         "(deret kumulatif) menurut Deteksi Hansen vs tanggal SK — baris CITRA dari "
         "backtrack_tahunan (dulu tabel kembar; pendamping baseline_tahunan yang isinya hektar).",
         "backtrack_tahunan (WHERE aturan='CITRA')",
         "CREATE VIEW ... SELECT year, n_aktif AS n_mulai_aktif, n_sk_terbit, "
         "n_aktif_sebelum_sk FROM backtrack_tahunan WHERE aturan='CITRA' — terbukti "
         "EXCEPT dua arah 0 baris vs tabel lama. n_mulai_aktif NULL sebelum 2009 "
         "(aturan mulai-aktif hanya berlaku sejak 2009 — batas aturan, bukan nol temuan).",
         "scripts/build_laju_izin.py"),
        ("laju_izin_eventstudy",
         "Loss per tahun-relatif-terbit-izin (rel_year = tahun − iup_year) per kelas izin — "
         "kurva akselerasi di sekitar t=0.",
         "atribusi_izin_aktif × wiup_loss_yearly × atribusi_sawit_yearly",
         "Kohort iup_year 2009-2025; rel_year −10..+16; n = konsesi yang tahun kalendernya "
         "masuk jangkauan (kotor 2001-2025, bersih 2001-2021). t=0 PERPANJANGAN = SK "
         "perpanjangan (sisi pra tercemar) — kurva bersih-tafsir = kelas IZIN_PERTAMA.",
         "scripts/build_laju_izin.py"),
    ]
    # column_meta HARUS dibangun SEBELUM snapshot 'existing' di bawah, agar tabel
    # itu sudah ada saat sqlite_master dibaca — kalau tidak, baris provenance
    # ("column_meta", …) ikut terbuang oleh existing_meta_rows() pada build
    # single-pass (lihat bash rescrape/process.sh, yang memanggil skrip ini
    # persis sekali).
    build_column_meta(con)

    # Hanya catat provenance tabel yang benar-benar ada (mis. atribusi_sawit/
    # klasifikasi_izin absen bila prasyaratnya belum dijalankan) — registry tak
    # boleh berbohong.
    existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    # atribusi_sawit/klasifikasi_izin bisa ADA sbg cangkang kosong (LAPISAN_SHELLS)
    # tapi belum diisi — snapshot sqlite_master di atas tak membedakan "ada" dari
    # "ada & berisi"; buang keduanya (dan periode_sawit yg bergantung padanya)
    # dari registry provenance memakai guard tabel_ada_berisi() yg sudah dihitung
    # di atas (has_atribusi/has_klasifikasi), bukan cek keberadaan mentah.
    if not has_atribusi:
        existing -= {"atribusi_sawit", "periode_sawit"}
    if not has_klasifikasi:
        existing -= {"klasifikasi_izin"}
    if not has_atribusi_yearly:
        existing -= {"atribusi_sawit_yearly"}
    if not has_bersih:
        existing -= {"periode_ringkasan" + BERSIH_SUFFIX, "periode_tahunan_aktif" + BERSIH_SUFFIX,
                     "periode_komoditas" + BERSIH_SUFFIX, "periode_signifikansi" + BERSIH_SUFFIX}
    con.executemany("INSERT INTO analysis_meta VALUES (?,?,?,?,?,?)",
                    [row + (ANALYSIS_STATUS.get(row[0], "AKTIF"),)
                     for row in existing_meta_rows(meta, existing)])

    con.commit()
    # DB dilayani read-only tanpa dir writable → mode DELETE (bukan WAL).
    con.execute("PRAGMA journal_mode=DELETE")
    con.commit()

    # ── Ringkas ke stdout ─────────────────────────────────────────────────────
    print("OK — tabel dibuat:")
    for r in PERIODES:
        row = con.execute("SELECT n, loss_2001_2025_ha, pct_akselerasi, r_luas_loss_2001_2025 FROM periode_ringkasan WHERE periode=?", (r,)).fetchone()
        sl = con.execute("SELECT slope_ha_per_year, peak_year FROM periode_slope WHERE periode=?", (r,)).fetchone()
        print(f"  {r:8} n={row[0]:>3} loss={row[1]:>10.0f} %aksel={row[2]:>5.1f} r={row[3]:.3f} "
              f"slope={sl[0]:>7.1f} ha/th puncak={sl[1]}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
