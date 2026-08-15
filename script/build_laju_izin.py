#!/usr/bin/env python3
"""Bangun tabel LAJU DEFORESTASI PER JAM IZIN — pivot "bukti lapangan" (12 Agu r2).

Tiap konsesi punya jam yang MULAI DARI BUKTI, bukan asumsi kelas izin (aturan E,
menggantikan aturan B sebagai jam utama):

  mulai = min( tahun-bukti, max(2009, iup_year) )
  tahun-bukti = tahun PERTAMA di [2001, 2021] dgn kehilangan NON-SAWIT
(`mulai` lalu diklem ke >= 2009 — jendela hitung era Minerba)
                (Hansen − Descals tol2th) >= AMBANG_BUKTI_HA. Deteksi berhenti
                2021 (batas peta Descals — 2022-2025 tak bisa diverifikasi
                bukan-sawit, jadi tak pernah MENCIPTAKAN backtrack; konservatif).
  Konsesi ber-izin 2026/tanpa-tahun tetap MASUK bila ada bukti; keluar kohort
  hanya bila tak ada bukti DAN tak ada tahun izin dalam jendela.

Jadi backtrack tak lagi "karena dia perpanjangan", melainkan "karena di poligon
itu SUDAH ADA pembukaan non-sawit sebelum SK-nya". dasar_mulai mencatat mana
yang menang: 'BUKTI' (backtrack/bukti lebih dulu) atau 'IZIN'.

Laju dihitung DUA BASIS yang tak pernah dicampur:

  bersih  (UTAMA — Descals first-class): Σ max(0, loss − sawit_tol2th),
          tahun ∈ [mulai, 2021]; NULL bila mulai > 2021 / lapisan sawit absen.
  kotor   (pendamping jangkauan penuh): Σ loss Hansen, tahun ∈ [mulai, 2025].

Tiga metrik (keputusan user 12 Agu 2026):
  (a) laju ha/tahun   = loss_basis / tahun_aktif_basis
  (b) laju %/tahun    = 100 · laju_ha_thn / hutan_mulai_aktif_ha
                        (hutan_mulai = forest_2000 − Σ loss 2001..mulai−1)
  (c) event-study     = loss per tahun-relatif-izin (rel_year = tahun − iup_year),
                        per kelas izin; t=0 PERPANJANGAN = SK perpanjangan
                        (sisi pra tercemar — kurva bersih-tafsir = IZIN_PERTAMA).
                        Event-study TETAP berjam iup_year (mengukur efek SK),
                        terpisah dari jam bukti di tabel laju.

Tabel: laju_izin_konsesi (825, kohort = mulai NOT NULL), laju_izin_ringkas
(distribusi basis × dimensi × kelompok), laju_izin_eventstudy (kelas × rel_year,
kohort iup_year 2009-2025). Kolom jendela era Minerba (loss_2009_2025_ha dst)
kini hidup di wiup_loss (dihitung ingest build_combined_db) — tabel
jendela_2009 lama di-drop di sini bila masih ada. Provenance/kamus kolom ditulis build_periode_tables.py
(jalan SETELAH skrip ini di process.sh). Idempotent, stdlib saja.

    python3 scripts/build_laju_izin.py --db data/kalimantan.db
"""
from __future__ import annotations

import argparse
import sqlite3

JENDELA_MIN, JENDELA_MAX = 2009, 2025
BATAS_DESCALS = 2021          # batas keras peta Descals — 2022-2025 tak terperiksa
BUKTI_MIN = 2001              # bukti pembukaan dicari sejak awal Hansen (lihat
                              # hitung_mulai_bukti: jendela BUKTI != jendela HITUNG)
AMBANG_BUKTI_HA = 1.0         # bukti pembukaan = loss non-sawit >= 1 ha dlm setahun
                              # (~11 piksel Hansen 30 m — di atas derau piksel tunggal)
REL_MIN, REL_MAX = -10, 16    # jangkauan rel_year event-study


# to_periode diimpor dari modul kanonik — dulu replika hardcode di sini
# (temuan audit 15 Agu: risiko drift senyap antar 4 salinan).
from build_periode_tables import to_periode  # noqa: E402


def hitung_mulai_bukti(iup_year, loss_th, sawit_th, ada_sawit):
    """(mulai, dasar, tahun_bukti) aturan E — jam bukti lapangan.

    DUA JENDELA BERBEDA, jangan disatukan:
      • jendela BUKTI = [BUKTI_MIN=2001, BATAS_DESCALS=2021]. Kita mencari
        kapan lahan mulai dibuka, dan pembukaan tahun 2003 sama sahihnya sbg
        bukti "tambangnya sudah ada" dengan pembukaan tahun 2012 — SK ber-tahun
        2017 di poligon yang sudah terbuka sejak 2003 hampir pasti perpanjangan.
        Sebelumnya jendela ini keliru dipatok mulai 2009, sehingga konsesi yang
        buktinya HANYA di 2001-2008 jatuh kembali ke tahun SK-nya dan kehilangan
        2009..SK-nya tak terhitung (temuan igoen 13 Agu — underestimate).
      • jendela HITUNG = mulai 2009 (JENDELA_MIN), karena kerangka analisis ini
        era UU Minerba. Karena itu `mulai` DIKLEM ke >= 2009: bukti 2003 berarti
        "sudah aktif sebelum jendela kita", jadi dihitung sejak 2009 — bukan
        sejak 2003 (itu akan mencampur pra-Minerba ke dalam angka era Minerba).

    tahun_bukti dikembalikan APA ADANYA (boleh 2001-2008) supaya alasan di balik
    `mulai` tetap terbaca; yang diklem hanya `mulai`. Tanpa lapisan sawit
    (ada_sawit=False) bukti memakai Hansen mentah (degradasi, diberi peringatan
    pemanggil). izin = max(2009, iup_year) hanya bila iup_year <= JENDELA_MAX.
    """
    bukti = None
    for y in range(BUKTI_MIN, BATAS_DESCALS + 1):
        bersih = loss_th.get(y, 0.0) - (sawit_th.get(y, 0.0) if ada_sawit else 0.0)
        if bersih >= AMBANG_BUKTI_HA:
            bukti = y
            break
    bukti_klem = max(JENDELA_MIN, bukti) if bukti is not None else None
    izin = max(JENDELA_MIN, iup_year) if (iup_year is not None and iup_year <= JENDELA_MAX) else None
    kandidat = [x for x in (bukti_klem, izin) if x is not None]
    if not kandidat:
        return None, None, bukti
    mulai = min(kandidat)
    dasar = "BUKTI" if (bukti_klem is not None and (izin is None or bukti_klem < izin)) else "IZIN"
    return mulai, dasar, bukti


def pctl(sorted_vals, q):
    """Persentil interpolasi linier (q dalam [0,100]); None utk daftar kosong."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def hitung_laju(mulai, forest_2000, loss_th, sawit_th, ada_sawit):
    """Semua kolom laju satu konsesi. loss_th/sawit_th: dict tahun→ha (2001-2025).

    Kembalian: (hutan_mulai, tk, loss_k, laju_k_ha, laju_k_pct,
                tb, loss_b, laju_b_ha, laju_b_pct) — None utk yang tak terdefinisi.
    """
    if mulai is None:
        return (None,) * 9
    hutan_mulai = forest_2000 - sum(v for y, v in loss_th.items() if y < mulai)
    tk = JENDELA_MAX - mulai + 1
    loss_k = sum(v for y, v in loss_th.items() if mulai <= y <= JENDELA_MAX)
    laju_k_ha = loss_k / tk
    laju_k_pct = 100.0 * laju_k_ha / hutan_mulai if hutan_mulai > 0 else None
    if not ada_sawit or mulai > BATAS_DESCALS:
        return (hutan_mulai, tk, loss_k, laju_k_ha, laju_k_pct,
                None, None, None, None)
    tb = BATAS_DESCALS - mulai + 1
    loss_b = sum(max(0.0, loss_th.get(y, 0.0) - sawit_th.get(y, 0.0))
                 for y in range(mulai, BATAS_DESCALS + 1))
    laju_b_ha = loss_b / tb
    laju_b_pct = 100.0 * laju_b_ha / hutan_mulai if hutan_mulai > 0 else None
    return (hutan_mulai, tk, loss_k, laju_k_ha, laju_k_pct,
            tb, loss_b, laju_b_ha, laju_b_pct)


def build_backtrack_tables(con, rows, loss_th, sawit_th, f2000, ada_sawit):
    """Tabel pembanding 3 metode backtrack — kunci kolom `aturan`.

    aturan ∈ {'CITRA','INDIKASI','POLOS'}; tahun mulai per konsesi:
      CITRA     = laju_izin_konsesi.mulai        (aturan E — bukti citra; UTAMA)
      INDIKASI  = atribusi_izin_aktif.mulai_b    (perpanjangan→2009, lain→max(2009,iup))
      POLOS     = atribusi_izin_aktif.mulai_d    (tanpa backtrack — max(2009, tahun SK))
    Jendela hitung SEMUA metode: [mulai, 2025] (tanpa-sawit: [mulai, 2021]).
    Kolom bernama `..._mulai_aktif_...` merujuk tahun mulai aktif VERSI `aturan`
    di baris yang sama — penanda jangkarnya ya kolom aturan itu (konvensi
    penamaan DECISIONS 13 Agu). Baris CITRA WAJIB identik dgn tabel utama (invarian
    cek_backtrack) — tabel ini pembanding, bukan sumber kebenaran baru.

    Bila atribusi_izin_aktif absen, hanya baris CITRA yang dibangun (UI wajib
    menoleransi metode yang hilang).
    """
    # gini/med didefinisikan DI ATAS fungsi (dulu di dalam blok
    # backtrack_distribusi) karena kini dipakai DUA blok: distribusi DAN
    # periode_kalender (statistik luas wilayah aktif, igoen 15 Agu malam).
    # Gini: rumus selisih-berpasangan atas nilai >= 0, None bila n<2 / Σ=0.
    def gini(vals):
        v = sorted(x for x in vals if x is not None and x >= 0)
        n = len(v)
        tot = sum(v)
        if n < 2 or tot <= 0:
            return None
        kum = 0.0
        for i, x in enumerate(v, start=1):
            kum += i * x
        return round((2.0 * kum) / (n * tot) - (n + 1.0) / n, 4)

    def med(vals):
        v = sorted(vals)
        if not v:
            return None
        m = len(v) // 2
        return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0

    mulai_of = {"CITRA": dict(con.execute(
        "SELECT kode_wiup, mulai FROM laju_izin_konsesi"))}
    ada_atr = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='atribusi_izin_aktif'").fetchone() is not None
    if ada_atr:
        b, d = {}, {}
        for kode, mb, md in con.execute(
                "SELECT kode_wiup, mulai_b, mulai_d FROM atribusi_izin_aktif"):
            b[kode] = mb
            d[kode] = md
        mulai_of["INDIKASI"] = b
        # PERKIRAAN (aturan C) PENSIUN dari backtrack_* (igoen 15 Agu): dgn cara
        # baca aditif yang benar (perpanjangan MENAMBAH waktu setelah izin asal
        # habis), taksiran izin asal = iup_year − 20 selalu < 2009 → terklem →
        # C ≡ B utk seluruh 525 perpanjangan. Rumus jangkar-akhir lama
        # (iup+durasi−20) memakai asumsi kontinuitas-horizon yang ditolak.
        # Data aturan C (mulai_c/loss_mulai_c_sampai_2025_ha) TETAP di atribusi_izin_aktif sbg
        # arsip — tak dibaca di sini.
        # POLOS = tanpa backtrack apa pun: murni max(2009, tahun SK) — aturan D.
        mulai_of["POLOS"] = d
    else:
        print("PERINGATAN: atribusi_izin_aktif absen — backtrack_* hanya baris CITRA.")

    geo = {kode: (iy, luas, pejabat, komo) for kode, iy, luas, pejabat, komo in con.execute(
        "SELECT kode_wiup, iup_year, COALESCE(luas_sk,0), pejabat, komoditas "
        "FROM wiup_geoportal")}
    poly = dict(con.execute(
        "SELECT kode_wiup, COALESCE(polygon_area_ha,0) FROM wiup_loss"))
    kelas_of = {kode: kelas for kode, kelas, _b, _iy in rows}
    kode_semua = [r[0] for r in rows]

    def loss_jendela(kode, mulai, y_akhir):
        return sum(v for y, v in loss_th.get(kode, {}).items() if mulai <= y <= y_akhir)

    def tanpa_sawit_jendela(kode, mulai):
        th, sw = loss_th.get(kode, {}), sawit_th.get(kode, {})
        return sum(max(0.0, v - sw.get(y, 0.0))
                   for y, v in th.items() if mulai <= y <= BATAS_DESCALS)

    def grup_komo(komo):
        return "BATUBARA" if (komo or "").upper().startswith("BATUBARA") else "MINERAL LOGAM"

    # ── backtrack_tahunan: flow loss + jumlah aktif per tahun per aturan ──────
    con.execute("DROP TABLE IF EXISTS backtrack_tahunan")
    con.execute("""CREATE TABLE backtrack_tahunan (
        aturan TEXT, year INTEGER,
        n_aktif INTEGER, n_sk_terbit INTEGER, n_aktif_sebelum_sk INTEGER,
        loss_ha REAL, loss_tanpa_sawit_ha REAL,
        hutan_awal_tahun_ha REAL,
        PRIMARY KEY (aturan, year))""")
    for aturan, m_of in mulai_of.items():
        for y in range(2001, JENDELA_MAX + 1):
            n_sk = sum(1 for kode in kode_semua
                       if geo.get(kode, (None,))[0] is not None and geo[kode][0] <= y)
            if y < JENDELA_MIN:
                con.execute("INSERT INTO backtrack_tahunan VALUES (?,?,?,?,?,?,?,?)",
                            (aturan, y, None, n_sk, None, None, None, None))
                continue
            aktif = [kode for kode in kode_semua
                     if m_of.get(kode) is not None and m_of[kode] <= y]
            n_seb = sum(1 for kode in aktif
                        if geo.get(kode, (None,))[0] is None or geo[kode][0] > y)
            fl = sum(loss_th.get(kode, {}).get(y, 0.0) for kode in aktif)
            if ada_sawit and y <= BATAS_DESCALS:
                fb = sum(max(0.0, loss_th.get(kode, {}).get(y, 0.0)
                             - sawit_th.get(kode, {}).get(y, 0.0)) for kode in aktif)
            else:
                fb = None
            # Stok hutan yang masih berdiri AWAL tahun ini di konsesi aktif —
            # penyebut laju %/tahun (grafik per-tahun blok 2, igoen 15 Agu):
            # forest_2000 dikurangi seluruh loss 2001..y-1 (Hansen penuh; stok
            # fisik pohon, bukan varian tanpa-sawit).
            stok = sum(f2000.get(kode, 0.0)
                       - sum(v for yy, v in loss_th.get(kode, {}).items() if yy < y)
                       for kode in aktif)
            con.execute("INSERT INTO backtrack_tahunan VALUES (?,?,?,?,?,?,?,?)",
                        (aturan, y, len(aktif), n_sk, n_seb, round(fl, 2),
                         None if fb is None else round(fb, 2), round(stok, 2)))

    # ── backtrack_periode_kalender: REDEFINISI periode (igoen 15 Agu) ─────────
    # P1/P2/P3 = jendela TAHUN KALENDER murni (2009-2014 / 2015-2019 /
    # 2020-2025), BUKAN kohort tahun-terbit-SK. Loss satu jendela = Σ flow
    # backtrack_tahunan tahun-tahun itu dari konsesi yang AKTIF versi `aturan`
    # — dengan begitu klaim "kehilangan PADA rentang ini" lurus, dan batas
    # Descals (2021) memang hanya menggigit P3 (kolom loss_2022_2025_belum_terperiksa_ha).
    # Kohort-SK TIDAK dibuang: tetap hidup di backtrack_periode dkk. dan tampil
    # di UI sebagai seksi terpisah "SK yang terbit pada rentang ini".
    # Dihitung QUERY-BALIK atas baris backtrack_tahunan yang BARU ditulis di
    # atas (bukan hitung ulang dari loss_th) supaya rekonsiliasi jendela-vs-
    # tahunan eksak by construction (invarian backtrack-kalender-rekonsil).
    con.execute("DROP TABLE IF EXISTS backtrack_periode_kalender")
    # Nama kolom tanpa-sawit/belum-terperiksa menyebut jendelanya (rename 15
    # Agu): batas Descals 2021 & sisa 2022-2025 adalah ujung jendela TETAP —
    # masuk NAMA kolom (DECISIONS 13 Agu), bukan pengetahuan tersirat pembaca.
    con.execute("""CREATE TABLE backtrack_periode_kalender (
        aturan TEXT, periode TEXT, tahun_awal INTEGER, tahun_akhir INTEGER,
        loss_ha REAL, loss_tanpa_sawit_sampai_2021_ha REAL,
        loss_2022_2025_belum_terperiksa_ha REAL,
        n_aktif_akhir INTEGER,
        luas_aktif_total_ha REAL, mean_luas_aktif_ha REAL,
        median_luas_aktif_ha REAL, gini_luas_aktif REAL,
        PRIMARY KEY (aturan, periode))""")
    JENDELA_KALENDER = (("P1", 2009, 2014), ("P2", 2015, 2019),
                        ("P3", 2020, JENDELA_MAX))
    for aturan, m_of in mulai_of.items():
        for pp, awal, akhir in JENDELA_KALENDER:
            l_jendela = con.execute(
                "SELECT SUM(loss_ha) FROM backtrack_tahunan "
                "WHERE aturan=? AND year BETWEEN ? AND ?",
                (aturan, awal, akhir)).fetchone()[0]
            # Varian tanpa-sawit hanya terdefinisi s.d. 2021 (batas peta
            # Descals): jumlahkan hanya tahun terperiksa [awal, min(akhir,
            # 2021)]. Bila SELURUH jendela > 2021 tak ada tahun terperiksa →
            # NULL (tak terjadi utk P1-P3; guard tetap dipasang supaya definisi
            # kolom tak bergantung pada pilihan jendela saat ini). NULL juga
            # muncul alami saat lapisan sawit absen (SUM atas NULL semua).
            akhir_periksa = min(akhir, BATAS_DESCALS)
            if awal <= akhir_periksa:
                ts_jendela = con.execute(
                    "SELECT SUM(loss_tanpa_sawit_ha) FROM backtrack_tahunan "
                    "WHERE aturan=? AND year BETWEEN ? AND ?",
                    (aturan, awal, akhir_periksa)).fetchone()[0]
            else:
                ts_jendela = None
            # Bagian jendela di atas batas Descals (2022-2025): loss Hansen
            # yang TAK BISA diperiksa sawit — 0 (bukan NULL) utk P1/P2 karena
            # seluruh rentangnya memang terperiksa, tak ada yang "belum".
            if akhir > BATAS_DESCALS:
                belum = con.execute(
                    "SELECT COALESCE(SUM(loss_ha),0) FROM backtrack_tahunan "
                    "WHERE aturan=? AND year BETWEEN ? AND ?",
                    (aturan, max(awal, BATAS_DESCALS + 1), akhir)).fetchone()[0]
            else:
                belum = 0.0
            n_akhir = con.execute(
                "SELECT n_aktif FROM backtrack_tahunan WHERE aturan=? AND year=?",
                (aturan, akhir)).fetchone()[0]
            # Statistik LUAS wilayah aktif ikut metode backtrack (igoen 15 Agu
            # malam): bukan "SK terbit pada rentang" (statis), melainkan
            # himpunan KUMULATIF {konsesi dgn mulai versi aturan <= tahun_akhir}
            # — aktif KAPAN PUN s.d. akhir jendela, predikat yang sama dgn
            # n_aktif_akhir di atas. Konsekuensi yang DISENGAJA: di POLOS
            # himpunan ini ≈ kohort SK, sedangkan di CITRA hampir identik antar
            # jendela (hampir semua sudah aktif sejak 2009) — itu temuan
            # (backtrack meratakan perbedaan antar periode), bukan bug.
            luas_aktif = [geo[k][1] for k in kode_semua
                          if m_of.get(k) is not None and m_of[k] <= akhir]
            n_luas = len(luas_aktif)
            tot_luas = sum(luas_aktif)
            med_luas = med(luas_aktif)
            con.execute(
                "INSERT INTO backtrack_periode_kalender VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (aturan, pp, awal, akhir,
                 None if l_jendela is None else round(l_jendela, 2),
                 None if ts_jendela is None else round(ts_jendela, 2),
                 round(belum, 2), n_akhir,
                 round(tot_luas, 2),
                 round(tot_luas / n_luas, 2) if n_luas else None,
                 None if med_luas is None else round(med_luas, 2),
                 gini(luas_aktif)))

    # ── backtrack_periode / _komoditas / _klasifikasi: agregat per sel ────────
    # Penanda jangkar 'mulai_aktif' (rename 15 Agu, konvensi DECISIONS 13 Agu):
    # 'mulai' telanjang ambigu — mulai apa? Penanda mulai_aktif menunjuk tahun
    # mulai AKTIF versi kolom `aturan` di baris yang sama (CITRA/INDIKASI/POLOS).
    con.execute("DROP TABLE IF EXISTS backtrack_periode")
    con.execute("""CREATE TABLE backtrack_periode (
        aturan TEXT, periode TEXT, n INTEGER, n_mulai INTEGER,
        loss_mulai_aktif_sampai_2025_ha REAL, loss_mulai_aktif_sampai_2021_ha REAL,
        loss_mulai_aktif_sampai_2021_tanpa_sawit_ha REAL,
        polygon_ha REAL, pct_poligon_mulai_aktif_sampai_2025 REAL, r_luas_loss REAL,
        PRIMARY KEY (aturan, periode))""")
    con.execute("DROP TABLE IF EXISTS backtrack_komoditas")
    con.execute("""CREATE TABLE backtrack_komoditas (
        aturan TEXT, periode TEXT, grup_komoditas TEXT, n INTEGER,
        loss_mulai_aktif_sampai_2025_ha REAL, loss_mulai_aktif_sampai_2021_tanpa_sawit_ha REAL,
        PRIMARY KEY (aturan, periode, grup_komoditas))""")
    con.execute("DROP TABLE IF EXISTS backtrack_klasifikasi")
    con.execute("""CREATE TABLE backtrack_klasifikasi (
        aturan TEXT, periode TEXT, kelas TEXT, n INTEGER,
        loss_mulai_aktif_sampai_2025_ha REAL,
        PRIMARY KEY (aturan, periode, kelas))""")

    def pearson(xs, ys):
        n = len(xs)
        if n < 3:
            return None
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sxx <= 0 or syy <= 0:
            return None
        return sxy / (sxx * syy) ** 0.5

    # Ember TANPA_PERIODE (temuan audit 15 Agu): 11 konsesi kohort (7 tanpa
    # iup_year, 4 iup_year=2026) dulu DIBUANG diam-diam → Σ backtrack_periode
    # kurang 4.165 ha dari laju_izin_ringkas di halaman yang sama. Kini masuk
    # ember sendiri: jumlah rekonsil, UI tinggal tak merender ember ini.
    PERIODES = ("Pra-2009", "P1", "P2", "P3", "TANPA_PERIODE")
    for aturan, m_of in mulai_of.items():
        per_p: dict[str, list] = {pp: [] for pp in PERIODES}
        for kode in kode_semua:
            iy = geo.get(kode, (None,))[0]
            pp = to_periode(iy) or "TANPA_PERIODE"
            per_p[pp].append(kode)
        for pp, anggota in per_p.items():
            # Filter m <= JENDELA_MAX (audit 3.5): mulai_d bisa 2026 (di luar
            # jendela) — tanpa filter ini ia mencemari r_luas_loss sbg titik 0.
            punya = [k for k in anggota
                     if m_of.get(k) is not None and m_of[k] <= JENDELA_MAX]
            l25 = sum(loss_jendela(k, m_of[k], JENDELA_MAX) for k in punya)
            # kotor s.d. 2021 — pembilang dekomposisi kartu: selisih thd l25 =
            # loss 2022-2025 (tak terperiksa); selisih thd tanpa-sawit = sawit.
            l21k = sum(loss_jendela(k, m_of[k], BATAS_DESCALS) for k in punya)
            l21 = sum(tanpa_sawit_jendela(k, m_of[k]) for k in punya) if ada_sawit else None
            pg = sum(poly.get(k, 0.0) for k in anggota)
            r = pearson([geo[k][1] for k in punya],
                        [loss_jendela(k, m_of[k], JENDELA_MAX) for k in punya])
            con.execute("INSERT INTO backtrack_periode VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (aturan, pp, len(anggota), len(punya), round(l25, 2),
                         round(l21k, 2),
                         None if l21 is None else round(l21, 2), round(pg, 2),
                         round(100.0 * l25 / pg, 2) if pg else None,
                         None if r is None else round(r, 3)))
            per_g: dict[str, list] = {}
            for k in punya:
                per_g.setdefault(grup_komo(geo[k][3]), []).append(k)
            for g, ks in sorted(per_g.items()):
                gl25 = sum(loss_jendela(k, m_of[k], JENDELA_MAX) for k in ks)
                gl21 = sum(tanpa_sawit_jendela(k, m_of[k]) for k in ks) if ada_sawit else None
                con.execute("INSERT INTO backtrack_komoditas VALUES (?,?,?,?,?,?)",
                            (aturan, pp, g, len(ks), round(gl25, 2),
                             None if gl21 is None else round(gl21, 2)))
            per_k: dict[str, list] = {}
            for k in punya:
                per_k.setdefault(kelas_of.get(k) or "TAK_DINILAI", []).append(k)
            for kls, ks in sorted(per_k.items()):
                kl25 = sum(loss_jendela(k, m_of[k], JENDELA_MAX) for k in ks)
                con.execute("INSERT INTO backtrack_klasifikasi VALUES (?,?,?,?,?)",
                            (aturan, pp, kls, len(ks), round(kl25, 2)))

    # ── backtrack_stok: akumulasi konsesi AKTIF (bukan izin terbit) ───────────
    con.execute("DROP TABLE IF EXISTS backtrack_stok")
    # loss_kumulatif_sejak_2009_ha (eks loss_kumulatif_ha): akumulasinya mulai
    # 2009 (jendela era Minerba) — beda dari periode_tahunan_aktif yang
    # mengakumulasi sejak 2001; awal akumulasi masuk nama agar tak tertukar.
    con.execute("""CREATE TABLE backtrack_stok (
        aturan TEXT, grup_tipe TEXT, grup TEXT, year INTEGER,
        n_aktif INTEGER, luas_aktif_ha REAL, forest_aktif_ha REAL,
        loss_ha REAL, loss_kumulatif_sejak_2009_ha REAL,
        PRIMARY KEY (aturan, grup_tipe, grup, year))""")
    for aturan, m_of in mulai_of.items():
        for grup_tipe in ("periode", "penerbit"):
            per_g2: dict[str, list] = {}
            for kode in kode_semua:
                iy, _luas, pejabat, _komo = geo.get(kode, (None, 0, None, None))
                kunci = ((to_periode(iy) or "TANPA_PERIODE")
                         if grup_tipe == "periode" else pejabat)
                if kunci is None:
                    continue
                per_g2.setdefault(kunci, []).append(kode)
            for g, ks in per_g2.items():
                kum = 0.0
                for y in range(JENDELA_MIN, JENDELA_MAX + 1):
                    aktif = [k for k in ks if m_of.get(k) is not None and m_of[k] <= y]
                    fl = sum(loss_th.get(k, {}).get(y, 0.0) for k in aktif)
                    kum += fl
                    con.execute("INSERT INTO backtrack_stok VALUES (?,?,?,?,?,?,?,?,?)",
                                (aturan, grup_tipe, g, y, len(aktif),
                                 round(sum(geo[k][1] for k in aktif), 2),
                                 round(sum(f2000.get(k, 0.0) for k in aktif), 2),
                                 round(fl, 2), round(kum, 2)))

    # ── backtrack_sawit: pemeriksaan sawit di jendela [mulai_aktif, 2021] ─────
    # DROP selalu (bersihkan sisa build lama), tapi CREATE hanya bila lapisan
    # sawit ada (item audit 15 Agu): dulu tabel KOSONG ikut tercipta di
    # data-full — cangkang tanpa data menyesatkan pembaca registry (analysis_meta
    # existing-filter lalu mendaftarkannya seolah pemeriksaan sawit pernah
    # dilakukan di DB itu).
    con.execute("DROP TABLE IF EXISTS backtrack_sawit")
    if ada_sawit:
        con.execute("""CREATE TABLE backtrack_sawit (
            aturan TEXT, periode TEXT, n INTEGER,
            loss_mulai_aktif_sampai_2021_ha REAL, loss_sawit_mulai_aktif_sampai_2021_ha REAL,
            loss_mulai_aktif_sampai_2021_tanpa_sawit_ha REAL,
            persen_sawit_mulai_aktif_sampai_2021 REAL,
            PRIMARY KEY (aturan, periode))""")
        for aturan, m_of in mulai_of.items():
            per_p2: dict[str, list] = {pp: [] for pp in PERIODES}
            for kode in kode_semua:
                pp = to_periode(geo.get(kode, (None,))[0]) or "TANPA_PERIODE"
                if m_of.get(kode) is not None and m_of[kode] <= BATAS_DESCALS:
                    per_p2[pp].append(kode)
            for pp, ks in per_p2.items():
                l21 = sum(loss_jendela(k, m_of[k], BATAS_DESCALS) for k in ks)
                sw21 = sum(v for k in ks for y, v in sawit_th.get(k, {}).items()
                           if m_of[k] <= y <= BATAS_DESCALS)
                ts = sum(tanpa_sawit_jendela(k, m_of[k]) for k in ks)
                con.execute("INSERT INTO backtrack_sawit VALUES (?,?,?,?,?,?,?)",
                            (aturan, pp, len(ks), round(l21, 2), round(sw21, 2),
                             round(ts, 2),
                             round(100.0 * sw21 / l21, 2) if l21 else None))

    # ── backtrack_laju_ringkas: persentil laju per aturan (blok Kecepatan) ───
    # Skema = laju_izin_ringkas + kolom aturan. Baris CITRA == laju_izin_ringkas
    # (invarian tak dipasang di sini krn pembulatan _stat_row sama persis —
    # keduanya dihitung fungsi yang sama atas mulai yang sama).
    con.execute("DROP TABLE IF EXISTS backtrack_laju_ringkas")
    con.execute("""CREATE TABLE backtrack_laju_ringkas (
        aturan TEXT, basis TEXT, dimensi TEXT, kelompok TEXT, n INTEGER, n_pct INTEGER,
        total_loss_ha REAL,
        median_ha_thn REAL, mean_ha_thn REAL, p25_ha_thn REAL, p75_ha_thn REAL,
        p90_ha_thn REAL,
        median_pct_thn REAL, mean_pct_thn REAL, p25_pct_thn REAL, p75_pct_thn REAL,
        p90_pct_thn REAL,
        PRIMARY KEY (aturan, basis, dimensi, kelompok))""")
    kelas_map = {kode: kelas for kode, kelas, _b, _iy in rows}
    for aturan, m_of in mulai_of.items():
        pk = []
        for kode in kode_semua:
            m = m_of.get(kode)
            # m > 2025 mustahil utk CITRA (diklem), tapi mulai_c/d ikut iup_year
            # mentah — konsesi ber-SK di luar jendela dilewati (laju tak terdefinisi).
            if m is None or m > JENDELA_MAX:
                continue
            th = loss_th.get(kode, {})
            sw = sawit_th.get(kode, {})
            (_hm, _tk, lk, ljk, ljkp, _tb, lb, ljb, ljbp) = hitung_laju(
                m, f2000.get(kode, 0.0), th, sw, ada_sawit)
            pp = to_periode(geo.get(kode, (None,))[0])
            pk.append((kelas_map.get(kode, "TAK_DINILAI"), pp, ljk, ljkp, ljb, ljbp, lk, lb))
        kel = [("semua", "SEMUA", lambda r: True)]
        for kls in sorted({r[0] for r in pk}):
            kel.append(("kelas", kls, lambda r, k=kls: r[0] == k))
        for pp in ("P1", "P2", "P3"):
            kel.append(("periode", pp, lambda r, p2=pp: r[1] == p2))
        for dimensi, nama, kunci in kel:
            anggota = [r for r in pk if kunci(r)]
            isi = [("kotor", [r[2] for r in anggota], [r[3] for r in anggota],
                    sum(r[6] for r in anggota))]
            bb = [r for r in anggota if r[4] is not None]
            isi.append(("bersih", [r[4] for r in bb], [r[5] for r in bb],
                        sum(r[7] for r in bb)))
            for basis, vha, vpct, tot in isi:
                con.execute(
                    "INSERT INTO backtrack_laju_ringkas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aturan, basis, dimensi, nama) + _stat_row(vha, vpct, tot))

    # ── backtrack_distribusi: mean/median/gini per metrik ukuran ─────────────
    # Blok 4-5 /era. metrik:
    #   luas_sk               = luas SK konsesi (ha)
    #   luas_sk_tanpa_sawit   = luas_sk − kehilangan berujung sawit 2001-2021
    #                           di konsesi itu (satu-satunya ukuran sawit yang
    #                           kita punya; definisi dicatat utk konfirmasi igoen)
    #   ditambang             = loss Hansen DALAM jendela kelompok (lihat bawah)
    #   ditambang_tanpa_sawit = Σ max(0, loss−sawit) pada bagian jendela yang
    #                           terperiksa Descals (≤ 2021)
    # (gini/med kini didefinisikan di atas fungsi — dipakai juga blok kalender.)
    sawit_tot = {kode: sum(sw.values()) for kode, sw in sawit_th.items()}
    con.execute("DROP TABLE IF EXISTS backtrack_distribusi")
    con.execute("""CREATE TABLE backtrack_distribusi (
        aturan TEXT, metrik TEXT, kelompok TEXT, n INTEGER,
        mean_ha REAL, median_ha REAL, gini REAL,
        PRIMARY KEY (aturan, metrik, kelompok))""")
    for aturan, m_of in mulai_of.items():
        anggota_all = [k for k in kode_semua
                       if m_of.get(k) is not None and m_of[k] <= JENDELA_MAX]
        # REDEFINISI kelompok P1/P2/P3 (igoen 15 Agu malam): dulu kohort
        # iup_year ("SK terbit pada rentang"), kini KUMULATIF "aktif kapan pun
        # s.d. akhir jendela" (mulai versi aturan <= 2014/2019/2025) —
        # konsisten dgn backtrack_periode_kalender & seksi WILAYAH AKTIF di
        # kartu /era. Konsekuensinya himpunan P3 == SEMUA (keduanya mulai <=
        # 2025); yang membedakan barisnya adalah JENDELA metrik ditambang:
        #   P1/P2/P3 = loss DALAM jendela kalender itu (Σ loss tahun ∈
        #              [tahun_awal, tahun_akhir] per konsesi — konsep
        #              jendela-kalender, sama dgn loss_ha kalender);
        #   SEMUA    = definisi lama "sejak mulai" ([mulai, 2025] /
        #              tanpa-sawit [mulai, 2021]).
        # Framing dokumen (kohort SK murni) tetap tersedia lewat pil POLOS
        # (mulai POLOS = max(2009, tahun SK), tanpa backtrack).
        kel = [("SEMUA", anggota_all, None)]
        for pp, awal, akhir in JENDELA_KALENDER:
            kel.append((pp, [k for k in kode_semua
                             if m_of.get(k) is not None and m_of[k] <= akhir],
                        (awal, akhir)))
        for kelompok, ks, jendela in kel:
            if jendela is None:
                dit = [loss_jendela(k, m_of[k], JENDELA_MAX) for k in ks]
                dit_ts = ([tanpa_sawit_jendela(k, m_of[k]) for k in ks]
                          if ada_sawit else None)
            else:
                awal, akhir = jendela
                # Jendela per konsesi DIKLEM ke tahun mulainya (koreksi 15 Agu
                # malam-2b): konsesi POLOS ber-SK 2024 hanya dihitung sejak
                # 2024, BUKAN sejak awal jendela 2020 — tanpa klem ini metrik
                # ditambang nyaris identik antar metode (hanya beda keanggotaan)
                # dan saklar terasa mati.
                dit = [loss_jendela(k, max(awal, m_of[k]), akhir) for k in ks]
                # Varian tanpa-sawit hanya utk bagian jendela yang terperiksa
                # Descals: [max(awal, mulai), min(akhir, 2021)] — P1/P2
                # terperiksa penuh, P3 hanya 2020-2021.
                a2 = min(akhir, BATAS_DESCALS)
                dit_ts = ([sum(max(0.0, loss_th.get(k, {}).get(y, 0.0)
                                   - sawit_th.get(k, {}).get(y, 0.0))
                               for y in range(max(awal, m_of[k]), a2 + 1)) for k in ks]
                          if ada_sawit else None)
            metrik_vals = {
                "luas_sk": [geo[k][1] for k in ks],
                # Tanpa lapisan sawit metrik ini ≡ luas_sk (sawit_tot kosong) —
                # baris "tanpa_sawit" yang tak pernah memeriksa sawit adalah
                # klaim bohong, jadi DILEWATI (None), sama spt ditambang_tanpa_
                # sawit di bawah (item audit 15 Agu: data-full tanpa Descals).
                "luas_sk_tanpa_sawit": ([max(0.0, geo[k][1] - sawit_tot.get(k, 0.0))
                                         for k in ks] if ada_sawit else None),
                "ditambang": dit,
                "ditambang_tanpa_sawit": dit_ts,
            }
            for metrik, vals in metrik_vals.items():
                if vals is None:
                    continue
                n = len(vals)
                mean_v = round(sum(vals) / n, 2) if n else None
                med_v = med(vals)
                con.execute("INSERT INTO backtrack_distribusi VALUES (?,?,?,?,?,?,?)",
                            (aturan, metrik, kelompok, n, mean_v,
                             None if med_v is None else round(med_v, 2),
                             gini(vals)))

    # ── backtrack_signifikansi: uji beda antar-periode per aturan ────────────
    con.execute("DROP TABLE IF EXISTS backtrack_signifikansi")
    con.execute("""CREATE TABLE backtrack_signifikansi (
        aturan TEXT, metrik TEXT, uji TEXT, grup_a TEXT, grup_b TEXT,
        n_a INTEGER, n_b INTEGER, statistik REAL, p_value REAL, p_adjusted REAL,
        signifikan_005 INTEGER,
        PRIMARY KEY (aturan, metrik, uji, grup_a, grup_b))""")
    try:
        from scipy import stats as _st
    except ImportError:
        _st = None
        print("PERINGATAN: scipy absen — backtrack_signifikansi kosong (UI menyembunyikan).")
    if _st is not None:
        for aturan, m_of in mulai_of.items():
            sampel: dict[str, dict[str, list]] = {"loss": {}, "laju_pct": {}}
            for kode in kode_semua:
                pp = to_periode(geo.get(kode, (None,))[0])
                if pp not in ("P1", "P2", "P3") or m_of.get(kode) is None:
                    continue
                m = m_of[kode]
                l25 = loss_jendela(kode, m, JENDELA_MAX)
                sampel["loss"].setdefault(pp, []).append(l25)
                hm = f2000.get(kode, 0.0) - sum(
                    v for y, v in loss_th.get(kode, {}).items() if y < m)
                nt = JENDELA_MAX - m + 1
                if hm > 0 and nt > 0:
                    sampel["laju_pct"].setdefault(pp, []).append(100.0 * (l25 / nt) / hm)
            for metrik, per in sampel.items():
                grup2 = [per.get(pp, []) for pp in ("P1", "P2", "P3")]
                if any(len(g) < 3 for g in grup2):
                    continue
                h, p_kw = _st.kruskal(*grup2)
                con.execute("INSERT INTO backtrack_signifikansi VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (aturan, metrik, "kruskal_wallis", "P1|P2|P3", "-",
                             sum(len(g) for g in grup2), 0, round(float(h), 4),
                             round(float(p_kw), 6), None, 1 if p_kw < 0.05 else 0))
                pasang = [("P1", "P2"), ("P1", "P3"), ("P2", "P3")]
                hasil = []
                for a, b2 in pasang:
                    u, p = _st.mannwhitneyu(per[a], per[b2], alternative="two-sided")
                    hasil.append((a, b2, len(per[a]), len(per[b2]), float(u), float(p)))
                # koreksi Holm
                urut = sorted(range(len(hasil)), key=lambda i: hasil[i][5])
                p_adj = [None] * len(hasil)
                m_uji = len(hasil)
                runmax = 0.0
                for rank, i in enumerate(urut):
                    padj = min(1.0, (m_uji - rank) * hasil[i][5])
                    runmax = max(runmax, padj)
                    p_adj[i] = runmax
                for (a, b2, na, nb, u, p), padj in zip(hasil, p_adj):
                    con.execute(
                        "INSERT INTO backtrack_signifikansi VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (aturan, metrik, "mann_whitney_holm", a, b2, na, nb,
                         round(u, 2), round(p, 6), round(padj, 6),
                         1 if padj < 0.05 else 0))


def _stat_row(vals_ha, vals_pct, total_loss):
    ha = sorted(vals_ha)
    pc = sorted(v for v in vals_pct if v is not None)
    r2 = lambda v: None if v is None else round(v, 2)  # noqa: E731
    r3 = lambda v: None if v is None else round(v, 3)  # noqa: E731
    return (len(ha), len(pc), round(total_loss, 2),
            r2(pctl(ha, 50)), r2(sum(ha) / len(ha)) if ha else None,
            r2(pctl(ha, 25)), r2(pctl(ha, 75)), r2(pctl(ha, 90)),
            r3(pctl(pc, 50)), r3(sum(pc) / len(pc)) if pc else None,
            r3(pctl(pc, 25)), r3(pctl(pc, 75)), r3(pctl(pc, 90)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalimantan.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    ada_klas = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='klasifikasi_izin'").fetchone() is not None
    ada_sawit = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='atribusi_sawit_yearly'"
    ).fetchone() is not None and con.execute(
        "SELECT 1 FROM atribusi_sawit_yearly LIMIT 1").fetchone() is not None
    if not ada_sawit:
        print("PERINGATAN: atribusi_sawit_yearly absen/kosong — kolom bersih NULL "
              "dan BUKTI memakai Hansen mentah (tak bisa dipastikan non-sawit).")

    if ada_klas:
        rows = con.execute(
            """SELECT g.kode_wiup, COALESCE(k.kelas, 'TAK_DINILAI'), k.bukti, g.iup_year
               FROM wiup_geoportal g LEFT JOIN klasifikasi_izin k USING (kode_wiup)"""
        ).fetchall()
    else:
        rows = [(kode, "TAK_DINILAI", None, iy) for kode, iy in
                con.execute("SELECT kode_wiup, iup_year FROM wiup_geoportal")]
    f2000 = dict(con.execute("SELECT kode_wiup, COALESCE(forest_2000_ha,0) FROM wiup_loss"))
    loss_th: dict[str, dict[int, float]] = {}
    for kode, y, ha in con.execute(
        "SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly "
        "WHERE year BETWEEN 2001 AND ?", (JENDELA_MAX,)):
        loss_th.setdefault(kode, {})[y] = ha or 0.0
    sawit_th: dict[str, dict[int, float]] = {}
    if ada_sawit:
        for kode, y, ha in con.execute(
            "SELECT kode_wiup, year, loss_sawit_tol2th_ha FROM atribusi_sawit_yearly"):
            sawit_th.setdefault(kode, {})[y] = ha or 0.0

    # ── laju_izin_konsesi ────────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS laju_izin_konsesi")
    con.execute("""CREATE TABLE laju_izin_konsesi (
        kode_wiup TEXT PRIMARY KEY, kelas TEXT, bukti TEXT, iup_year INTEGER,
        periode TEXT, mulai INTEGER, dasar_mulai TEXT, tahun_bukti INTEGER,
        hutan_mulai_aktif_ha REAL,
        n_tahun_dari_mulai_aktif_sampai_2025 INTEGER, loss_mulai_aktif_sampai_2025_ha REAL,
        laju_mulai_aktif_sampai_2025_ha_thn REAL, laju_mulai_aktif_sampai_2025_pct_thn REAL,
        n_tahun_dari_mulai_aktif_sampai_2021 INTEGER, loss_mulai_aktif_sampai_2021_tanpa_sawit_ha REAL,
        laju_mulai_aktif_sampai_2021_tanpa_sawit_ha_thn REAL, laju_mulai_aktif_sampai_2021_tanpa_sawit_pct_thn REAL)""")

    per_kon = []   # baris kohort utk ringkas: (kelas, periode, k_ha, k_pct, b_ha, b_pct, loss_k, loss_b)
    n_backtrack = 0
    for kode, kelas, bukti, iy in rows:
        th = loss_th.get(kode, {})
        sw = sawit_th.get(kode, {})
        mulai, dasar, thn_bukti = hitung_mulai_bukti(iy, th, sw, ada_sawit)
        (hm, tk, lk, ljk, ljkp, tb, lb, ljb, ljbp) = hitung_laju(
            mulai, f2000.get(kode, 0.0), th, sw, ada_sawit)
        periode = to_periode(iy)
        r2 = lambda v: None if v is None else round(v, 2)  # noqa: E731
        r3 = lambda v: None if v is None else round(v, 3)  # noqa: E731
        con.execute("INSERT INTO laju_izin_konsesi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (kode, kelas, bukti, iy, periode, mulai, dasar, thn_bukti, r2(hm),
                     tk, r2(lk), r3(ljk), r3(ljkp), tb, r2(lb), r3(ljb), r3(ljbp)))
        if mulai is not None:
            per_kon.append((kelas, periode, ljk, ljkp, ljb, ljbp, lk, lb))
            if dasar == "BUKTI":
                n_backtrack += 1

    # (Kolom jendela era Minerba kini milik wiup_loss — dihitung saat ingest
    # build_combined_db step_loss; tabel jendela_2009 lama dihapus, Fase B r2.)
    con.execute("DROP TABLE IF EXISTS jendela_2009")

    # ── laju_izin_ringkas ────────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS laju_izin_ringkas")
    con.execute("""CREATE TABLE laju_izin_ringkas (
        basis TEXT, dimensi TEXT, kelompok TEXT, n INTEGER, n_pct INTEGER,
        total_loss_ha REAL,
        median_ha_thn REAL, mean_ha_thn REAL, p25_ha_thn REAL, p75_ha_thn REAL,
        p90_ha_thn REAL,
        median_pct_thn REAL, mean_pct_thn REAL, p25_pct_thn REAL, p75_pct_thn REAL,
        p90_pct_thn REAL,
        PRIMARY KEY (basis, dimensi, kelompok))""")

    def grup(dimensi, kunci):
        """Baris ringkas kedua basis utk satu kelompok. kunci(r) -> ikut/tidak."""
        anggota = [r for r in per_kon if kunci(r)]
        keluar = []
        # kotor: semua anggota kohort
        keluar.append(("kotor", [r[2] for r in anggota], [r[3] for r in anggota],
                       sum(r[6] for r in anggota)))
        # bersih: hanya yang lajunya terdefinisi (mulai ≤ 2021 & sawit ada)
        b = [r for r in anggota if r[4] is not None]
        keluar.append(("bersih", [r[4] for r in b], [r[5] for r in b],
                       sum(r[7] for r in b)))
        return keluar

    kelompok = [("semua", "SEMUA", lambda r: True)]
    for kls in sorted({r[0] for r in per_kon}):
        kelompok.append(("kelas", kls, lambda r, k=kls: r[0] == k))
    for p in ("P1", "P2", "P3"):
        kelompok.append(("periode", p, lambda r, p=p: r[1] == p))
    for dimensi, nama, kunci in kelompok:
        for basis, vha, vpct, tot in grup(dimensi, kunci):
            con.execute(
                "INSERT INTO laju_izin_ringkas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (basis, dimensi, nama) + _stat_row(vha, vpct, tot))

    # ── laju_izin_eventstudy ─────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS laju_izin_eventstudy")
    # Kolom *_tanpa_sawit_sampai_2021* (eks *_bersih*): "bersih" pensiun total
    # (DECISIONS 13 Agu — tak menyebut bersih dari apa, dan menyembunyikan
    # bahwa jangkauannya berhenti di 2021, batas peta Descals).
    con.execute("""CREATE TABLE laju_izin_eventstudy (
        kelas TEXT, rel_year INTEGER, n INTEGER, sum_loss_ha REAL, mean_loss_ha REAL,
        n_tanpa_sawit_sampai_2021 INTEGER, sum_tanpa_sawit_sampai_2021_ha REAL,
        mean_tanpa_sawit_sampai_2021_ha REAL,
        PRIMARY KEY (kelas, rel_year))""")
    kohort_es = [(kode, kelas, iy) for kode, kelas, _b, iy in rows
                 if iy is not None and JENDELA_MIN <= iy <= JENDELA_MAX]
    agg: dict[tuple[str, int], list] = {}   # (kelas, rel) -> [n, sum, n_b, sum_b]
    for kode, kelas, iy in kohort_es:
        th = loss_th.get(kode, {})
        sw = sawit_th.get(kode, {})
        for rel in range(REL_MIN, REL_MAX + 1):
            y = iy + rel
            if not 2001 <= y <= JENDELA_MAX:
                continue
            for k in (kelas, "SEMUA"):
                a = agg.setdefault((k, rel), [0, 0.0, 0, 0.0])
                a[0] += 1
                a[1] += th.get(y, 0.0)
                if ada_sawit and y <= BATAS_DESCALS:
                    a[2] += 1
                    a[3] += max(0.0, th.get(y, 0.0) - sw.get(y, 0.0))
    for (kelas, rel), (n, s, nb, sb) in sorted(agg.items()):
        con.execute("INSERT INTO laju_izin_eventstudy VALUES (?,?,?,?,?,?,?,?)",
                    (kelas, rel, n, round(s, 2), round(s / n, 2) if n else None,
                     nb, round(sb, 2), round(sb / nb, 2) if nb else None))

    # ── konsesi_aktif_tahunan: BERAPA KONSESI yang sudah aktif tiap tahun ──────
    # Pendamping baseline_tahunan (yang isinya hektar). Dua deret KUMULATIF:
    #   n_mulai_aktif  = konsesi yang tahun mulai aktifnya (aturan E) <= tahun
    #   n_sk_terbit    = konsesi yang tahun SK-nya (iup_year) <= tahun
    # Selisih keduanya = efek backtrack, dan n_aktif_sebelum_sk menghitungnya
    # LANGSUNG per konsesi (bukan hasil pengurangan dua agregat, yang bisa
    # menyesatkan kalau ada konsesi tanpa iup_year).
    # Sebelum 2009 kolom n_mulai_aktif sengaja NULL, BUKAN 0: aturan mulai-aktif
    # hanya memeriksa 2009-2025 (era UU Minerba), jadi "nol konsesi aktif di
    # 2005" bukan temuan lapangan melainkan akibat batas aturan — menulis 0 akan
    # dibaca sebagai klaim tentang kenyataan.
    con.execute("DROP TABLE IF EXISTS konsesi_aktif_tahunan")
    con.execute(
        """CREATE TABLE konsesi_aktif_tahunan (
            year INTEGER PRIMARY KEY,
            n_mulai_aktif INTEGER,
            n_sk_terbit INTEGER,
            n_aktif_sebelum_sk INTEGER)"""
    )
    # Sumbernya tabel yang BARU SAJA ditulis di transaksi ini (bukan per_kon —
    # baris per_kon tak memuat kode_wiup, hanya metrik agregat per konsesi).
    mulai_of = dict(con.execute(
        "SELECT kode_wiup, mulai FROM laju_izin_konsesi").fetchall())
    iup_of = dict(con.execute(
        "SELECT kode_wiup, iup_year FROM wiup_geoportal").fetchall())
    for y in range(2001, JENDELA_MAX + 1):
        n_sk = sum(1 for iy in iup_of.values() if iy is not None and iy <= y)
        if y < JENDELA_MIN:
            n_aktif = n_sebelum = None
        else:
            n_aktif = sum(1 for m in mulai_of.values() if m is not None and m <= y)
            n_sebelum = sum(
                1 for kode, m in mulai_of.items()
                if m is not None and m <= y
                and (iup_of.get(kode) is None or iup_of[kode] > y))
        con.execute("INSERT INTO konsesi_aktif_tahunan VALUES (?,?,?,?)",
                    (y, n_aktif, n_sk, n_sebelum))

    build_backtrack_tables(con, rows, loss_th, sawit_th, f2000, ada_sawit)

    con.commit()
    n_kohort = len(per_kon)
    n_tanpa_sawit = sum(1 for r in per_kon if r[4] is not None)
    tot_k = sum(r[6] for r in per_kon)
    tot_b = sum(r[7] for r in per_kon if r[7] is not None)
    print(f"laju_izin (aturan E/bukti): kohort {n_kohort} (tanpa-sawit terdefinisi {n_tanpa_sawit}, "
          f"jam-bukti/backtrack {n_backtrack}) · Σ loss kotor {tot_k:,.0f} ha · "
          f"Σ loss bersih {tot_b:,.0f} ha · eventstudy {len(kohort_es)} konsesi")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
