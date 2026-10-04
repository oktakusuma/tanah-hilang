#!/usr/bin/env python3
"""Langkah 10 — verifikasi tanah-hilang.db (SKEMA.md §10). Wajib PASS sebelum deploy.

Dokumentasi 4-unsur (Konvensi #4):
(a) APA: asersi bernama atas DB hasil pipeline v3 — (1) skema persis SKEMA.md (daftar eksplisit
    di lib/w4_skema.py), (2) analysis_meta & column_meta 100% dua arah, (3) Σ hansen_tahunan =
    hilang_2001_2024_ha per konsesi, (4) pct_hutan_2000 konsisten, (5) izin_laju identitas
    pra/pasca vs hansen_tahunan, (6) hash geometri MapBiomas = konsesi (= hitung ulang),
    (7) Σ transisi = Σ mapbiomas_tahunan tanpa kelas 27 (kedua sisi, tiap label, per konsesi),
    (8) transisi_pasangan 276 pasangan, (9) umur_izin n_konsesi > 0, (10) keyakinan_ringkas
    lo ≤ harapan ≤ hi, (11) dashboard-stats.json = DB (dihitung ulang dgn fungsi yang sama
    seperti 09_sajikan.py), (12) paritas arsip (angka jangkar §2, §4, §5, §6, §7) bila
    --arsip diberikan. Tambahan: kunci `bangun` wajib, tabel `sumber` terisi, integritas rujukan.
    Tiga beda v3 vs arsip yang SUDAH dijelaskan dan karenanya bukan FAIL: (i) selang bootstrap
    (urutan iterasi konsesi), (ii) kohort `umur-10_+0` (jendela dijepit 2001–2024), (iii) bendera
    `pra_izin_dominan` + AUC model RS (jendela pasca 2024 vs 2025) — masing-masing punya jangkar
    v3 sendiri, jadi drift baru tetap ketahuan.
(b) CARA PAKAI:
      python pipeline/10_verifikasi.py --db data/tanah-hilang.db --himpunan minerba \\
          --stats webapp/src/generated/dashboard-stats.json \\
          --arsip data/arsip/kalimantan.db --arsip-mapbiomas data/arsip/mapbiomas.db
      python pipeline/10_verifikasi.py --db data/tanah-hilang-lengkap.db --himpunan lengkap --stats …
    Path arsip yang tak ada dicoba juga di data/arsip/<nama> dan data/<nama> (sebelum/sesudah git mv).
(c) APA YANG DITAMPILKAN: satu baris `[PASS|WARN|FAIL] nama — pesan` per pemeriksaan + RINGKASAN.
    Exit 0 = tanpa FAIL; 1 = ada FAIL; 2 = prasyarat absen (DB/tabel inti tak ada).
(d) REPRODUKSI: DB dibuka baca-saja (mode=ro); semua asersi = SQL murni / fungsi lib yang bisa
    dijalankan ulang manual. Toleransi: 0,01 ha per baris; 0,5 ha agregat; 0,01 poin persen;
    1e-4 untuk laju bahaya (pecahan/tahun).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib import w4_skema as S  # noqa: E402
from pipeline.lib.db import AKAR, HIMPUNAN, baca_bangun, gagal, hash_geometri, wajib_tabel  # noqa: E402
from pipeline.lib.meta import INFRA, cakupan_dua_arah  # noqa: E402
from pipeline.lib.w4_sajikan import LABEL_SANKEY, blok_lengkap, stats_minerba  # noqa: E402

TOL_BARIS = 0.01
TOL_AGREGAT = 0.5
TOL_PCT = 0.01
TOL_LAJU = 1e-4
TOL_BOOTSTRAP = 2000.0   # selang bootstrap boleh bergeser < 2.000 ha dari arsip (urutan iterasi) → WARN, bukan FAIL
TOL_AUC = 1e-6
TOL_AUC_RS = 0.01        # AUC model RS boleh bergeser < 0,01 dari arsip BILA sebabnya bendera D (lihat bawah)
# Jangkar selang bootstrap pipeline v3 (catatan W0/W3 2 Sep 2026, SKEMA §7): lo / hi ha. Satu-satunya
# angka tertulis di verifier — dipakai hanya untuk membedakan "beda yang sudah dijelaskan" dari drift baru.
JANGKAR_V3_BOOTSTRAP = (1144662.06, 1252064.79)
# Kohort transisi v3 (SKEMA §4, W0 2 Sep 2026): label → (n_konsesi, n_keluar). `umur-10_+0` sengaja BEDA
# dari arsip (682/143) karena jendela dijepit 2001–2024 (tahun_awal 2000 tak lagi dipakai).
JANGKAR_V3_KOHORT = {"2001-2024": (825, 0), "umur+0_+10": (268, 557), "umur-10_+0": (624, 201)}
# Bendera `izin_klasifikasi.pra_izin_dominan` v3 dihitung pada jendela 2001–2024, arsip pada 2001–2025
# (03_izin.py). Menghapus tahun 2025 hanya bisa MEMPERKECIL sisi pasca → pangsa pra naik, jadi bendera
# hanya boleh berbalik 0/NULL → 1. Jumlah pembalikan yang sudah ditelusuri: 15 konsesi.
JANGKAR_V3_PRA_DOMINAN = 15

# Tabel inti yang WAJIB ada — absen = gagal keras (exit 2), bukan WARN.
TABEL_INTI = [t for t in S.KOLOM if t not in INFRA]


class Pelapor:
    def __init__(self, tulis=print):
        self.hasil: list[tuple[str, str, str]] = []
        self.tulis = tulis

    def catat(self, status, nama, pesan):
        self.hasil.append((status, nama, pesan))
        self.tulis(f"[{status}] {nama} — {pesan}")

    def ok(self, nama, pesan): self.catat("PASS", nama, pesan)
    def warn(self, nama, pesan): self.catat("WARN", nama, pesan)
    def fail(self, nama, pesan): self.catat("FAIL", nama, pesan)

    def status(self, nama: str) -> str | None:
        for st, n, _ in self.hasil:
            if n == nama:
                return st
        return None

    def ringkas(self) -> int:
        n = {s: sum(1 for st, _, _ in self.hasil if st == s) for s in ("PASS", "WARN", "FAIL")}
        self.tulis(f"RINGKASAN: {len(self.hasil)} pemeriksaan · {n['PASS']} PASS · {n['WARN']} WARN · {n['FAIL']} FAIL")
        return 1 if n["FAIL"] else 0


def satu(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return r[0] if r else None


def _ringkas_daftar(xs, n=6):
    xs = list(xs)
    return ", ".join(str(x) for x in xs[:n]) + (f" … (+{len(xs) - n})" if len(xs) > n else "")


# ── (1) skema ────────────────────────────────────────────────────────────────────────────────

def cek_skema(con, lap):
    objek = S.objek_di_db(con)
    beda = []
    for t in S.KOLOM:
        if t not in objek:
            beda.append(f"{t}: absen")
            continue
        jenis = "view" if t in S.VIEW else "table"
        if objek[t] != jenis:
            beda.append(f"{t}: {objek[t]} padahal harus {jenis}")
        nyata, harap = S.kolom_objek(con, t), S.KOLOM[t]
        if set(nyata) != set(harap):
            kurang, lebih = sorted(set(harap) - set(nyata)), sorted(set(nyata) - set(harap))
            beda.append(f"{t}: kolom kurang {kurang} lebih {lebih}")
    lebih_objek = sorted(set(objek) - set(S.KOLOM))
    if lebih_objek:
        beda.append("objek di luar SKEMA.md: " + ", ".join(lebih_objek))
    if beda:
        lap.fail("skema", "; ".join(beda))
    else:
        lap.ok("skema", f"{len(S.KOLOM)} objek ({len(S.VIEW)} view) & seluruh kolomnya persis SKEMA.md")


# ── (2) meta dua arah + bangun + sumber + rujukan ────────────────────────────────────────────

def cek_meta(con, lap):
    masalah = cakupan_dua_arah(con)
    if masalah:
        lap.fail("meta-dua-arah", "; ".join(masalah))
    else:
        n_t = satu(con, "SELECT COUNT(*) FROM analysis_meta")
        n_k = satu(con, "SELECT COUNT(*) FROM column_meta")
        lap.ok("meta-dua-arah", f"{n_t} tabel/view ber-analysis_meta, {n_k} baris column_meta, 100% dua arah")
    kosong = [r[0] for r in con.execute(
        "SELECT nama_tabel FROM analysis_meta WHERE TRIM(deskripsi)='' OR TRIM(metode)='' OR TRIM(skrip)='' OR TRIM(lisensi)=''")]
    kosong += [f"{r[0]}.{r[1]}" for r in con.execute("SELECT nama_tabel, nama_kolom FROM column_meta WHERE TRIM(deskripsi)=''")]
    if kosong:
        lap.fail("meta-terisi", "deskripsi/metode/skrip/lisensi kosong: " + _ringkas_daftar(kosong))
    else:
        lap.ok("meta-terisi", "tak ada deskripsi/metode/skrip/lisensi kosong")


def cek_bangun(con, lap, himpunan):
    ada = {r[0]: r[1] for r in con.execute("SELECT kunci, nilai FROM bangun")}
    kurang = [k for k in S.BANGUN_WAJIB if k not in ada]
    if kurang:
        lap.fail("bangun-kunci", f"kunci bangun absen: {kurang}")
    elif ada["himpunan"] != himpunan:
        lap.fail("bangun-kunci", f"bangun.himpunan={ada['himpunan']!r} ≠ --himpunan {himpunan!r}")
    elif int(ada["konsesi.n"]) != satu(con, "SELECT COUNT(*) FROM konsesi"):
        lap.fail("bangun-kunci", f"bangun.konsesi.n={ada['konsesi.n']} ≠ COUNT(konsesi)")
    else:
        lap.ok("bangun-kunci", f"{len(ada)} kunci; himpunan={himpunan}, konsesi.n={ada['konsesi.n']}, "
                               f"pipeline_versi={ada['pipeline_versi']!r}, git={ada['git_commit']}")


def cek_sumber(con, lap):
    ids = {r[0] for r in con.execute("SELECT id FROM sumber")}
    wajib = {"hansen", "mapbiomas", "geoportal_wiup"}
    kosong = satu(con, "SELECT COUNT(*) FROM sumber WHERE TRIM(lisensi)='' OR TRIM(nama)=''")
    if not wajib <= ids:
        lap.fail("sumber", f"id sumber wajib absen: {sorted(wajib - ids)} (ada: {sorted(ids)})")
    elif kosong:
        lap.fail("sumber", f"{kosong} baris sumber tanpa nama/lisensi")
    else:
        lap.ok("sumber", f"{len(ids)} sumber tercatat: {', '.join(sorted(ids))}")


# Kartu provenansi Geoportal di halaman Metodologi menulis jumlah fitur hasil unduhan APA ADANYA
# (angka itu hidup di MANIFEST.csv, bukan di DB, jadi tak bisa lewat rantai dashboard-stats.json).
# Tanpa pemeriksaan ini, unduh ulang Geoportal membuat kartu itu salah diam-diam.
MANIFEST_GEOPORTAL = AKAR / "data/geoportal/MANIFEST.csv"
KARTU_METODOLOGI = AKAR / "webapp/src/views/MethodologyView.tsx"
# berkas MANIFEST → potongan kalimat yang HARUS ada di kartu (mis. 397 → "397 poligon operasi")
FRASA_KARTU = {
    "ippkh_operasi.geojson": "{n} poligon operasi",
    "ippkh_eksplorasi.geojson": "{n} eksplorasi",
    "overlay_hutan.geojson": "{n} fitur",
}


def _ribuan(n: int) -> str:
    """Format Indonesia: 3962 → '3.962' (kartu memakai titik sbg pemisah ribuan)."""
    return f"{n:,}".replace(",", ".")


def cek_kartu_metodologi(lap):
    if not MANIFEST_GEOPORTAL.is_file() or not KARTU_METODOLOGI.is_file():
        lap.warn("kartu-metodologi",
                 "MANIFEST.csv atau MethodologyView.tsx tak ada — kesegaran kartu Geoportal tak diperiksa")
        return
    import csv
    n_fitur = {r["berkas"]: int(r["n_fitur"])
               for r in csv.DictReader(MANIFEST_GEOPORTAL.open(encoding="utf-8"))}
    teks = KARTU_METODOLOGI.read_text(encoding="utf-8")
    hilang = []
    for berkas, pola in FRASA_KARTU.items():
        if berkas not in n_fitur:
            hilang.append(f"{berkas} tak ada di MANIFEST")
            continue
        n = n_fitur[berkas]
        if pola.format(n=n) not in teks and pola.format(n=_ribuan(n)) not in teks:
            hilang.append(f"{berkas}: MANIFEST {_ribuan(n)}, kartu tak menyebutnya")
    if hilang:
        lap.fail("kartu-metodologi",
                 "kartu Geoportal tak sinkron dgn MANIFEST.csv (unduh ulang tanpa memperbarui kartu?): "
                 + "; ".join(hilang))
    else:
        lap.ok("kartu-metodologi",
               "jumlah fitur Geoportal di kartu Metodologi cocok MANIFEST.csv ("
               + ", ".join(f"{b.split('.')[0]}={_ribuan(n_fitur[b])}" for b in FRASA_KARTU) + ")")


def cek_rujukan(con, lap):
    objek = S.objek_di_db(con)
    yatim = []
    for t in S.ANAK_KONSESI:
        if t in objek:
            n = satu(con, f"SELECT COUNT(*) FROM {t} WHERE kode_wiup NOT IN (SELECT kode_wiup FROM konsesi)")
            if n:
                yatim.append(f"{t}={n}")
    if yatim:
        lap.fail("rujukan-konsesi", "baris dengan kode_wiup di luar konsesi: " + ", ".join(yatim))
    else:
        lap.ok("rujukan-konsesi", f"{len(S.ANAK_KONSESI)} tabel anak tanpa kode_wiup yatim")


# ── (3)(4) Hansen ────────────────────────────────────────────────────────────────────────────

def _tanpa_batch(con) -> int:
    """Konsesi yang poligonnya tak menghasilkan satu pun baris di CSV batch Hansen (poligon lebih
    kecil dari satu piksel ~30 m). 01_identitas mencatatnya di `bangun.konsesi.tanpa_batch_csv`.
    Mereka SAH tak punya baris hansen_ringkas/izin_laju: menuliskan nol akan mengklaim
    "hutan 2000 = 0 ha" padahal yang benar adalah "tak terukur"."""
    r = con.execute("SELECT nilai FROM bangun WHERE kunci='konsesi.tanpa_batch_csv'").fetchone()
    if not r or not r[0]:
        return 0
    nilai = str(r[0]).strip()
    # 01_identitas menulis DAFTAR kode_wiup (dipisah koma/spasi), bukan cacah. Hitung hanya kode
    # yang memang anggota himpunan DB ini DAN benar-benar tak punya baris hansen_ringkas —
    # daftar itu bisa memuat kode dari himpunan lain (mis. galian C yang tak ada di minerba).
    kode = [x for x in nilai.replace(";", ",").replace(" ", ",").split(",") if x]
    if not kode:
        return 0
    q = ",".join("?" * len(kode))
    return con.execute(
        f"SELECT COUNT(*) FROM konsesi WHERE kode_wiup IN ({q}) "
        f"AND kode_wiup NOT IN (SELECT kode_wiup FROM hansen_ringkas)", kode).fetchone()[0]


def cek_hansen(con, lap):
    n_k = satu(con, "SELECT COUNT(*) FROM konsesi") - _tanpa_batch(con)
    n_r = satu(con, "SELECT COUNT(*) FROM hansen_ringkas")
    tanpa = max(0, satu(con, "SELECT COUNT(*) FROM konsesi WHERE kode_wiup NOT IN (SELECT kode_wiup FROM hansen_ringkas)")
                - _tanpa_batch(con))
    luar = satu(con, f"SELECT COUNT(*) FROM hansen_tahunan WHERE tahun NOT BETWEEN {S.TAHUN_AWAL} AND {S.TAHUN_AKHIR}")
    neg = satu(con, "SELECT COUNT(*) FROM hansen_tahunan WHERE hilang_ha < 0")
    beda = con.execute(f"""
        SELECT r.kode_wiup, r.hilang_2001_2024_ha, COALESCE(t.s,0)
        FROM hansen_ringkas r LEFT JOIN (SELECT kode_wiup, SUM(hilang_ha) s FROM hansen_tahunan GROUP BY 1) t USING (kode_wiup)
        WHERE ABS(r.hilang_2001_2024_ha - COALESCE(t.s,0)) > {TOL_BARIS}""").fetchall()
    total = satu(con, "SELECT COALESCE(SUM(hilang_2001_2024_ha),0) FROM hansen_ringkas")
    if tanpa or n_r != n_k or luar or neg or beda:
        pesan = []
        if tanpa or n_r != n_k:
            pesan.append(f"konsesi={n_k}, hansen_ringkas={n_r}, konsesi tanpa ringkas={tanpa}")
        if luar:
            pesan.append(f"{luar} baris hansen_tahunan di luar {S.TAHUN_AWAL}–{S.TAHUN_AKHIR}")
        if neg:
            pesan.append(f"{neg} baris hilang_ha negatif")
        if beda:
            pesan.append(f"{len(beda)} konsesi Σ tahunan ≠ ringkas (>{TOL_BARIS} ha): "
                         + _ringkas_daftar(f"{k} {a:.2f}≠{b:.2f}" for k, a, b in beda))
        lap.fail("hansen-identitas", "; ".join(pesan))
    else:
        lap.ok("hansen-identitas", f"{n_k} konsesi: Σ hansen_tahunan = hilang_2001_2024_ha (tol {TOL_BARIS}); "
                                   f"Σ = {total:,.2f} ha")

    salah = con.execute(f"""
        SELECT kode_wiup FROM hansen_ringkas
        WHERE (hutan_2000_ha <= 0 AND pct_hutan_2000 IS NOT NULL)
           OR (hutan_2000_ha > 0 AND (pct_hutan_2000 IS NULL
               OR ABS(pct_hutan_2000 - 100.0*hilang_2001_2024_ha/hutan_2000_ha) > {TOL_PCT}))""").fetchall()
    if salah:
        lap.fail("hansen-pct", f"{len(salah)} konsesi pct_hutan_2000 ≠ 100·hilang/hutan_2000 (tol {TOL_PCT}): "
                               + _ringkas_daftar(r[0] for r in salah))
    else:
        hutan = satu(con, "SELECT COALESCE(SUM(hutan_2000_ha),0) FROM hansen_ringkas")
        lap.ok("hansen-pct", f"pct_hutan_2000 konsisten; agregat {100.0 * total / hutan if hutan else 0:.1f}% "
                             f"dari hutan 2000 {hutan:,.2f} ha")


# ── (5) izin_laju ────────────────────────────────────────────────────────────────────────────

def cek_izin_laju(con, lap):
    pesan = []
    n_k = satu(con, "SELECT COUNT(*) FROM konsesi") - _tanpa_batch(con)
    n_l = satu(con, "SELECT COUNT(*) FROM izin_laju")
    if n_l != n_k:
        pesan.append(f"izin_laju={n_l} ≠ konsesi berukur={n_k}")
    beda_thn = satu(con, "SELECT COUNT(*) FROM izin_laju l JOIN konsesi k USING (kode_wiup) "
                         "WHERE COALESCE(l.tahun_izin,-1) <> COALESCE(k.tahun_izin,-1)")
    if beda_thn:
        pesan.append(f"{beda_thn} baris tahun_izin ≠ konsesi.tahun_izin")
    vonis_asing = [r[0] for r in con.execute(
        f"SELECT DISTINCT vonis FROM izin_laju WHERE vonis NOT IN ({','.join('?' * len(S.VONIS))})", S.VONIS)]
    if vonis_asing:
        pesan.append(f"vonis di luar domain: {vonis_asing}")
    null_wajib = satu(con, f"SELECT COUNT(*) FROM izin_laju WHERE tahun_izin BETWEEN {S.TAHUN_AWAL} AND {S.TAHUN_AKHIR} "
                           "AND (hilang_pra_ha IS NULL OR hilang_pasca_ha IS NULL)")
    if null_wajib:
        pesan.append(f"{null_wajib} konsesi ber-tahun_izin dalam jendela tapi pra/pasca NULL")
    beda_pra = con.execute(f"""
        SELECT l.kode_wiup FROM izin_laju l WHERE l.hilang_pra_ha IS NOT NULL AND ABS(l.hilang_pra_ha -
          (SELECT COALESCE(SUM(hilang_ha),0) FROM hansen_tahunan t WHERE t.kode_wiup=l.kode_wiup AND t.tahun < l.tahun_izin)) > {TOL_BARIS}""").fetchall()
    beda_pasca = con.execute(f"""
        SELECT l.kode_wiup FROM izin_laju l WHERE l.hilang_pasca_ha IS NOT NULL AND ABS(l.hilang_pasca_ha -
          (SELECT COALESCE(SUM(hilang_ha),0) FROM hansen_tahunan t WHERE t.kode_wiup=l.kode_wiup AND t.tahun >= l.tahun_izin)) > {TOL_BARIS}""").fetchall()
    if beda_pra:
        pesan.append(f"{len(beda_pra)} konsesi hilang_pra_ha ≠ Σ hansen_tahunan tahun<tahun_izin: " + _ringkas_daftar(r[0] for r in beda_pra))
    if beda_pasca:
        pesan.append(f"{len(beda_pasca)} konsesi hilang_pasca_ha ≠ Σ hansen_tahunan tahun≥tahun_izin: " + _ringkas_daftar(r[0] for r in beda_pasca))
    dist = {v: n for v, n in con.execute("SELECT vonis, COUNT(*) FROM izin_laju GROUP BY vonis ORDER BY vonis")}
    if pesan:
        lap.fail("izin-laju-identitas", "; ".join(pesan))
    else:
        lap.ok("izin-laju-identitas", f"{n_l} konsesi, pra/pasca = Σ hansen_tahunan (tol {TOL_BARIS}); vonis {dist}")


# ── (6) hash geometri ────────────────────────────────────────────────────────────────────────

def cek_hash(con, lap):
    h_k = baca_bangun(con, "konsesi.hash_geometri")
    h_m = baca_bangun(con, "mapbiomas.hash_geometri")
    h_hitung = hash_geometri(con)
    if not h_k or not h_m:
        lap.fail("hash-geometri", f"bangun.konsesi.hash_geometri={h_k!r}, bangun.mapbiomas.hash_geometri={h_m!r} (absen)")
    elif h_k != h_hitung:
        lap.fail("hash-geometri", f"bangun.konsesi.hash_geometri {h_k[:12]}… ≠ hitung ulang {h_hitung[:12]}… (geometri konsesi berubah setelah 01?)")
    elif h_m != h_k:
        lap.fail("hash-geometri", f"bangun.mapbiomas.hash_geometri {h_m[:12]}… ≠ konsesi {h_k[:12]}… — tabel MapBiomas dibangun atas geometri lain")
    else:
        lap.ok("hash-geometri", f"konsesi = mapbiomas = hitung ulang ({h_k[:16]}…)")


# ── (7)(8) transisi ──────────────────────────────────────────────────────────────────────────

def cek_transisi(con, lap):
    pesan = []
    n27 = satu(con, "SELECT COUNT(*) FROM transisi_konsesi WHERE kelas_awal=27 OR kelas_akhir=27")
    if n27:
        pesan.append(f"{n27} baris transisi memuat kelas 27 (awan)")
    label_asing = [r[0] for r in con.execute(
        "SELECT DISTINCT label FROM transisi_konsesi WHERE label NOT IN (SELECT label FROM transisi_kohort)")]
    if label_asing:
        pesan.append(f"label tanpa baris transisi_kohort: {label_asing}")
    kohort_beda = con.execute("""
        SELECT k.label, k.n_konsesi, COUNT(DISTINCT t.kode_wiup) FROM transisi_kohort k
        LEFT JOIN transisi_konsesi t USING (label) GROUP BY k.label HAVING k.n_konsesi <> COUNT(DISTINCT t.kode_wiup)""").fetchall()
    if kohort_beda:
        pesan.append("transisi_kohort.n_konsesi ≠ jumlah konsesi di transisi_konsesi: "
                     + _ringkas_daftar(f"{l} {a}≠{b}" for l, a, b in kohort_beda))
    # Tahun ujung transisi yang tak ada di mapbiomas_tahunan (2001–2024) — mis. label umur-10_+0
    # arsip memakai tahun_awal 2000 — tak bisa diperiksa: dilaporkan WARN terpisah, bukan FAIL.
    tak_terperiksa = []
    for sisi, kol in (("awal", "tahun_awal"), ("akhir", "tahun_akhir")):
        luar = con.execute(f"""
            SELECT label, COUNT(DISTINCT kode_wiup), MIN({kol}), MAX({kol}) FROM transisi_konsesi
            WHERE {kol} NOT BETWEEN {S.TAHUN_AWAL} AND {S.TAHUN_AKHIR} GROUP BY label""").fetchall()
        tak_terperiksa += [f"sisi {sisi} {l}: {n} konsesi (tahun {a}–{b})" for l, n, a, b in luar]
        beda = con.execute(f"""
            WITH t AS (SELECT kode_wiup, label, {kol} AS thn, SUM(ha) ha FROM transisi_konsesi
                       WHERE {kol} BETWEEN {S.TAHUN_AWAL} AND {S.TAHUN_AKHIR} GROUP BY 1,2,3),
                 m AS (SELECT kode_wiup, tahun, SUM(ha) ha FROM mapbiomas_tahunan WHERE kelas <> 27 GROUP BY 1,2)
            SELECT t.label, COUNT(*) FROM t LEFT JOIN m ON m.kode_wiup=t.kode_wiup AND m.tahun=t.thn
            WHERE ABS(t.ha - COALESCE(m.ha,0)) > {TOL_BARIS} GROUP BY t.label""").fetchall()
        if beda:
            pesan.append(f"sisi {sisi}: Σ transisi ≠ Σ mapbiomas_tahunan (tanpa kelas 27) pada "
                         + ", ".join(f"{l}: {n} konsesi" for l, n in beda))
    if tak_terperiksa:
        lap.warn("transisi-tahun-luar-jendela", "ujung transisi di luar mapbiomas_tahunan "
                 f"{S.TAHUN_AWAL}–{S.TAHUN_AKHIR}, identitasnya tak terperiksa: " + "; ".join(tak_terperiksa))
    if pesan:
        lap.fail("transisi-identitas", "; ".join(pesan))
    else:
        tot = con.execute("SELECT label, ROUND(SUM(ha),2), COUNT(DISTINCT kode_wiup) FROM transisi_konsesi GROUP BY label ORDER BY label").fetchall()
        lap.ok("transisi-identitas", "kedua sisi = Σ mapbiomas_tahunan tanpa kelas 27 per konsesi (tol "
                                     f"{TOL_BARIS}); " + "; ".join(f"{l}: {ha:,.2f} ha / {n} konsesi" for l, ha, n in tot))

    pasangan = con.execute("SELECT DISTINCT tahun_awal, tahun_akhir FROM transisi_pasangan").fetchall()
    salah = [p for p in pasangan if not (S.TAHUN_AWAL <= p[0] < p[1] <= S.TAHUN_AKHIR)]
    if len(pasangan) != S.N_PASANGAN or salah:
        lap.fail("transisi-pasangan", f"{len(pasangan)} pasangan tahun (harus {S.N_PASANGAN}); di luar jendela/a≥b: {salah[:5]}")
    else:
        lap.ok("transisi-pasangan", f"{S.N_PASANGAN} pasangan tahun a<b dalam {S.TAHUN_AWAL}–{S.TAHUN_AKHIR}, "
                                    f"{satu(con, 'SELECT COUNT(*) FROM transisi_pasangan')} baris")


# ── (9) umur, (10) keyakinan ─────────────────────────────────────────────────────────────────

def cek_umur(con, lap):
    ranc = {r[0] for r in con.execute("SELECT DISTINCT rancangan FROM umur_izin_kurun")}
    nol_t = satu(con, "SELECT COUNT(*) FROM umur_izin_tahunan WHERE n_konsesi <= 0")
    nol_k = satu(con, "SELECT COUNT(*) FROM umur_izin_kurun WHERE n_konsesi <= 0")
    n_t = satu(con, "SELECT COUNT(*) FROM umur_izin_tahunan")
    if not {"A_seimbang", "B_semua"} <= ranc:
        lap.fail("umur-izin", f"rancangan wajib A_seimbang & B_semua; ada {sorted(ranc)}")
    elif nol_t or nol_k or n_t == 0:
        lap.fail("umur-izin", f"n_konsesi ≤ 0: tahunan {nol_t} baris, kurun {nol_k} baris (tahunan total {n_t})")
    else:
        a = con.execute("SELECT kurun, n_konsesi, laju_bahaya_per_tahun FROM umur_izin_kurun WHERE rancangan='A_seimbang' ORDER BY umur_awal").fetchall()
        lap.ok("umur-izin", f"{n_t} titik tahunan n_konsesi>0; A_seimbang " +
               " → ".join(f"{k} n={n} {100 * (l or 0):.3f}%/th" for k, n, l in a))


def cek_keyakinan(con, lap):
    r = {k: v for k, v in con.execute("SELECT kunci, nilai FROM keyakinan_ringkas")}
    kurang = [k for k in S.KEYAKINAN_KUNCI if k not in r]
    if kurang:
        lap.fail("keyakinan-batas", f"kunci keyakinan_ringkas absen: {kurang} (ada: {sorted(r)})")
        return
    try:
        bawah, lo, hrp, hi, atas = (float(r[k]) for k in ("hilang_batas_bawah_ha", "hilang_bootstrap_lo_ha",
                                                           "hilang_harapan_ha", "hilang_bootstrap_hi_ha", "hilang_batas_atas_ha"))
    except ValueError as e:
        lap.fail("keyakinan-batas", f"nilai bukan angka: {e}")
        return
    luar = satu(con, "SELECT COUNT(*) FROM keyakinan_pra_izin WHERE peluang_akhir < 0 OR peluang_akhir > 1 OR peluang_akhir IS NULL")
    if not (bawah <= lo <= hrp <= hi <= atas):
        lap.fail("keyakinan-batas", f"urutan salah: bawah {bawah:,.2f} ≤ lo {lo:,.2f} ≤ harapan {hrp:,.2f} ≤ hi {hi:,.2f} ≤ atas {atas:,.2f}")
    elif luar:
        lap.fail("keyakinan-batas", f"{luar} konsesi peluang_akhir di luar [0,1] / NULL")
    else:
        n = satu(con, "SELECT COUNT(*) FROM keyakinan_pra_izin")
        lap.ok("keyakinan-batas", f"harapan {hrp:,.2f} ha [{lo:,.2f}–{hi:,.2f}] dalam [{bawah:,.2f}–{atas:,.2f}]; {n} konsesi peluang ∈ [0,1]")


# ── (11) stats web ───────────────────────────────────────────────────────────────────────────

def _beda_dict(harap, ada, awalan=""):
    out = []
    if isinstance(harap, dict) and isinstance(ada, dict):
        for k in sorted(set(harap) | set(ada)):
            if k not in ada:
                out.append(f"{awalan}{k}: absen di JSON")
            elif k not in harap:
                out.append(f"{awalan}{k}: tak dikenal (bukan dari DB)")
            else:
                out += _beda_dict(harap[k], ada[k], f"{awalan}{k}.")
    elif isinstance(harap, list) and isinstance(ada, list):
        if len(harap) != len(ada):
            out.append(f"{awalan[:-1]}: panjang {len(ada)} ≠ {len(harap)}")
        else:
            for i, (h, a) in enumerate(zip(harap, ada)):
                out += _beda_dict(h, a, f"{awalan}{i}.")
    elif harap != ada:
        out.append(f"{awalan[:-1]}: JSON {ada!r} ≠ DB {harap!r}")
    return out


def cek_stats(con, lap, stats_path: Path, himpunan: str):
    if not stats_path.is_file():
        lap.fail("stats-web", f"{stats_path} tidak ada — jalankan 09_sajikan.py")
        return
    d = json.loads(stats_path.read_text(encoding="utf-8"))
    beda = []
    if d.get("jendela") != f"{S.TAHUN_AWAL}-{S.TAHUN_AKHIR}":
        beda.append(f"jendela {d.get('jendela')!r} ≠ '{S.TAHUN_AWAL}-{S.TAHUN_AKHIR}'")
    arsip = [k for k in ("periode", "atribusi", "lapisan", "kohort", "default", "full", "registry") if k in d]
    if arsip:
        beda.append(f"kunci arsip masih ada: {arsip}")
    if himpunan == "minerba":
        harap = stats_minerba(con)
        for k, v in harap.items():
            if k not in d:
                beda.append(f"{k}: absen di JSON")
            else:
                beda += _beda_dict(v, d[k], f"{k}.")
        ringkas = harap["minerba"]
    else:
        harap = blok_lengkap(con)
        if d.get("lengkap") is None:
            beda.append("lengkap: null padahal DB lengkap diverifikasi")
        else:
            beda += _beda_dict(harap, d["lengkap"], "lengkap.")
        ringkas = harap
    if beda:
        lap.fail("stats-web", f"dashboard-stats.json ≠ DB: " + "; ".join(beda[:12]) +
                 (f" … (+{len(beda) - 12})" if len(beda) > 12 else "") + " → jalankan ulang 09_sajikan.py")
    else:
        lap.ok("stats-web", f"{stats_path.name} = DB ({himpunan}): {ringkas['n_konsesi']} konsesi, "
                            f"{ringkas['hilang_2001_2024_ha']:,} ha, {ringkas['pct_hutan_2000']}%")


# ── (12) paritas arsip ───────────────────────────────────────────────────────────────────────

def cari_arsip(p: Path | None, *kandidat: Path) -> Path | None:
    coba = []
    if p is not None:
        coba += [p, AKAR / "data/arsip" / p.name, AKAR / "data" / p.name]
    coba += list(kandidat)
    for c in coba:
        if c.is_file():
            return c
    return None


def _cmp(a, b, tol):
    return abs((a or 0) - (b or 0)) <= tol


def cek_paritas_minerba(con, lap, arsip_k: Path, arsip_m: Path | None):
    con.execute("ATTACH DATABASE ? AS a", (f"file:{arsip_k}?mode=ro",))
    # §2 Hansen
    n_a = satu(con, "SELECT COUNT(*) FROM a.wiup_geoportal")
    n_b = satu(con, "SELECT COUNT(*) FROM konsesi")
    hil_a = satu(con, "SELECT SUM(loss_2001_2024_ha) FROM a.wiup_loss")
    hil_b = satu(con, "SELECT SUM(hilang_2001_2024_ha) FROM hansen_ringkas")
    hut_a = satu(con, "SELECT SUM(forest_2000_ha) FROM a.wiup_loss")
    hut_b = satu(con, "SELECT SUM(hutan_2000_ha) FROM hansen_ringkas")
    if n_a != n_b or not _cmp(hil_a, hil_b, TOL_AGREGAT) or not _cmp(hut_a, hut_b, TOL_AGREGAT):
        lap.fail("paritas-hansen", f"n {n_b}≠{n_a} / Σ hilang {hil_b:,.2f}≠{hil_a:,.2f} / Σ hutan {hut_b:,.2f}≠{hut_a:,.2f} (arsip {arsip_k.name})")
    else:
        lap.ok("paritas-hansen", f"n={n_b}, Σ hilang 2001–2024 {hil_b:,.2f} ha, Σ hutan 2000 {hut_b:,.2f} ha = arsip (tol {TOL_AGREGAT})")

    # vonis — 5 vonis inti persis; izin_setelah_jendela + tanpa_tahun_izin dibandingkan GABUNGANNYA
    # (v3 memasukkan SK 2026 ke izin_setelah_jendela: 63/13, arsip 59/17 — keputusan W0 2 Sep 2026)
    baru = {v: n for v, n in con.execute("SELECT vonis, COUNT(*) FROM izin_laju GROUP BY vonis")}
    lama = {}
    for verd, n in con.execute("SELECT verdict_jendela_2024, COUNT(*) FROM a.wiup_temporal GROUP BY 1"):
        k = (verd or "tanpa_tahun_izin").replace("izin_setelah_jendela_2024", "izin_setelah_jendela")
        lama[k] = lama.get(k, 0) + n
    inti = ["accelerated_post_iup", "decelerated_post_iup", "stable", "no_loss_either", "loss_only_after_iup"]
    beda = [f"{v}: {baru.get(v, 0)} ≠ arsip {lama.get(v, 0)}" for v in inti if baru.get(v, 0) != lama.get(v, 0)]
    gab_b = baru.get("izin_setelah_jendela", 0) + baru.get("tanpa_tahun_izin", 0)
    gab_a = lama.get("izin_setelah_jendela", 0) + lama.get("tanpa_tahun_izin", 0)
    if gab_b != gab_a:
        beda.append(f"izin_setelah_jendela+tanpa_tahun_izin: {gab_b} ≠ arsip {gab_a}")
    (lap.fail if beda else lap.ok)("paritas-vonis", "; ".join(beda) if beda else
        f"5 vonis inti = arsip; setelah_jendela+tanpa = {gab_b} (= arsip {lama.get('izin_setelah_jendela', 0)}+{lama.get('tanpa_tahun_izin', 0)}): {baru}")

    # §3 klasifikasi
    ka = sorted(con.execute("SELECT kelas, COALESCE(bukti,''), COUNT(*) FROM a.klasifikasi_izin GROUP BY 1,2").fetchall())
    kb = sorted(con.execute("SELECT kelas, COALESCE(bukti,''), COUNT(*) FROM izin_klasifikasi GROUP BY 1,2").fetchall())
    (lap.ok if ka == kb else lap.fail)("paritas-klasifikasi", f"kelas×bukti {kb}" + ("" if ka == kb else f" ≠ arsip {ka}"))

    # §3 bendera pra_izin_dominan — jendela v3 2001–2024 vs arsip 2001–2025 (lihat JANGKAR_V3_PRA_DOMINAN).
    # Arah pembalikan itu sendiri yang diuji: 1 → 0 mustahil bila hanya jendelanya yang dipersempit.
    naik = satu(con, "SELECT COUNT(*) FROM izin_klasifikasi b JOIN a.klasifikasi_izin x USING (kode_wiup) "
                     "WHERE COALESCE(b.pra_izin_dominan,0)=1 AND COALESCE(x.pra_izin_dominan,0)=0")
    turun = satu(con, "SELECT COUNT(*) FROM izin_klasifikasi b JOIN a.klasifikasi_izin x USING (kode_wiup) "
                      "WHERE COALESCE(b.pra_izin_dominan,0)=0 AND COALESCE(x.pra_izin_dominan,0)=1")
    if turun:
        lap.fail("paritas-pra-izin-dominan", f"{turun} konsesi bendera pra_izin_dominan berbalik 1 → 0; mustahil "
                                             "bila hanya jendela pasca yang dipersempit 2025 → 2024 (03_izin.py)")
    elif naik in (0, JANGKAR_V3_PRA_DOMINAN):
        lap.ok("paritas-pra-izin-dominan", f"{naik} konsesi berbalik 0/NULL → 1 (jangkar v3 {JANGKAR_V3_PRA_DOMINAN}; "
                                           "jendela pasca 2024 vs arsip 2025), tak ada yang berbalik 1 → 0")
    else:
        lap.warn("paritas-pra-izin-dominan", f"{naik} konsesi berbalik 0/NULL → 1 (arahnya benar) tapi ≠ jangkar v3 "
                                             f"{JANGKAR_V3_PRA_DOMINAN} — periksa 03_izin.py bila tak disengaja")

    # §1 registri
    ca = satu(con, "SELECT COUNT(*) FROM a.wiup_match WHERE db_match='yes'")
    cb = satu(con, "SELECT SUM(cocok) FROM konsesi_registri")
    sa = sorted(con.execute("SELECT COALESCE(match_strategy,''), COUNT(*) FROM a.wiup_match GROUP BY 1").fetchall())
    sb = sorted(con.execute("SELECT COALESCE(strategi_cocok,''), COUNT(*) FROM konsesi_registri GROUP BY 1").fetchall())
    (lap.ok if (ca == cb and sa == sb) else lap.fail)("paritas-registri",
        f"cocok={cb}, per strategi {sb}" + ("" if (ca == cb and sa == sb) else f" ≠ arsip cocok={ca} {sa}"))

    # §5 IPPKH
    ia = con.execute("SELECT SUM(punya_ippkh), SUM(punya_ippkh_tambang) FROM a.konsesi_ippkh").fetchone()
    ib = con.execute("SELECT SUM(punya_ippkh), SUM(punya_ippkh_tambang) FROM ippkh").fetchone()
    (lap.ok if tuple(ia) == tuple(ib) else lap.fail)("paritas-ippkh",
        f"punya IPPKH {ib[0]}, tambang {ib[1]}" + ("" if tuple(ia) == tuple(ib) else f" ≠ arsip {ia[0]}/{ia[1]}"))

    # §7 keyakinan — harapan/batas bawah/batas atas/AUC/peluang per konsesi identik dgn arsip.
    # Selang bootstrap lo/hi SENGAJA berbeda dari arsip (catatan W0/W3, SKEMA §7): urutan iterasi
    # konsesi ORDER BY kode_wiup (v3) vs rowid (arsip) mengubah undian bootstrap, bukan modelnya.
    ra = {k: v for k, v in con.execute("SELECT kunci, nilai FROM a.keyakinan_ringkas")}
    rb = {k: v for k, v in con.execute("SELECT kunci, nilai FROM keyakinan_ringkas")}
    beda = []
    for lama, baru_k in (("loss_harapan_ha", "hilang_harapan_ha"), ("loss_batas_bawah_ha", "hilang_batas_bawah_ha"),
                         ("loss_batas_atas_ha", "hilang_batas_atas_ha")):
        try:
            if not _cmp(float(ra[lama]), float(rb[baru_k]), TOL_AGREGAT):
                beda.append(f"{baru_k} {rb[baru_k]} ≠ arsip {ra[lama]}")
        except (KeyError, ValueError) as e:
            beda.append(f"{baru_k}: {e!r}")
    # AUC: model R (penebak A,B,C,G) WAJIB identik — dialah yang memberi `peluang_akhir`. Model RS
    # menambah sinyal D = izin_klasifikasi.pra_izin_dominan, yang di v3 memakai jendela 2001–2024 dan
    # di arsip 2001–2025; jadi bila (dan HANYA bila) bendera D memang berbeda, pergeseran AUC RS yang
    # kecil = WARN berlabel, bukan FAIL. RS cuma uji ketahanan; atribusi tak memakainya.
    auc_a = {m: x for m, x in con.execute("SELECT model, MAX(auc) FROM a.keyakinan_model GROUP BY model")}
    auc_b = {m: x for m, x in con.execute("SELECT model, MAX(auc) FROM keyakinan_model GROUP BY model")}
    warn_auc_rs = None
    for m in sorted(set(auc_a) | set(auc_b)):
        if m not in auc_a or m not in auc_b:
            beda.append(f"AUC {m}: {auc_b.get(m)} ≠ arsip {auc_a.get(m)}")
        elif _cmp(auc_a[m], auc_b[m], TOL_AUC):
            continue
        elif m == "RS" and naik and abs(auc_a[m] - auc_b[m]) <= TOL_AUC_RS:
            warn_auc_rs = (f"AUC RS {auc_b[m]:.6f} ≠ arsip {auc_a[m]:.6f} (Δ {abs(auc_a[m] - auc_b[m]):.5f} ≤ "
                           f"{TOL_AUC_RS}) — sinyal D berbeda pada {naik} konsesi karena jendela pasca 2024 vs 2025; "
                           "model R & peluang_akhir tetap identik dengan arsip")
        else:
            beda.append(f"AUC {m}: {auc_b.get(m)} ≠ arsip {auc_a.get(m)}")
    n_beda_p = satu(con, """SELECT COUNT(*) FROM keyakinan_pra_izin b JOIN a.keyakinan_pra_izin x USING (kode_wiup)
                            WHERE ABS(COALESCE(b.peluang_akhir,-1) - COALESCE(x.peluang_akhir,-1)) > 1e-6""")
    n_b = satu(con, "SELECT COUNT(*) FROM keyakinan_pra_izin"); n_a = satu(con, "SELECT COUNT(*) FROM a.keyakinan_pra_izin")
    if n_a != n_b or n_beda_p:
        beda.append(f"peluang_akhir per konsesi: n {n_b}≠{n_a} / {n_beda_p} konsesi berbeda dari arsip")
    (lap.fail if beda else lap.ok)("paritas-keyakinan", "; ".join(beda) if beda else
        f"harapan {float(rb['hilang_harapan_ha']):,.2f} ha, batas [{float(rb['hilang_batas_bawah_ha']):,.2f}–"
        f"{float(rb['hilang_batas_atas_ha']):,.2f}], AUC {', '.join(f'{m} {x:.3f}' for m, x in sorted(auc_b.items()))}, "
        f"peluang {n_b} konsesi = arsip")
    if warn_auc_rs:
        lap.warn("paritas-keyakinan-auc-rs", warn_auc_rs)
    try:
        lo_b, hi_b = float(rb["hilang_bootstrap_lo_ha"]), float(rb["hilang_bootstrap_hi_ha"])
        lo_a, hi_a = float(ra["loss_bootstrap_lo_ha"]), float(ra["loss_bootstrap_hi_ha"])
        lo_v, hi_v = JANGKAR_V3_BOOTSTRAP
        if _cmp(lo_b, lo_v, TOL_AGREGAT) and _cmp(hi_b, hi_v, TOL_AGREGAT):
            lap.ok("paritas-keyakinan-bootstrap", f"selang [{lo_b:,.2f}–{hi_b:,.2f}] = jangkar v3 (arsip [{lo_a:,.2f}–{hi_a:,.2f}]; "
                                                  "beda karena urutan iterasi konsesi, SKEMA §7)")
        elif _cmp(lo_b, lo_a, TOL_AGREGAT) and _cmp(hi_b, hi_a, TOL_AGREGAT):
            lap.ok("paritas-keyakinan-bootstrap", f"selang [{lo_b:,.2f}–{hi_b:,.2f}] = arsip")
        elif abs(lo_b - lo_a) < TOL_BOOTSTRAP and abs(hi_b - hi_a) < TOL_BOOTSTRAP:
            lap.warn("paritas-keyakinan-bootstrap", f"selang [{lo_b:,.2f}–{hi_b:,.2f}] ≠ jangkar v3 [{lo_v:,.2f}–{hi_v:,.2f}] "
                                                    f"tapi < {TOL_BOOTSTRAP:,.0f} ha dari arsip [{lo_a:,.2f}–{hi_a:,.2f}]")
        else:
            lap.fail("paritas-keyakinan-bootstrap", f"selang [{lo_b:,.2f}–{hi_b:,.2f}] menjauh > {TOL_BOOTSTRAP:,.0f} ha dari arsip "
                                                    f"[{lo_a:,.2f}–{hi_a:,.2f}] (jangkar v3 [{lo_v:,.2f}–{hi_v:,.2f}])")
    except (KeyError, ValueError) as e:
        lap.fail("paritas-keyakinan-bootstrap", f"kunci/nilai bootstrap: {e!r}")

    # §6 umur (kedua rancangan; jangkar utama A_seimbang)
    ua = {(r, k): (n, l) for r, k, n, l in con.execute("SELECT rancangan, kurun, n_konsesi, laju_bahaya_per_tahun FROM a.umur_izin_kurun")}
    ub = {(r, k): (n, l) for r, k, n, l in con.execute("SELECT rancangan, kurun, n_konsesi, laju_bahaya_per_tahun FROM umur_izin_kurun")}
    beda = [f"{r}/{k}" for (r, k) in set(ua) | set(ub)
            if (r, k) not in ua or (r, k) not in ub or ua[(r, k)][0] != ub[(r, k)][0] or not _cmp(ua[(r, k)][1], ub[(r, k)][1], TOL_LAJU)]
    a_ = [f"{k} n={n} {100 * (l or 0):.3f}%" for (r, k), (n, l) in sorted(ub.items()) if r == "A_seimbang"]
    (lap.fail if beda else lap.ok)("paritas-umur", ("kurun berbeda dari arsip: " + ", ".join(sorted(beda))) if beda else
        "A_seimbang = arsip: " + " → ".join(a_))

    # §4 kohort transisi = jangkar v3 (bukan arsip — lihat JANGKAR_V3_KOHORT)
    kohort = {l: (n, k) for l, n, k in con.execute("SELECT label, n_konsesi, n_keluar FROM transisi_kohort")}
    beda = [f"{l}: {kohort.get(l)} ≠ v3 {v}" for l, v in JANGKAR_V3_KOHORT.items() if kohort.get(l) != v]
    beda += [f"{l}: label tak dikenal" for l in kohort if l not in JANGKAR_V3_KOHORT]
    (lap.fail if beda else lap.ok)("paritas-kohort", "; ".join(beda) if beda else
        "transisi_kohort = jangkar v3: " + ", ".join(f"{l} {n}/{k}" for l, (n, k) in sorted(kohort.items())))

    # §4 Sankey & MapBiomas tahunan
    if arsip_m is None:
        lap.warn("paritas-sankey", "--arsip-mapbiomas tak diberikan/tak ditemukan — paritas MapBiomas dilewati")
    else:
        con.execute("ATTACH DATABASE ? AS m", (f"file:{arsip_m}?mode=ro",))
        sa_ = con.execute("SELECT SUM(ha), SUM(n_konsesi) FROM m.v_transisi_agregat WHERE label=? AND kelas_awal<>kelas_akhir", (LABEL_SANKEY,)).fetchone()
        sb_ = con.execute("SELECT SUM(ha), SUM(n_konsesi) FROM v_transisi_aliran WHERE label=? AND kelas_awal<>kelas_akhir", (LABEL_SANKEY,)).fetchone()
        if _cmp(sa_[0], sb_[0], TOL_AGREGAT):
            lap.ok("paritas-sankey", f"total pita berubah {LABEL_SANKEY} {sb_[0]:,.2f} ha = arsip v_transisi_agregat")
        else:
            lap.fail("paritas-sankey", f"total pita berubah {LABEL_SANKEY} {sb_[0] or 0:,.2f} ≠ arsip {sa_[0] or 0:,.2f}")
        ta = {y: h for y, h in con.execute("SELECT year, SUM(ha) FROM m.landuse_konsesi WHERE year BETWEEN ? AND ? GROUP BY year", (S.TAHUN_AWAL, S.TAHUN_AKHIR))}
        tb = {y: h for y, h in con.execute("SELECT tahun, SUM(ha) FROM mapbiomas_tahunan GROUP BY tahun")}
        beda = [f"{y}: {tb.get(y, 0):,.2f}≠{ta.get(y, 0):,.2f}" for y in sorted(set(ta) | set(tb)) if not _cmp(ta.get(y), tb.get(y), TOL_AGREGAT)]
        (lap.fail if beda else lap.ok)("paritas-mapbiomas-tahunan",
            ("Σ ha per tahun ≠ arsip landuse_konsesi: " + _ringkas_daftar(beda)) if beda else
            f"Σ ha per tahun {S.TAHUN_AWAL}–{S.TAHUN_AKHIR} = arsip landuse_konsesi ({len(tb)} tahun; {S.TAHUN_AKHIR}: {tb.get(S.TAHUN_AKHIR, 0):,.2f} ha)")
        con.execute("DETACH DATABASE m")
    con.execute("DETACH DATABASE a")


def cek_paritas_lengkap(con, lap, arsip: Path):
    con.execute("ATTACH DATABASE ? AS a", (f"file:{arsip}?mode=ro",))
    n_a = satu(con, "SELECT COUNT(*) FROM a.wiup_geoportal")
    n_b = satu(con, "SELECT COUNT(*) FROM konsesi")
    hil_a = satu(con, "SELECT SUM(loss_2001_2024_ha) FROM a.wiup_loss")
    hil_b = satu(con, "SELECT SUM(hilang_2001_2024_ha) FROM hansen_ringkas")
    con.execute("DETACH DATABASE a")
    if n_a != n_b or not _cmp(hil_a, hil_b, TOL_AGREGAT):
        lap.fail("paritas-hansen", f"lengkap: n {n_b}≠{n_a} / Σ hilang {hil_b or 0:,.2f}≠{hil_a or 0:,.2f} (arsip {arsip})")
    else:
        lap.ok("paritas-hansen", f"lengkap: n={n_b}, Σ hilang 2001–2024 {hil_b:,.2f} ha = arsip {arsip.name}")


# ── main ─────────────────────────────────────────────────────────────────────────────────────

def jalankan(db: Path, himpunan: str, stats: Path | None, arsip: Path | None, arsip_mapbiomas: Path | None,
             tulis=print) -> Pelapor:
    if not db.is_file():
        gagal(f"DB tidak ada: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    wajib_tabel(con, *TABEL_INTI)          # gagal keras (exit 2), bukan WARN
    lap = Pelapor(tulis)
    tulis(f"10_verifikasi — {db} (himpunan {himpunan})")
    cek_skema(con, lap)
    cek_meta(con, lap)
    cek_bangun(con, lap, himpunan)
    cek_sumber(con, lap)
    cek_kartu_metodologi(lap)
    cek_rujukan(con, lap)
    cek_hansen(con, lap)
    cek_izin_laju(con, lap)
    cek_hash(con, lap)
    cek_transisi(con, lap)
    cek_umur(con, lap)
    cek_keyakinan(con, lap)
    if stats is not None:
        cek_stats(con, lap, stats, himpunan)
    else:
        lap.warn("stats-web", "--stats tak diberikan — kesegaran dashboard-stats.json tak diperiksa")

    if himpunan == "minerba":
        if arsip is not None:
            p = cari_arsip(arsip)
            pm = cari_arsip(arsip_mapbiomas) if arsip_mapbiomas is not None else None
            if p is None:
                lap.fail("paritas-arsip", f"--arsip {arsip} tidak ditemukan (dicoba juga data/arsip/ & data/)")
            else:
                if arsip_mapbiomas is not None and pm is None:
                    lap.fail("paritas-arsip", f"--arsip-mapbiomas {arsip_mapbiomas} tidak ditemukan")
                    pm = None
                cek_paritas_minerba(con, lap, p, pm)
        else:
            tulis("  (paritas arsip dilewati: --arsip tak diberikan)")
    else:
        # fallback ke lokasi baku HANYA bila --arsip tak diberikan; path eksplisit yang tak ada = FAIL
        p = cari_arsip(arsip) if arsip is not None else \
            cari_arsip(None, AKAR / "data/arsip/kalimantan-lengkap.db", AKAR / "data-full/kalimantan.db")
        if p is None:
            if arsip is not None:
                lap.fail("paritas-arsip", f"--arsip {arsip} tidak ditemukan")
            else:
                tulis("  (paritas lengkap dilewati: arsip kalimantan-lengkap.db tak ditemukan)")
        else:
            cek_paritas_lengkap(con, lap, p)
    con.close()
    return lap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--himpunan", choices=HIMPUNAN, required=True)
    ap.add_argument("--stats", type=Path, default=None, help="dashboard-stats.json yang ditulis 09_sajikan.py")
    ap.add_argument("--arsip", type=Path, default=None, help="kalimantan.db arsip (minerba) / data-full (lengkap)")
    ap.add_argument("--arsip-mapbiomas", type=Path, default=None, help="mapbiomas.db arsip (hanya minerba)")
    a = ap.parse_args()
    lap = jalankan(a.db, a.himpunan, a.stats, a.arsip, a.arsip_mapbiomas)
    return lap.ringkas()


if __name__ == "__main__":
    raise SystemExit(main())
