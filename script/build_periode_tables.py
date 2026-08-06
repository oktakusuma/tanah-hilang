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
  2. periode_deforestasi_tahunan— (periode, year, loss_ha, n_konsesi) 2001-2025 → slope.
  3. periode_slope              — 1 baris/periode: slope OLS loss~year, r2, puncak.
  4. periode_eventstudy         — (periode, rel_year, n_konsesi, sum_loss_ha, mean_loss_ha)
                                rel_year = tahun kalender − iup_year (perbandingan adil).
  5. periode_komoditas          — periode × grup komoditas (BATUBARA vs MINERAL LOGAM):
                                n, luas, loss, %poligon, %akselerasi, laju pasca median.
  6. periode_ukuran             — distribusi ukuran konsesi per periode (p10..p90, mean,
                                share top-10%, gini) → bukti klaim "polarisasi".
  7. periode_signifikansi       — uji beda antar periode R1/R2/R3: Kruskal-Wallis +
                                pairwise Mann-Whitney U (butuh scipy; skip jika absen).
  8. analysis_meta            — PROVENANCE tiap tabel (sumber + metode + script).

Sumber kolom: wiup_geoportal(iup_year,luas_sk,pejabat), wiup_loss(polygon_area_ha,
total_loss_ha), wiup_temporal(verdict,rate_post_ha_per_year), wiup_loss_yearly(year,loss_ha).

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
    ("periode_ringkasan", "loss_total_ha", "Total kehilangan tutupan pohon (ha) seluruh konsesi periode, 2001-2025.",
     "Σ total_loss_ha", "wiup_loss.total_loss_ha"),
    ("periode_ringkasan", "polygon_total_ha", "Total luas poligon konsesi hasil overlay raster Hansen (bisa beda tipis dari luas_sk dokumen SK).",
     "Σ polygon_area_ha", "wiup_loss.polygon_area_ha"),
    ("periode_ringkasan", "forest2000_total_ha", "Total tutupan pohon tahun 2000 di dalam konsesi periode.",
     "Σ forest_2000_ha", "wiup_loss.forest_2000_ha"),
    ("periode_ringkasan", "pct_poligon", "Persen luas poligon konsesi yang kehilangan tutupan pohon.",
     "100 · Σ total_loss_ha / Σ polygon_area_ha", "wiup_loss"),
    ("periode_ringkasan", "rate_post_mean", "Rata-rata laju deforestasi pasca-izin (ha/tahun) konsesi periode.",
     "mean(rate_post_ha_per_year)", "wiup_temporal.rate_post_ha_per_year"),
    ("periode_ringkasan", "rate_post_median", "Median laju deforestasi pasca-izin (ha/tahun) — tahan-outlier.",
     "median(rate_post_ha_per_year)", "wiup_temporal.rate_post_ha_per_year"),
    ("periode_ringkasan", "pct_akselerasi", "Persen konsesi yang laju deforestasinya berakselerasi pasca-izin terbit.",
     "100 · count(verdict ∈ {accelerated_post_iup, loss_only_after_iup}) / n", "wiup_temporal.verdict"),
    ("periode_ringkasan", "r_luas_loss", "Korelasi Pearson antara luas SK konsesi dan total loss (apakah konsesi lebih luas cenderung lebih banyak deforestasi).",
     "Pearson(luas_sk, total_loss_ha) per konsesi periode", "wiup_geoportal.luas_sk × wiup_loss.total_loss_ha"),
    ("periode_ringkasan", "r_luas_ratepost", "Korelasi Pearson antara luas SK konsesi dan laju deforestasi pasca-izin.",
     "Pearson(luas_sk, rate_post_ha_per_year) per konsesi periode", "wiup_geoportal.luas_sk × wiup_temporal.rate_post_ha_per_year"),
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
    ("periode_tahunan_aktif", "loss_kumulatif_ha", "Akumulasi loss pasca-izin sejak awal jendela s/d tahun itu (stok, bukan flow).",
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
    ("penerbit_tahunan_aktif", "loss_kumulatif_ha", "Akumulasi loss pasca-izin sejak awal jendela s/d tahun itu, per penerbit (stok, bukan flow). Mencakup SEMUA iup_year 1998-2025 termasuk kohort Pra-2009 (Menteri KK/PKP2B).",
     "Σ_{y≤tahun} loss_ha (deret since-permit), per penerbit", "kolom loss_ha tabel ini"),

    # ── periode_deforestasi_tahunan: loss per (periode, tahun kalender) — kohort penuh ─
    ("periode_deforestasi_tahunan", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_deforestasi_tahunan", "year", "Tahun kalender (2001-2025).", None, "wiup_loss_yearly.year"),
    ("periode_deforestasi_tahunan", "loss_ha", "Total loss (ha) tahun kalender itu, KOHORT PENUH periode (semua konsesi periode, termasuk sebelum iup_year-nya sendiri) — deskriptif, bukan basis slope (lihat periode_tahunan_aktif untuk basis since-permit).",
     "Σ loss_ha semua konsesi periode pada tahun itu", "wiup_loss_yearly × periode(iup_year)"),
    ("periode_deforestasi_tahunan", "n_konsesi", "Jumlah total konsesi di periode (konstan sepanjang deret tahun; BUKAN stok izin-aktif — beda dgn periode_tahunan_aktif.n_konsesi_aktif).",
     "count(kode_wiup) per periode", "wiup_geoportal.iup_year"),

    # ── periode_eventstudy: rata-rata loss pada waktu-relatif-ke-izin ───────────
    ("periode_eventstudy", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_eventstudy", "rel_year", "Waktu relatif ke terbit izin: tahun kalender − iup_year (rentang -15..+15) — basis perbandingan adil antar konsesi dgn iup_year berbeda.",
     "rel = tahun_kalender − iup_year", "wiup_loss_yearly.year, wiup_geoportal.iup_year"),
    ("periode_eventstudy", "n_konsesi", "Jumlah konsesi periode yang 'teramati' pada rel_year itu (iup_year+rel_year masuk jendela 2001-2025).",
     "count(kode_wiup) dengan iup_year+rel ∈ [2001,2025]", "wiup_geoportal.iup_year"),
    ("periode_eventstudy", "sum_loss_ha", "Total loss (ha) seluruh konsesi teramati pada rel_year itu.",
     "Σ loss_ha konsesi dengan tahun_kalender = iup_year+rel", "wiup_loss_yearly"),
    ("periode_eventstudy", "mean_loss_ha", "Rata-rata loss (ha) per konsesi pada rel_year itu.",
     "sum_loss_ha / n_konsesi", "kolom pada tabel ini"),

    # ── periode_komoditas: kontrol komoditas (BATUBARA vs MINERAL LOGAM) ───────
    ("periode_komoditas", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_komoditas", "grup_komoditas", "Grup komoditas untuk kontrol variabel: BATUBARA atau MINERAL LOGAM.",
     "BATUBARA jika komoditas diawali 'BATUBARA', selainnya MINERAL LOGAM", "wiup_geoportal.komoditas"),
    ("periode_komoditas", "n", "Jumlah konsesi grup-komoditas dalam periode.", "count(kode_wiup) per (periode, grup_komoditas)", "wiup_geoportal"),
    ("periode_komoditas", "luas_total_ha", "Total luas SK (ha) grup-komoditas dalam periode.", "Σ luas_sk", "wiup_geoportal.luas_sk"),
    ("periode_komoditas", "luas_median_ha", "Median luas SK grup-komoditas dalam periode.", "median(luas_sk)", "wiup_geoportal.luas_sk"),
    ("periode_komoditas", "loss_total_ha", "Total kehilangan tutupan pohon (ha) grup-komoditas dalam periode.", "Σ total_loss_ha", "wiup_loss.total_loss_ha"),
    ("periode_komoditas", "pct_poligon", "Persen luas poligon grup-komoditas yang kehilangan tutupan pohon.",
     "100 · Σ total_loss_ha / Σ polygon_area_ha", "wiup_loss"),
    ("periode_komoditas", "rate_post_median", "Median laju deforestasi pasca-izin (ha/tahun) grup-komoditas.",
     "median(rate_post_ha_per_year)", "wiup_temporal.rate_post_ha_per_year"),
    ("periode_komoditas", "pct_akselerasi", "Persen konsesi grup-komoditas yang laju deforestasinya berakselerasi pasca-izin.",
     "100 · count(verdict ∈ {accelerated_post_iup, loss_only_after_iup}) / n", "wiup_temporal.verdict"),

    # ── periode_ukuran: distribusi ukuran konsesi (bukti polarisasi) ────────────
    ("periode_ukuran", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_ukuran", "n", "Jumlah konsesi dengan luas_sk valid dalam periode.", "count(luas_sk IS NOT NULL)", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "p10", "Persentil ke-10 luas SK konsesi periode (ha).", "percentile(luas_sk, 10), interpolasi linear", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "p25", "Persentil ke-25 luas SK konsesi periode (ha).", "percentile(luas_sk, 25), interpolasi linear", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "p50", "Persentil ke-50 (median) luas SK konsesi periode (ha).", "percentile(luas_sk, 50), interpolasi linear", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "p75", "Persentil ke-75 luas SK konsesi periode (ha).", "percentile(luas_sk, 75), interpolasi linear", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "p90", "Persentil ke-90 luas SK konsesi periode (ha).", "percentile(luas_sk, 90), interpolasi linear", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "mean_ha", "Rata-rata luas SK konsesi periode (ha).", "Σ luas_sk / n", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "share_top10pct", "Persen luas total yang dikuasai 10% konsesi terbesar periode — bukti konsentrasi/polarisasi.",
     "100 · Σ luas_sk (10% konsesi terbesar) / Σ luas_sk (semua)", "wiup_geoportal.luas_sk"),
    ("periode_ukuran", "gini", "Koefisien Gini distribusi luas konsesi periode (0=merata, 1=timpang total).",
     "gini = 2·Σᵢ(i·xᵢ)/(n·Σx) − (n+1)/n, atas luas_sk terurut naik (i=1..n)", "wiup_geoportal.luas_sk"),

    # ── periode_signifikansi: uji beda antar periode P1/P2/P3 ───────────────────
    ("periode_signifikansi", "metrik", "Metrik yang diuji beda antar-periode: rate_post_ha_per_year, total_loss_ha, atau luas_sk.",
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
    ("wiup_loss", "total_loss_ha", "Total kehilangan tutupan pohon 2001-2025 di dalam poligon (hanya piksel yang tergolong hutan 2000) — dasar angka headline tesis.",
     "Σ_tahun loss_ha (2001-2025)", "Hansen lossyear × poligon WIUP (= Σ wiup_loss_yearly.loss_ha)"),
    ("wiup_loss", "loss_pct_of_polygon", "Persen luas POLIGON (bukan hutan) yang hilang tutupan pohonnya.",
     "100 · total_loss_ha / polygon_area_ha", "wiup_loss"),
    ("wiup_loss", "loss_pct_of_forest", "Persen tutupan pohon 2000 yang hilang — metrik utama tesis (headline 40,7% dihitung dari agregat kolom ini).",
     "100 · total_loss_ha / forest_2000_ha", "wiup_loss"),
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
    ("wiup_temporal", "loss_pre_iup_ha", "Total loss (ha) jendela PRA-izin: 2001 s/d tahun sebelum izin terbit (iup_year−1).", "Σ loss_ha, tahun < iup_year", "wiup_loss_yearly"),
    ("wiup_temporal", "loss_post_iup_ha", "Total loss (ha) jendela PASCA-izin: tahun izin terbit (iup_year, inklusif) s/d 2025.", "Σ loss_ha, tahun ≥ iup_year", "wiup_loss_yearly"),
    ("wiup_temporal", "n_years_pre", "Jumlah tahun observasi sebelum iup_year (dalam jendela 2001-2025).",
     "count(tahun < iup_year), tahun ∈ [2001,2025]", None),
    ("wiup_temporal", "n_years_post", "Jumlah tahun observasi sejak iup_year (dalam jendela 2001-2025).",
     "count(tahun ≥ iup_year), tahun ∈ [2001,2025]", None),
    ("wiup_temporal", "rate_pre_ha_per_year", "Laju deforestasi rata-rata SEBELUM izin (ha/tahun).",
     "loss_pre_iup_ha / n_years_pre", None),
    ("wiup_temporal", "rate_post_ha_per_year", "Laju deforestasi rata-rata SETELAH izin (ha/tahun) — metrik utama analisis periode & Komparasi.",
     "loss_post_iup_ha / n_years_post", None),
    ("wiup_temporal", "ratio_post_pre", "Rasio laju pasca:pra-izin (>1 = akselerasi pasca-izin).",
     "rate_post_ha_per_year / rate_pre_ha_per_year (∞ jika pre=0 & post>0)", None),
    ("wiup_temporal", "verdict", "Kategori pola temporal per konsesi: accelerated_post_iup ('Dipercepat setelah izin', ratio>1,5), loss_only_after_iup ('Kerusakan hanya setelah izin', pre=0 & post>0), decelerated_post_iup ('Melambat setelah izin', ratio<0,67), stable ('stabil'), no_loss_either, no_iup_date_or_out_of_range.",
     "aturan ambang atas ratio_post_pre (lihat scripts/temporal_iup.py)", "scripts/temporal_iup.py"),

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
     "wiup_loss.total_loss_ha, threshold kanopi & sumber piksel SAMA PERSIS.",
     "Σ luas piksel hutan-2000 (kanopi≥30%) dgn lossyear 2001-2021",
     "Hansen lossyear × treecover2000 × poligon WIUP (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_tol2th_ha",
     "Kehilangan 2001-2021 yang piksel-nya juga menjadi sawit menurut Descals dkk. "
     "(2024), varian TOLERAN (UTAMA/patokan): tahun tanam sawit (YoP) boleh "
     "mendahului tahun kehilangan hingga 2 tahun — mengakomodasi RMSE deteksi "
     "tahun tanam Descals (2,02 th perkebunan industri / 4,89 th rakyat).",
     "Σ luas piksel dgn YoP ≥ tahun_loss − 2, dari subset loss_2001_2021_ha",
     "Descals dkk. (2024) tahun-tanam sawit × Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_jeda5th_ha",
     "Idem loss_sawit_tol2th_ha, varian PALING KETAT (BATAS BAWAH sensitivitas): tahun "
     "tanam (YoP) tak boleh mendahului tahun kehilangan sama sekali, dan maksimal 5 "
     "tahun sesudahnya — subset dari loss_sawit_tahunsama_ha (jeda5th ⊆ tahunsama ⊆ tol2th).",
     "Σ luas piksel dgn tahun_loss ≤ YoP ≤ tahun_loss + 5",
     "Descals dkk. (2024) × Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_tahunsama_ha",
     "Idem loss_sawit_tol2th_ha, varian TANPA TOLERANSI MUNDUR (TENGAH): tahun tanam "
     "(YoP) harus ≥ tahun kehilangan, tak boleh mendahului sama sekali (tanpa batas atas).",
     "Σ luas piksel dgn YoP ≥ tahun_loss",
     "Descals dkk. (2024) × Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_2022_2025_ha",
     "Kehilangan tutupan pohon 2022-2025 — TAK BISA diperiksa terhadap sawit sama "
     "sekali (Descals berhenti 2021); disimpan terpisah, BUKAN digabung diam-diam "
     "ke penyebut pangsa sawit (persen_sawit).",
     "Σ luas piksel hutan-2000 dgn lossyear 2022-2025", "Hansen lossyear (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "n_tile_hansen",
     "Jumlah tile Hansen (grid 10°×10°) yang disentuh konsesi ini (>1 bila konsesi "
     "lintas-tile — 16/825 konsesi begini; ditangani via clip-per-tile, bukan dilewati).",
     None, "scripts/attribution_sawit.py (_geo_common.pick_tile)"),

    # ── Task F15: silang dua sumbu pra/pasca-izin × sawit (jendela eksplisit) ──
    ("atribusi_sawit", "loss_sawit_pra_izin_ha",
     "Kehilangan yang teratribusi ke sawit (varian tol2th/UTAMA) pada jendela "
     "PRA-izin: tahun kalender 2001 s/d min(iup_year−1, 2021) — batas atas 2021 "
     "krn Descals berhenti di situ, jadi jendela ini bisa jadi PENUH 2001-2021 "
     "kalau iup_year > 2022 (lihat kolom bersih di wiup_master). NULL kalau "
     "iup_year konsesi ini NULL (bukan 0 — beda makna dgn 'sawit=0 tapi "
     "iup_year diketahui').",
     "Σ atribusi_sawit_yearly.loss_sawit_tol2th_ha utk tahun ∈ [2001, min(iup_year−1,2021)]",
     "atribusi_sawit_yearly × wiup_geoportal.iup_year (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_sawit_pasca_izin_2021_ha",
     "Kehilangan yang teratribusi ke sawit (varian tol2th/UTAMA) pada jendela "
     "PASCA-izin YANG BISA DIPERIKSA: tahun izin terbit (iup_year, inklusif) s/d "
     "2021 — BUKAN s/d 2025 (Descals berhenti 2021; sisa 2022-2025 tetap di "
     "loss_2022_2025_ha, tak masuk sini). NULL kalau iup_year NULL; 0 (bukan "
     "NULL) kalau iup_year > 2021 (jendela ini kosong, bukan tak diketahui).",
     "Σ atribusi_sawit_yearly.loss_sawit_tol2th_ha utk tahun ∈ [max(iup_year,2001), 2021]",
     "atribusi_sawit_yearly × wiup_geoportal.iup_year (scripts/attribution_sawit.py)"),
    ("atribusi_sawit", "loss_pasca_izin_2021_ha",
     "Total kehilangan tutupan pohon Hansen (BUKAN teratribusi sawit — jendela "
     "SAMA PERSIS dgn loss_sawit_pasca_izin_2021_ha, iup_year..2021) — PENYEBUT "
     "'bersih pasca-izin s/d 2021' (lihat wiup_master.loss_pasca_izin_2021_bersih_ha). "
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
     "1 jika loss_pre_iup_ha/(loss_pre_iup_ha+loss_post_iup_ha) > 0,5, else 0; NULL bila penyebut=0",
     "wiup_master.loss_pre_iup_ha, loss_post_iup_ha"),

    # ── periode_sawit: agregasi atribusi_sawit per periode kewenangan izin ──────
    ("periode_sawit", "periode", "Kode periode kewenangan izin (P1/P2/P3/Pra-2009).", None, "periode(iup_year)"),
    ("periode_sawit", "n_konsesi", "Jumlah konsesi periode yang punya data atribusi sawit (baris di atribusi_sawit).",
     "count(kode_wiup) dari atribusi_sawit dengan to_periode(iup_year)=periode", "atribusi_sawit × wiup_geoportal.iup_year"),
    ("periode_sawit", "loss_2001_2021_ha", "Total kehilangan tutupan pohon 2001-2021 (bisa diperiksa thd sawit) seluruh konsesi periode.",
     "Σ atribusi_sawit.loss_2001_2021_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_sawit_tol2th_ha",
     "Total kehilangan 2001-2021 periode yang teratribusi ke sawit, varian TOLERAN (UTAMA/patokan, YoP ≥ tahun_loss−2).",
     "Σ atribusi_sawit.loss_sawit_tol2th_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_sawit_jeda5th_ha",
     "Idem, varian PALING KETAT/batas bawah (tahun_loss ≤ YoP ≤ tahun_loss+5).",
     "Σ atribusi_sawit.loss_sawit_jeda5th_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_sawit_tahunsama_ha",
     "Idem, varian tanpa toleransi mundur/tengah (YoP ≥ tahun_loss).",
     "Σ atribusi_sawit.loss_sawit_tahunsama_ha per periode", "atribusi_sawit"),
    ("periode_sawit", "loss_bersih_ha",
     "Kehilangan 2001-2021 periode SETELAH dikurangi bagian teratribusi ke sawit "
     "(varian tol2th) — perkiraan kehilangan 'murni non-sawit' periode itu.",
     "loss_2001_2021_ha − loss_sawit_tol2th_ha", "kolom pada tabel ini"),
    ("periode_sawit", "persen_sawit",
     "Persen kehilangan periode yang teratribusi ke sawit (varian tol2th/UTAMA). "
     "PENYEBUT: loss_2001_2021_ha (kehilangan 2001-2021 yang bisa diperiksa thd "
     "sawit) — BUKAN luas konsesi, BUKAN hutan tahun 2000. NULL bila loss_2001_2021_ha periode itu = 0.",
     "100 · loss_sawit_tol2th_ha / loss_2001_2021_ha", "kolom pada tabel ini"),
    ("periode_sawit", "loss_2022_2025_ha",
     "Total kehilangan 2022-2025 periode — TAK TERPERIKSA thd sawit sama sekali "
     "(Descals berhenti 2021); disajikan terpisah, tidak masuk penyebut persen_sawit.",
     "Σ atribusi_sawit.loss_2022_2025_ha per periode", "atribusi_sawit"),

    # ── atribusi_sawit_yearly: pecahan PER TAHUN dari loss_sawit_tol2th_ha ──────
    # LAPISAN opsional (Task F1/FASE F) — dasar rumus "loss bersih dari sawit"
    # per tahun, dipakai periode_tahunan_aktif_bersih. Sparse spt wiup_loss_yearly
    # (tahun tanpa loss-sawit tak disimpan, tersirat 0 via COALESCE pemakainya).
    ("atribusi_sawit_yearly", "kode_wiup", "Kode unik WIUP (fk ke wiup_geoportal).", None, None),
    ("atribusi_sawit_yearly", "year", "Tahun kalender kehilangan (2001-2021 — dibatasi jendela Descals; 2022-2025 tak disimpan di sini sama sekali).",
     None, "band 'lossyear' Hansen GFC, dibatasi ≤2021"),
    ("atribusi_sawit_yearly", "loss_sawit_tol2th_ha",
     "Kehilangan tahun itu yang teratribusi ke sawit, varian TOLERAN/tol2th (UTAMA/patokan, "
     "YoP ≥ tahun_loss−2) — SUM per kode_wiup atas seluruh tahun HARUS = atribusi_sawit.loss_sawit_tol2th_ha "
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
    ("wiup_master", "total_loss_ha", "Total kehilangan tutupan pohon 2001-2025 di dalam poligon — dasar angka headline tesis.", None, "wiup_loss.total_loss_ha"),
    ("wiup_master", "loss_pct_of_polygon", "Persen luas poligon yang hilang tutupan pohonnya.", None, "wiup_loss.loss_pct_of_polygon"),
    ("wiup_master", "loss_pct_of_forest", "Persen tutupan pohon 2000 yang hilang — metrik utama tesis per konsesi.", None, "wiup_loss.loss_pct_of_forest"),
    ("wiup_master", "hansen_tiles", "Daftar tile Hansen yang overlap poligon konsesi.", None, "wiup_loss.tiles (alias hansen_tiles)"),
    ("wiup_master", "loss_pre_iup_ha", "Total loss (ha) jendela PRA-izin: 2001 s/d tahun sebelum izin terbit (iup_year−1) — BUKAN bagian dari total_loss_ha yang terjadi di bawah izin ini.", None, "wiup_temporal.loss_pre_iup_ha"),
    ("wiup_master", "loss_post_iup_ha", "Total loss (ha) jendela PASCA-izin: tahun izin terbit (iup_year, inklusif) s/d 2025 — beda dgn total_loss_ha yang mencakup 2001–2025 penuh termasuk pra-izin.", None, "wiup_temporal.loss_post_iup_ha"),
    ("wiup_master", "rate_pre_ha_per_year", "Laju deforestasi rata-rata sebelum izin (ha/tahun).", None, "wiup_temporal.rate_pre_ha_per_year"),
    ("wiup_master", "rate_post_ha_per_year", "Laju deforestasi rata-rata setelah izin (ha/tahun) — metrik utama Komparasi & peta.", None, "wiup_temporal.rate_post_ha_per_year"),
    ("wiup_master", "ratio_post_pre", "Rasio laju pasca:pra-izin (>1 = akselerasi pasca-izin).", None, "wiup_temporal.ratio_post_pre"),
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
    ("wiup_master", "loss_sawit_tol2th_ha", "Kehilangan 2001-2021 konsesi ini yang teratribusi ke sawit, varian TOLERAN (UTAMA/patokan, YoP ≥ tahun_loss−2).",
     None, "atribusi_sawit.loss_sawit_tol2th_ha"),
    ("wiup_master", "loss_sawit_jeda5th_ha", "Idem, varian PALING KETAT/batas bawah (tahun_loss ≤ YoP ≤ tahun_loss+5).",
     None, "atribusi_sawit.loss_sawit_jeda5th_ha"),
    ("wiup_master", "loss_sawit_tahunsama_ha", "Idem, varian tanpa toleransi mundur/tengah (YoP ≥ tahun_loss).",
     None, "atribusi_sawit.loss_sawit_tahunsama_ha"),
    ("wiup_master", "loss_2022_2025_ha", "Kehilangan 2022-2025 konsesi ini — tak terperiksa thd sawit sama sekali (Descals berhenti 2021).",
     None, "atribusi_sawit.loss_2022_2025_ha"),
    ("wiup_master", "loss_bersih_ha", "Kehilangan 2001-2021 konsesi ini dikurangi bagian teratribusi ke sawit (varian tol2th).",
     "loss_2001_2021_ha − loss_sawit_tol2th_ha", "view wiup_master (dihitung di CREATE VIEW)"),
    ("wiup_master", "persen_sawit",
     "Persen kehilangan konsesi ini yang teratribusi ke sawit (varian tol2th/UTAMA). "
     "PENYEBUT: loss_2001_2021_ha konsesi ini — BUKAN luas konsesi (luas_sk), BUKAN "
     "hutan 2000 (forest_2000_ha). NULL bila loss_2001_2021_ha=0.",
     "100 · loss_sawit_tol2th_ha / loss_2001_2021_ha", "view wiup_master (dihitung di CREATE VIEW)"),

    # ── Task F15: silang dua sumbu pra/pasca-izin × sawit ───────────────────────
    ("wiup_master", "loss_sawit_pra_izin_ha",
     "Alias atribusi_sawit.loss_sawit_pra_izin_ha — kehilangan teratribusi sawit "
     "(varian tol2th) pd jendela PRA-izin: 2001 s/d min(iup_year−1, 2021).",
     None, "atribusi_sawit.loss_sawit_pra_izin_ha"),
    ("wiup_master", "loss_sawit_pasca_izin_2021_ha",
     "Alias atribusi_sawit.loss_sawit_pasca_izin_2021_ha — kehilangan teratribusi "
     "sawit (varian tol2th) pd jendela PASCA-izin yg bisa diperiksa: iup_year..2021 "
     "(BUKAN s/d 2025 — Descals berhenti 2021).",
     None, "atribusi_sawit.loss_sawit_pasca_izin_2021_ha"),
    ("wiup_master", "loss_pasca_izin_2021_ha",
     "Alias atribusi_sawit.loss_pasca_izin_2021_ha — total kehilangan Hansen "
     "(BUKAN teratribusi sawit) pd jendela iup_year..2021, PENYEBUT kolom bersih "
     "di bawah.",
     None, "atribusi_sawit.loss_pasca_izin_2021_ha"),
    ("wiup_master", "loss_pra_izin_bersih_ha",
     "Kehilangan jendela PRA-izin (loss_pre_iup_ha, F14: 2001 s/d iup_year−1, "
     "TANPA dipotong 2021) dikurangi bagian teratribusi sawit "
     "(loss_sawit_pra_izin_ha, dipotong 2021). NULL bila iup_year > 2022 — "
     "jendela pra melewati batas Descals (2021) sehingga TAK SEPENUHNYA "
     "terperiksa thd sawit (bukan 0% sawit, melainkan tak diketahui); NULL "
     "juga bila iup_year konsesi ini NULL.",
     "loss_pre_iup_ha − loss_sawit_pra_izin_ha (NULL bila iup_year>2022 atau iup_year NULL)",
     "view wiup_master (dihitung di CREATE VIEW; wiup_temporal.loss_pre_iup_ha × atribusi_sawit.loss_sawit_pra_izin_ha)"),
    ("wiup_master", "loss_pasca_izin_2021_bersih_ha",
     "Kehilangan jendela PASCA-izin s/d 2021 (loss_pasca_izin_2021_ha, Hansen, "
     "BUKAN loss_post_iup_ha F14 yang s/d 2025 penuh) dikurangi bagian "
     "teratribusi sawit (loss_sawit_pasca_izin_2021_ha) — keduanya jendela yang "
     "SAMA (iup_year..2021), jadi TAK butuh syarat iup_year≤2022 seperti sisi "
     "pra. Sisa 2022-2025 tetap tak terperiksa, lihat loss_2022_2025_ha.",
     "loss_pasca_izin_2021_ha − loss_sawit_pasca_izin_2021_ha",
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
    ("kepadatan_penduduk", "kode_kabkot", "Kode kabupaten/kota (BPS), kunci utama tabel.",
     None, "BPS"),
    ("kepadatan_penduduk", "provinsi", "Nama provinsi kabupaten/kota.", None, "BPS"),
    ("kepadatan_penduduk", "kabupaten", "Nama kabupaten/kota (versi BPS).", None, "BPS"),
    ("kepadatan_penduduk", "kab_normalized", "Nama kabupaten/kota versi baku — kanonik dipakai join lintas-tabel (wiup_geoportal).",
     None, "normalisasi nama BPS saat ingest"),
    ("kepadatan_penduduk", "d2015", "Kepadatan penduduk tahun 2015 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2016", "Kepadatan penduduk tahun 2016 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2017", "Kepadatan penduduk tahun 2017 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2018", "Kepadatan penduduk tahun 2018 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2019", "Kepadatan penduduk tahun 2019 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2020", "Kepadatan penduduk tahun 2020 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2021", "Kepadatan penduduk tahun 2021 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2022", "Kepadatan penduduk tahun 2022 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2023", "Kepadatan penduduk tahun 2023 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "d2024", "Kepadatan penduduk tahun 2024 (lihat kolom 'satuan').", None, "BPS - Kepadatan Penduduk per Kabupaten/Kota 2015-2024"),
    ("kepadatan_penduduk", "satuan", "Satuan nilai kolom d2015..d2024 (jiwa/km²).", None, "BPS"),
    ("kepadatan_penduduk", "sumber", "Sitasi sumber data baris ini (nama publikasi BPS).", None, "BPS"),

    # ── analysis_meta: PROVENANCE tiap tabel analisis (dokumentasi, bukan data) ─
    ("analysis_meta", "nama_tabel", "Nama tabel/view yang didokumentasikan provenance-nya (kunci utama).", None, None),
    ("analysis_meta", "deskripsi", "Ringkasan 1 kalimat isi tabel tsb.", None, None),
    ("analysis_meta", "sumber", "Berkas/tabel input yang jadi bahan tabel tsb.", None, None),
    ("analysis_meta", "metode", "Cara tabel tsb dihitung (rumus/agregasi) — sumber utama isi COLUMN_META di halaman ini.",
     None, None),
    ("analysis_meta", "script", "Nama skrip Python yang membangun tabel tsb.", None, None),

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


def _bersih_column_meta_rows():
    rows = []
    for tabel, kolom, deskripsi, rumus, sumber in COLUMN_META:
        if tabel not in _BERSIH_TABLES:
            continue
        sumber_bersih = ("atribusi_sawit(_yearly) × " + sumber) if sumber else "atribusi_sawit(_yearly)"
        rows.append((tabel + BERSIH_SUFFIX, kolom, _BERSIH_CATATAN + deskripsi, rumus, sumber_bersih))
    return rows


COLUMN_META = COLUMN_META + _bersih_column_meta_rows()


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
        """SELECT g.iup_year, a.loss_2001_2021_ha, a.loss_sawit_tol2th_ha,
                  a.loss_sawit_jeda5th_ha, a.loss_sawit_tahunsama_ha, a.loss_2022_2025_ha
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
            loss_2001_2021_ha REAL, loss_sawit_tol2th_ha REAL,
            loss_sawit_jeda5th_ha REAL, loss_sawit_tahunsama_ha REAL,
            loss_bersih_ha REAL, persen_sawit REAL, loss_2022_2025_ha REAL)"""
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


def build_periode_ringkasan(con, table_name, by_per, forest_of, loss_of):
    """Bangun `table_name` (periode_ringkasan / periode_ringkasan_bersih).

    `loss_of`: dict kode_wiup -> loss_ha dipakai SEBAGAI GANTI wiup_loss.total_loss_ha
    (parameter Task F1 — DRY, jangan copy-paste builder tabel asli vs bersih).
    Kalau kode_wiup TAK ADA di `loss_of`, dianggap "tak ada data" (DIBUANG dari
    rata-rata/Pearson, PERSIS spt x[5] is None di versi lama) — beda dari
    "ada tapi 0.0" (yg tetap terhitung). Pemanggil (main()) yg memutuskan mana
    dari dua makna ini yg dipakai per konsesi lewat isi `loss_of`.
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT PRIMARY KEY, rentang_tahun TEXT, n INTEGER,
            luas_total_ha REAL, luas_mean_ha REAL, luas_median_ha REAL,
            loss_total_ha REAL, polygon_total_ha REAL, forest2000_total_ha REAL,
            pct_poligon REAL,
            rate_post_mean REAL, rate_post_median REAL, pct_akselerasi REAL,
            r_luas_loss REAL, r_luas_ratepost REAL,
            komposisi_otoritas TEXT)"""
    )
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
        con.execute(
            f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r, RENTANG[r], len(rows),
             round(sum(luas), 2),
             round(sum(luas) / len(luas), 2) if luas else None,
             round(statistics.median(luas), 2) if luas else None,
             round(sum(loss), 2), round(sum(poly), 2), round(sum(forest), 2),
             round(100 * sum(loss) / sum(poly), 2) if poly and sum(poly) else None,
             round(sum(ratep) / len(ratep), 2) if ratep else None,
             round(statistics.median(ratep), 2) if ratep else None,
             round(100 * accel / len(rows), 2) if rows else None,
             round(r_ll, 3) if r_ll is not None else None,
             round(r_lr, 3) if r_lr is not None else None,
             comp),
        )


def build_periode_tahunan_aktif(con, table_name, by_per, loss_lookup, forest_of, year_max):
    """Bangun `table_name` (periode_tahunan_aktif / periode_tahunan_aktif_bersih).

    `loss_lookup`: dict (kode_wiup, year) -> loss_ha dipakai SEBAGAI GANTI
    wiup_loss_yearly mentah (parameter Task F1). `year_max`: batas atas deret
    tahun (2025 utk tabel asli, 2021 utk bersih — Descals berhenti 2021,
    tahun 2022-2025 DIBUANG SELURUHNYA dari varian bersih, bukan cuma
    sawit-nya yg diabaikan). Konsesi ber-iup_year > year_max tak menyumbang
    baris apa pun di varian bersih (start > year_max -> range kosong).
    """
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT, year INTEGER, loss_ha REAL, n_konsesi_aktif INTEGER,
            luas_aktif_ha REAL, forest_aktif_ha REAL, loss_kumulatif_ha REAL,
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


def build_periode_komoditas(con, table_name, by_per, komod_of, loss_of):
    """Bangun `table_name` (periode_komoditas / periode_komoditas_bersih).

    `loss_of`: idem build_periode_ringkasan (parameter Task F1).
    """
    def kgroup(komoditas):
        return "BATUBARA" if (komoditas or "").upper().startswith("BATUBARA") else "MINERAL LOGAM"

    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        f"""CREATE TABLE {table_name} (
            periode TEXT, grup_komoditas TEXT, n INTEGER,
            luas_total_ha REAL, luas_median_ha REAL,
            loss_total_ha REAL, pct_poligon REAL,
            rate_post_median REAL, pct_akselerasi REAL,
            PRIMARY KEY (periode, grup_komoditas))"""
    )
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
            con.execute(
                f"INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?)",
                (r, gname, len(rows),
                 round(sum(luas), 2),
                 round(statistics.median(luas), 2) if luas else None,
                 round(sum(loss), 2),
                 round(100 * sum(loss) / sum(poly), 2) if poly and sum(poly) else None,
                 round(statistics.median(ratep), 2) if ratep else None,
                 round(100 * accel / len(rows), 2) if rows else None),
            )


def build_periode_signifikansi(con, table_name, by_per, loss_of):
    """Bangun `table_name` (periode_signifikansi / periode_signifikansi_bersih).

    HANYA metrik total_loss_ha yg memakai `loss_of` (parameter Task F1);
    rate_post_ha_per_year & luas_sk tak bergantung sawit — identik antara
    tabel asli & bersih (dites tegas di test_build_periode_tables.py).
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
            "rate_post_ha_per_year": lambda x: x[6],
            "total_loss_ha": lambda x: loss_of.get(x[0]),
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
                  l.polygon_area_ha, l.total_loss_ha,
                  t.rate_post_ha_per_year, t.verdict
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

    # loss_of: dict kode_wiup -> total_loss_ha, sumber ASLI (identik x[5] di
    # tuple `konsesi`) — parameter Task F1 dilewatkan ke builder generik supaya
    # tabel ASLI (loss_of=loss_of_asli) & _bersih (loss_of=loss_of_bersih di
    # bawah, dibangun HANYA bila atribusi_sawit ada+berisi) berbagi 1 builder.
    loss_of_asli = {x[0]: x[5] for x in konsesi}

    # ── 1. periode_ringkasan ───────────────────────────────────────────────────
    build_periode_ringkasan(con, "periode_ringkasan", by_per, forest_of, loss_of_asli)

    # ── 2. periode_deforestasi_tahunan ──────────────────────────────────────────
    # loss per (periode, tahun kalender) dari wiup_loss_yearly.
    yearly = con.execute("SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly").fetchall()
    # Lookup (kode, year) → loss; dipakai tabel aktif (2b) & event-study (4).
    loss_lookup = {(kode, y): (loss or 0) for kode, y, loss in yearly}
    tah = {r: {y: 0.0 for y in range(YEAR_MIN, YEAR_MAX + 1)} for r in PERIODES}
    for kode, y, loss in yearly:
        r = to_periode(iup_of.get(kode))
        if r is None or y < YEAR_MIN or y > YEAR_MAX:
            continue
        tah[r][y] += loss or 0
    con.execute("DROP TABLE IF EXISTS periode_deforestasi_tahunan")
    con.execute(
        """CREATE TABLE periode_deforestasi_tahunan (
            periode TEXT, year INTEGER, loss_ha REAL, n_konsesi INTEGER,
            PRIMARY KEY (periode, year))"""
    )
    for r in PERIODES:
        for y in range(YEAR_MIN, YEAR_MAX + 1):
            con.execute("INSERT INTO periode_deforestasi_tahunan VALUES (?,?,?,?)",
                        (r, y, round(tah[r][y], 2), len(by_per[r])))

    # ── 2b. periode_tahunan_aktif (akuntansi IZIN-AKTIF, bukan kohort penuh) ───
    # Tiap konsesi baru dihitung sejak iup_year-nya SENDIRI terbit — loss
    # sebelum izin ada tidak pernah masuk. Garis kohort "menebal" seiring izin
    # bertambah (mis. R2@2015 hanya 21 izin ≈ 3,6 rb ha; kohort penuh 27,7 rb).
    # Kolom stok per (periode, tahun) — semua atas izin ber-iup_year <= tahun:
    #   n_konsesi_aktif   : jumlah izin terbit
    #   luas_aktif_ha     : Σ luas_sk izin aktif
    #   forest_aktif_ha   : Σ hutan-2000 di dalam izin aktif
    #   loss_ha           : loss tahun itu di izin aktif (flow)
    #   loss_kumulatif_ha : akumulasi loss pasca-izin s/d tahun itu
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
            luas_aktif_ha REAL, forest_aktif_ha REAL, loss_kumulatif_ha REAL,
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

    # ── 4. periode_eventstudy (waktu relatif ke tahun izin) ─────────────────────
    # rel = tahun kalender − iup_year. n_konsesi = konsesi periode yg "teramati" pada
    # rel itu (iup_year+rel ∈ [2001,2025]). mean = sum_loss / n_konsesi.
    REL_MIN, REL_MAX = -15, 15
    ev_sum = {r: {} for r in PERIODES}   # rel -> sum loss
    ev_n = {r: {} for r in PERIODES}     # rel -> jml konsesi teramati
    for r in PERIODES:
        for x in by_per[r]:
            kode, iy = x[0], x[1]
            if iy is None or iy < YEAR_MIN or iy > YEAR_MAX:
                continue  # butuh iup_year valid utk alignment (mis. buang iup 2026)
            for rel in range(REL_MIN, REL_MAX + 1):
                cal = iy + rel
                if cal < YEAR_MIN or cal > YEAR_MAX:
                    continue
                ev_n[r][rel] = ev_n[r].get(rel, 0) + 1
                ev_sum[r][rel] = ev_sum[r].get(rel, 0.0) + loss_lookup.get((kode, cal), 0)
    con.execute("DROP TABLE IF EXISTS periode_eventstudy")
    con.execute(
        """CREATE TABLE periode_eventstudy (
            periode TEXT, rel_year INTEGER, n_konsesi INTEGER,
            sum_loss_ha REAL, mean_loss_ha REAL,
            PRIMARY KEY (periode, rel_year))"""
    )
    for r in PERIODES:
        for rel in sorted(ev_n[r]):
            n = ev_n[r][rel]
            s = ev_sum[r].get(rel, 0.0)
            con.execute("INSERT INTO periode_eventstudy VALUES (?,?,?,?,?)",
                        (r, rel, n, round(s, 2), round(s / n, 4) if n else None))

    # ── 5. periode_komoditas (kontrol komoditas: batubara vs mineral logam) ────
    komod_of = dict(con.execute("SELECT kode_wiup, komoditas FROM wiup_geoportal"))
    build_periode_komoditas(con, "periode_komoditas", by_per, komod_of, loss_of_asli)

    # ── 6. periode_ukuran (distribusi ukuran → bukti "polarisasi") ─────────────
    # Persentil luas, share top-10% terbesar, dan koefisien Gini per periode.
    def gini(vals):
        xs = sorted(v for v in vals if v is not None and v >= 0)
        n = len(xs)
        tot = sum(xs)
        if n < 2 or tot == 0:
            return None
        cum = sum((i + 1) * x for i, x in enumerate(xs))
        return (2 * cum) / (n * tot) - (n + 1) / n

    def pct(vals, p):
        xs = sorted(vals)
        if not xs:
            return None
        k = (len(xs) - 1) * p / 100
        lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - int(k))

    con.execute("DROP TABLE IF EXISTS periode_ukuran")
    con.execute(
        """CREATE TABLE periode_ukuran (
            periode TEXT PRIMARY KEY, n INTEGER,
            p10 REAL, p25 REAL, p50 REAL, p75 REAL, p90 REAL,
            mean_ha REAL, share_top10pct REAL, gini REAL)"""
    )
    for r in PERIODES:
        luas = [x[3] for x in by_per[r] if x[3] is not None]
        if not luas:
            continue
        srt = sorted(luas, reverse=True)
        ntop = max(1, round(len(srt) * 0.10))
        share_top = 100 * sum(srt[:ntop]) / sum(srt) if sum(srt) else None
        g = gini(luas)
        con.execute(
            "INSERT INTO periode_ukuran VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r, len(luas),
             round(pct(luas, 10), 1), round(pct(luas, 25), 1), round(pct(luas, 50), 1),
             round(pct(luas, 75), 1), round(pct(luas, 90), 1),
             round(sum(luas) / len(luas), 1),
             round(share_top, 1) if share_top is not None else None,
             round(g, 3) if g is not None else None),
        )

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
                "SELECT kode_wiup, loss_2001_2021_ha, loss_sawit_tol2th_ha FROM atribusi_sawit")
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
        build_periode_ringkasan(con, "periode_ringkasan" + BERSIH_SUFFIX, by_per,
                                 forest_of, loss_of_bersih)
        build_periode_tahunan_aktif(con, "periode_tahunan_aktif" + BERSIH_SUFFIX, by_per,
                                     loss_lookup_bersih, forest_of, YEAR_MAX_BERSIH)
        build_periode_komoditas(con, "periode_komoditas" + BERSIH_SUFFIX, by_per,
                                 komod_of, loss_of_bersih)
        build_periode_signifikansi(con, "periode_signifikansi" + BERSIH_SUFFIX, by_per,
                                    loss_of_bersih)
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

    # ── 8. analysis_meta (PROVENANCE) ─────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS analysis_meta")
    con.execute(
        """CREATE TABLE analysis_meta (
            nama_tabel TEXT PRIMARY KEY, deskripsi TEXT, sumber TEXT, metode TEXT, script TEXT)"""
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
         "total_loss_ha, forest_2000_ha, polygon_area_ha, loss_pct_of_forest per kode_wiup.",
         "scripts/build_combined_db.py"),
        ("wiup_loss_yearly",
         "Kehilangan tutupan pohon per konsesi PER TAHUN 2001-2025 (long format).",
         "data/analysis/batch_KALIMANTAN_t30_wide.csv (kolom lossyear Hansen)",
         "(kode_wiup, year, loss_ha). Sumber grafik loss/tahun & pivot properti peta.",
         "scripts/build_combined_db.py"),
        ("wiup_temporal",
         "Laju deforestasi pra- vs pasca-terbit izin per konsesi.",
         "data/analysis/temporal_iup_analysis.csv (dari scripts/temporal_iup.py)",
         "rate_pre/rate_post_ha_per_year, verdict, temporal_verdict per kode_wiup.",
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
         "Kepadatan penduduk BPS per kabupaten/kota Kalimantan, 2015-2024.",
         "data/kepadatan_penduduk.csv (BPS — Kepadatan Penduduk per Kabupaten/Kota 2015-2024)",
         "Ingest 1:1 dari CSV committed (bukan lagi ditempel manual di luar pipeline — lihat "
         "docstring step_kepadatan()); kab_normalized dinormalkan saat ingest agar konsisten "
         "join ke wiup_geoportal.kab_normalized.",
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
         "tanggal_berakhir, loss_pre_iup_ha, loss_post_iup_ha)",
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
         "Σ tiap kolom atribusi_sawit per periode; loss_bersih_ha=loss_2001_2021_ha− "
         "loss_sawit_tol2th_ha; persen_sawit=100·loss_sawit_tol2th_ha/loss_2001_2021_ha "
         "(penyebut = kehilangan 2001-2021, BUKAN luas konsesi, BUKAN hutan 2000). "
         "Dibangun HANYA bila atribusi_sawit ada & berisi (tabel_ada_berisi()); tak "
         "didaftarkan di analysis_meta bila dilewati.",
         "scripts/build_periode_tables.py"),
        ("periode_ringkasan",
         "Ringkasan per periode kewenangan izin (3 periode + Pra-2009).",
         "wiup_geoportal(iup_year,luas_sk,pejabat) + wiup_loss(polygon_area_ha,total_loss_ha) + wiup_temporal(verdict,rate_post)",
         "Group by periode(iup_year). luas=Σluas_sk & median; loss=Σtotal_loss_ha; "
         "pct_poligon=100·Σloss/Σpolygon; pct_akselerasi=100·count(verdict∈{accelerated_post_iup,loss_only_after_iup})/n; "
         "r=Pearson(luas_sk vs total_loss_ha / rate_post).",
         "scripts/build_periode_tables.py"),
        ("periode_deforestasi_tahunan",
         "Loss (ha) per periode per tahun kalender 2001-2025 (deskriptif kohort penuh; slope kini dari periode_tahunan_aktif).",
         "wiup_loss_yearly(year,loss_ha) × periode(iup_year)",
         "Σ loss_ha per (periode, year). n_konsesi=jml konsesi di periode.",
         "scripts/build_periode_tables.py"),
        ("periode_tahunan_aktif",
         "Deret stok IZIN-AKTIF per periode-tahun: tiap konsesi dihitung sejak "
         "iup_year-nya sendiri (pra-izin tak pernah masuk).",
         "wiup_loss_yearly × wiup_geoportal(iup_year, luas_sk) × wiup_loss(forest_2000_ha)",
         "Atas izin ber-iup_year <= tahun: n_konsesi_aktif (jumlah), luas_aktif_ha "
         "(Σ luas_sk), forest_aktif_ha (Σ hutan-2000), loss_ha (loss tahun itu), "
         "loss_kumulatif_ha (akumulasi loss pasca-izin). BASIS periode_slope "
         "(since-permit); beda dgn periode_deforestasi_tahunan (kohort penuh 2001-2025, deskriptif).",
         "scripts/build_periode_tables.py"),
        ("penerbit_tahunan_aktif",
         "Deret stok izin-aktif per PENERBIT (Bupati/Gubernur/Menteri) per tahun.",
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
        ("periode_eventstudy",
         "Event-study: rata-rata loss per waktu-relatif-ke-izin (rel=tahun−iup_year).",
         "wiup_loss_yearly × wiup_geoportal(iup_year)",
         "Utk tiap konsesi (iup_year 2001-2025), sejajarkan rel=cal−iup_year∈[-15,15] "
         "bila cal∈[2001,2025]. sum_loss & n_konsesi teramati per (periode,rel); mean=sum/n.",
         "scripts/build_periode_tables.py"),
        ("periode_komoditas",
         "Kontrol komoditas: periode × grup (BATUBARA vs MINERAL LOGAM).",
         "wiup_geoportal(komoditas) + wiup_loss + wiup_temporal",
         "grup=BATUBARA jika komoditas diawali 'BATUBARA', selainnya MINERAL LOGAM. "
         "Metrik sama dgn periode_ringkasan (n, luas, loss, %poligon, %aksel, rate median).",
         "scripts/build_periode_tables.py"),
        ("periode_ukuran",
         "Distribusi ukuran konsesi per periode → bukti klaim polarisasi.",
         "wiup_geoportal(luas_sk)",
         "Persentil p10/p25/p50/p75/p90 (interpolasi linear), mean, share_top10pct "
         "(=100·Σ luas 10% konsesi terbesar ÷ Σ luas), gini (0=merata,1=timpang).",
         "scripts/build_periode_tables.py"),
        ("periode_signifikansi",
         "Uji beda antar periode R1/R2/R3 (non-parametrik, distribusi skew).",
         "wiup_geoportal + wiup_loss + wiup_temporal (per konsesi)",
         "Kruskal-Wallis lintas P1|P2|P3 per metrik (rate_post, total_loss, luas_sk); "
         "pairwise Mann-Whitney U two-sided, p dikoreksi Holm (p_adjusted); "
         "signifikan_005 = p_adjusted<0,05. Pra-2009 dikecualikan (catatan kaki). "
         "CAVEAT: rate_post = laju loss/tahun sejak izin, jadi jendela pasca-izin "
         "timpang antar-periode (P1 ~12-17 th vs P3 ~1-6 th) — uji atas rate_post tak "
         "sepenuhnya apple-to-apple; total_loss & luas_sk tak terpengaruh.",
         "scripts/build_periode_tables.py"),
        ("atribusi_sawit_yearly",
         "Pecahan PER TAHUN dari atribusi_sawit.loss_sawit_tol2th_ha (varian tol2th/UTAMA) — "
         "LAPISAN opsional, dasar rumus 'loss bersih dari sawit' per (periode,tahun).",
         "Hansen GFC v1.13 (lossyear) × Descals dkk. (2024) tahun-tanam sawit × poligon wiup_geoportal, "
         "jendela 2001-2021 (Descals berhenti 2021)",
         "Σ luas piksel dgn YoP ≥ tahun_loss−2, dikelompokkan (kode_wiup, tahun_loss). Sparse "
         "(tahun tanpa loss-sawit tak disimpan). Konsistensi SUM(per konsesi) vs "
         "atribusi_sawit.loss_sawit_tol2th_ha (window) diverifikasi (ambang 0,5 ha) SEBELUM "
         "atribusi_sawit MAUPUN tabel ini ditulis — galat membatalkan seluruh run. Dibangun "
         "HANYA bila ada & berisi (guard periode_*_bersih di bawah).",
         "scripts/attribution_sawit.py"),
        ("periode_ringkasan" + BERSIH_SUFFIX,
         "Varian BERSIH periode_ringkasan: loss dipotong perkiraan konversi sawit, jendela 2001-2021.",
         "periode_ringkasan (skema identik) + atribusi_sawit(_yearly)",
         "Sama dgn periode_ringkasan, TAPI loss_total_ha per konsesi = loss_2001_2021_ha − "
         "loss_sawit_tol2th_ha (atribusi_sawit, varian tol2th/UTAMA); konsesi tanpa baris "
         "atribusi_sawit dianggap sawit=0 (tetap ikut, bukan dibuang). Tahun 2022-2025 DI LUAR "
         "cakupan varian ini (Descals berhenti 2021). Kolom tak-terkait loss (rate_post, "
         "pct_akselerasi, komposisi_otoritas, dst.) IDENTIK dgn tabel asli. Dibangun HANYA bila "
         "atribusi_sawit_yearly ada & berisi (tabel_ada_berisi()).",
         "scripts/build_periode_tables.py"),
        ("periode_tahunan_aktif" + BERSIH_SUFFIX,
         "Varian BERSIH periode_tahunan_aktif: deret since-permit dgn loss dipotong sawit, DIBATASI tahun ≤2021.",
         "periode_tahunan_aktif (skema identik) + atribusi_sawit_yearly",
         "Sama dgn periode_tahunan_aktif, TAPI loss_ha tahun itu = wiup_loss_yearly.loss_ha − "
         "atribusi_sawit_yearly.loss_sawit_tol2th_ha (COALESCE 0 bila tak ada baris), diklem "
         "≥0. Deret BERHENTI di tahun 2021 (bukan 2025) — Descals tak bisa memeriksa 2022-2025 "
         "sama sekali, jadi tahun itu DIBUANG SELURUHNYA (bukan cuma sawitnya diabaikan). "
         "Dibangun HANYA bila atribusi_sawit_yearly ada & berisi.",
         "scripts/build_periode_tables.py"),
        ("periode_komoditas" + BERSIH_SUFFIX,
         "Varian BERSIH periode_komoditas: kontrol komoditas dgn loss dipotong sawit, jendela 2001-2021.",
         "periode_komoditas (skema identik) + atribusi_sawit",
         "Sama dgn periode_komoditas, TAPI loss_total_ha = loss_2001_2021_ha − loss_sawit_tol2th_ha "
         "per konsesi (idem periode_ringkasan_bersih). Dibangun HANYA bila atribusi_sawit_yearly "
         "ada & berisi.",
         "scripts/build_periode_tables.py"),
        ("periode_signifikansi" + BERSIH_SUFFIX,
         "Varian BERSIH periode_signifikansi: uji beda antar-periode dgn metrik total_loss_ha dipotong sawit.",
         "periode_signifikansi (skema identik) + atribusi_sawit",
         "Sama dgn periode_signifikansi, TAPI metrik total_loss_ha memakai loss_2001_2021_ha − "
         "loss_sawit_tol2th_ha per konsesi; metrik rate_post_ha_per_year & luas_sk TAK berubah "
         "(tak bergantung sawit) — nilainya identik dgn tabel asli. Dibangun HANYA bila "
         "atribusi_sawit_yearly ada & berisi.",
         "scripts/build_periode_tables.py"),
        ("column_meta",
         "Kamus kolom: arti + rumus + sumber tiap kolom (untuk halaman Database).",
         "ditulis manual di build_periode_tables.py (COLUMN_META), divalidasi anti-yatim",
         "1 baris/kolom terdokumentasi; kolom turunan diisi rumus+sumber, kolom mentah cukup deskripsi.",
         "scripts/build_periode_tables.py"),
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
    con.executemany("INSERT INTO analysis_meta VALUES (?,?,?,?,?)",
                    existing_meta_rows(meta, existing))

    con.commit()
    # DB dilayani read-only tanpa dir writable → mode DELETE (bukan WAL).
    con.execute("PRAGMA journal_mode=DELETE")
    con.commit()

    # ── Ringkas ke stdout ─────────────────────────────────────────────────────
    print("OK — tabel dibuat:")
    for r in PERIODES:
        row = con.execute("SELECT n, loss_total_ha, pct_akselerasi, r_luas_loss FROM periode_ringkasan WHERE periode=?", (r,)).fetchone()
        sl = con.execute("SELECT slope_ha_per_year, peak_year FROM periode_slope WHERE periode=?", (r,)).fetchone()
        print(f"  {r:8} n={row[0]:>3} loss={row[1]:>10.0f} %aksel={row[2]:>5.1f} r={row[3]:.3f} "
              f"slope={sl[0]:>7.1f} ha/th puncak={sl[1]}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
