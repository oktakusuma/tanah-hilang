#!/usr/bin/env python3
# STATUS  : ARSIP — tabel keyakinan_* di kalimantan.db v2 (membaca data/mapbiomas.db lewat path hard-coded)
# CATATAN : pengganti: pipeline/08_keyakinan.py (benih 20260831 sama; kelas 30 MapBiomas kini dibaca dari DB yang sama)
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""Tingkat keyakinan: apakah konsesi SUDAH BEROPERASI sebelum tahun izinnya?

MASALAH YANG DIJAWAB (permintaan igoen, 31 Agu 2026). Aturan INDIKASI yang
lama memaksa pilihan keras: konsesi PERPANJANGAN dianggap aktif sejak awal
jendela, titik. Padahal keyakinannya jelas tidak sama untuk semua. Yang
diminta: nilai keyakinan bertingkat dari beberapa parameter.

APA YANG DIUKUR — DAN APA YANG TIDAK. Besaran yang ditaksir di sini adalah
"konsesi ini sudah beroperasi SEBELUM `iup_year` yang tercatat", BUKAN
"izin ini perpanjangan". Dua hal itu berbeda: kegiatan pra-izin juga bisa
berarti penambangan tanpa izin, bukan hanya perpanjangan. Yang dibutuhkan
atribusi kehilangan justru yang pertama — apakah ada kegiatan sebelum SK —
jadi itulah yang dinamai apa adanya.

KENAPA TIDAK MENGARANG BOBOT. Memberi "PKP2B = 40 poin, durasi pendek = 30
poin" tampak ilmiah tapi angkanya karangan, dan penguji berhak bertanya dari
mana 40 itu. Karena itu keyakinan di sini DIKALIBRASI ke bukti yang bisa
diamati, bukan ditetapkan lewat rapat.

DUA GOLONGAN SINYAL — pemisahan ini yang membuat kalibrasinya sah.

  BUKTI LANGSUNG (teramati, bebas dari registri) — dipakai sebagai SASARAN:
    E  lubang tambang MapBiomas >= 5 ha, bertahan >= 2 tahun berturut-turut,
       pada tahun-tahun SEBELUM iup_year. Bukti FISIK.
       Ambang & keteguhan dipakai supaya derau salah-klasifikasi piksel tak
       terbaca sebagai tambang.
    F  SK IPPKH terbit sebelum iup_year. Bukti DOKUMENTER dari instansi lain.

  PETUNJUK REGISTRI (inferensi dari catatan izin) — dipakai sebagai PENEBAK:
    A  PKP2B/KK ber-iup_year >= 2009 — kemustahilan hukum (sistem kontrak
       karya UU 11/1967 berhenti terbit sejak UU 4/2009).
    B  SK Operasi Produksi berjangka < 20 tahun — UU 4/2009 Ps. 47.
    C  masa berlaku diwarisi: tahun tgl_berlaku < iup_year.
    G  dua registri TIDAK SEPAKAT soal durasi SK (MinerbaOne vs Geoportal).
       Ketidaksepakatan itu sendiri pertanda catatannya tak bisa dipegang.
    D  > 50% kehilangan Hansen terjadi sebelum iup_year (hanya di model RS).

CARA. Regresi logistik (IRLS, numpy — sengaja tanpa sklearn supaya metodenya
kelihatan dan bisa diaudit) dari petunjuk registri ke bukti langsung E|F.
Peluang hasil fit ADALAH peluang terkalibrasi: "diberi profil registri
seperti ini, seberapa sering benar-benar terlihat kegiatan pra-izin".

  Model R   A, B, C, G           — bersih: registri menebak observasi.
  Model RS  A, B, C, G, D        — tambah satelit; lebih tajam, tapi D
                                    berhimpitan makna dgn sasaran. Dipakai
                                    sebagai uji ketahanan, bukan angka utama.

Keyakinan akhir: 1,0 bila bukti langsung TERAMATI (kita melihatnya, bukan
menebak); selain itu peluang model.

KAVEAT YANG TIDAK BOLEH DIHAPUS.
  * E dan F sama-sama BATAS BAWAH — akurasi kelas Lubang Tambang tidak
    dipublikasikan MapBiomas, dan layer IPPKH hanya memuat izin berstatus
    Aktif (bukan register sejarah). Sasaran yang kurang tercatat membuat
    model MEREMEHKAN; jadi peluang di sini batas bawah, bukan taksiran tak
    bias.
  * Tak ada satu pun label kebenaran mutlak di sini. Yang dikalibrasi adalah
    "terlihatnya kegiatan pra-izin", bukan status hukum izin.
  * Kalau AUC mendekati 0,5, petunjuk registri memang tak membawa informasi —
    itu dilaporkan apa adanya, bukan dipoles.

AKIBATNYA UNTUK ATRIBUSI. Selama ini angkanya kurung: INDIKASI (batas atas)
vs POLOS (batas bawah). Dengan peluang per konsesi, kurung itu bisa jadi satu
taksiran harapan berselang kepercayaan:

    loss_harapan_i = p_i * loss(2001..2024) + (1 - p_i) * loss(iup_year..2024)

Selangnya lewat bootstrap Bernoulli(p_i) — jadi ketidakpastiannya ikut
terbawa, bukan hilang di balik satu angka.

    python3 scripts/build_keyakinan_pra_izin.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np

AMBANG_PIT_HA = 5.0        # lubang tambang minimal, agar derau piksel tak terhitung
AMBANG_PIT_TAHUN = 2       # harus bertahan >= 2 tahun berturut-turut
TAHUN_MIN = 2001           # lantai pengamatan (awal rekaman Hansen)
# 2024, bukan 2025: jendela tesis 2001-2024 (proposal v0.3.2, igoen 1 Sep 2026)
TAHUN_MAX = 2024
N_BOOTSTRAP = 2000
BENIH = 20260831

_SKRIP = "scripts/build_keyakinan_pra_izin.py"

META_DDL = """
    CREATE TABLE IF NOT EXISTS analysis_meta (
        nama_tabel TEXT PRIMARY KEY, deskripsi TEXT, sumber TEXT, metode TEXT,
        script TEXT, status TEXT NOT NULL DEFAULT 'AKTIF'
        CHECK (status IN ('AKTIF','ARSIP','PROYEKSI')));
    CREATE TABLE IF NOT EXISTS column_meta (
        nama_tabel TEXT, nama_kolom TEXT, deskripsi TEXT, rumus TEXT, sumber TEXT,
        PRIMARY KEY (nama_tabel, nama_kolom));
"""



# ───────────────────────── regresi logistik (IRLS) ────────────────────────
def logistik(X: np.ndarray, y: np.ndarray, maks_iter=100, tol=1e-8):
    """Fit logistik ber-intersep lewat IRLS.

    Balikan (koefisien, kovarians asimtotik (X'WX)^-1, konvergen). Kovariansnya
    dipakai menarik beta dari MVN saat bootstrap, supaya galat penaksiran ikut
    masuk selang — bukan cuma keragaman Bernoulli.

    Ridge sangat kecil (1e-6) dipasang HANYA untuk menjaga matriks tetap
    bisa dibalik saat sebuah penebak memisahkan sempurna; tak cukup besar
    untuk menggeser taksiran secara berarti.
    """
    n, k = X.shape
    Xd = np.column_stack([np.ones(n), X])
    beta = np.zeros(k + 1)
    for _ in range(maks_iter):
        eta = Xd @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
        W = np.clip(p * (1 - p), 1e-9, None)
        z = eta + (y - p) / W
        A = Xd.T @ (Xd * W[:, None]) + 1e-6 * np.eye(k + 1)
        beta_baru = np.linalg.solve(A, Xd.T @ (W * z))
        if np.max(np.abs(beta_baru - beta)) < tol:
            return beta_baru, np.linalg.inv(A), True
        beta = beta_baru
    return beta, np.linalg.inv(A), False


def ramal(beta: np.ndarray, X: np.ndarray) -> np.ndarray:
    eta = beta[0] + X @ beta[1:]
    return 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """AUC lewat statistik Mann-Whitney (menangani nilai seri)."""
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    peringkat = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1.0
    # rata-ratakan peringkat untuk nilai seri
    gabung = np.concatenate([pos, neg])
    for v in np.unique(gabung):
        m = gabung == v
        if m.sum() > 1:
            peringkat[m] = peringkat[m].mean()
    r_pos = peringkat[:len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


# ─────────────────────────── kumpulkan sinyal ─────────────────────────────
def kumpulkan(con: sqlite3.Connection) -> list[dict]:
    con.row_factory = sqlite3.Row
    baris = {r["kode_wiup"]: dict(r) for r in con.execute("""
        SELECT g.kode_wiup, g.nama_usaha, g.iup_year, g.jenis_izin, g.kegiatan,
               g.luas_sk,
               k.kelas, k.bukti, k.durasi_sk AS durasi_minerba,
               k.masa_berlaku_diwarisi, k.pra_izin_dominan,
               p.durasi_sk_tahun AS durasi_geoportal, p.tgl_berlaku,
               i.tgl_ippkh_awal, i.punya_ippkh_tambang
        FROM wiup_geoportal g
        LEFT JOIN klasifikasi_izin   k USING (kode_wiup)
        LEFT JOIN wiup_tanggal_pulih p USING (kode_wiup)
        LEFT JOIN konsesi_ippkh      i USING (kode_wiup)
    """)}

    # E — lubang tambang pra-izin yang teguh (>= AMBANG_PIT_TAHUN tahun beruntun)
    pit: dict[str, list[int]] = {}
    con2 = sqlite3.connect("file:data/mapbiomas.db?mode=ro", uri=True)
    try:
        for kode, th, ha in con2.execute(
                "SELECT kode_wiup, year, ha FROM landuse_konsesi "
                "WHERE class_code=30 AND ha>=?", (AMBANG_PIT_HA,)):
            pit.setdefault(kode, []).append(int(th))
    finally:
        con2.close()

    # D/loss — kehilangan Hansen per tahun
    loss: dict[str, dict[int, float]] = {}
    for kode, th, ha in con.execute(
            "SELECT kode_wiup, year, loss_ha FROM wiup_loss_yearly WHERE year BETWEEN ? AND ?",
            (TAHUN_MIN, TAHUN_MAX)):
        loss.setdefault(kode, {})[int(th)] = ha or 0.0

    out = []
    for kode, r in baris.items():
        iy = r["iup_year"]
        if iy is None:
            continue                          # tak bisa dinilai sama sekali
        t_sk = max(TAHUN_MIN, int(iy))

        tahun_pit = sorted(t for t in pit.get(kode, []) if TAHUN_MIN <= t < iy)
        teguh = False
        for i in range(len(tahun_pit) - AMBANG_PIT_TAHUN + 1):
            jendela = tahun_pit[i:i + AMBANG_PIT_TAHUN]
            if jendela[-1] - jendela[0] == AMBANG_PIT_TAHUN - 1:
                teguh = True
                break
        E = int(teguh)

        th_ippkh = (int(r["tgl_ippkh_awal"][:4])
                    if r["tgl_ippkh_awal"] else None)
        F = int(th_ippkh is not None and th_ippkh < iy)

        A = int((r["jenis_izin"] or "") in ("PKP2B", "KK") and iy >= 2009)
        dur = r["durasi_geoportal"] if r["durasi_geoportal"] is not None \
            else r["durasi_minerba"]
        B = int((r["kegiatan"] or "") == "OPERASI PRODUKSI"
                and dur is not None and dur < 20)
        C = int(bool(r["masa_berlaku_diwarisi"]))
        G = int(r["durasi_geoportal"] is not None
                and r["durasi_minerba"] is not None
                and r["durasi_geoportal"] != r["durasi_minerba"])
        D = int(bool(r["pra_izin_dominan"]))

        th_loss = loss.get(kode, {})
        out.append({
            "kode_wiup": kode, "nama_usaha": r["nama_usaha"], "iup_year": int(iy),
            "kelas": r["kelas"], "durasi_sk": dur,
            "E_lubang_pra_izin": E, "F_ippkh_lebih_tua": F,
            "A_kontrak_karya": A, "B_durasi_pendek": B,
            "C_masa_diwarisi": C, "G_registri_beda": D * 0 + G,
            "D_hansen_pra_dominan": D,
            "loss_sejak_2001_ha": round(sum(th_loss.values()), 4),
            "loss_sejak_sk_ha": round(
                sum(v for t, v in th_loss.items() if t >= t_sk), 4),
        })
    return out


# ────────────────────────────── utama ─────────────────────────────────────
PENEBAK_R = ("A_kontrak_karya", "B_durasi_pendek", "C_masa_diwarisi",
             "G_registri_beda")
PENEBAK_RS = PENEBAK_R + ("D_hansen_pra_dominan",)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/kalimantan.db")
    a = ap.parse_args(argv)

    con = sqlite3.connect(a.db)
    data = kumpulkan(con)
    print(f"{len(data)} konsesi ber-iup_year")

    y = np.array([d["E_lubang_pra_izin"] or d["F_ippkh_lebih_tua"] for d in data],
                 dtype=float)
    print(f"Bukti langsung pra-izin teramati: {int(y.sum())} "
          f"({y.mean() * 100:.1f}%) — E={sum(d['E_lubang_pra_izin'] for d in data)}, "
          f"F={sum(d['F_ippkh_lebih_tua'] for d in data)}")

    hasil = {}
    for nama, penebak in (("R", PENEBAK_R), ("RS", PENEBAK_RS)):
        X = np.array([[d[k] for k in penebak] for d in data], dtype=float)
        beta, kov, konv = logistik(X, y)
        p = ramal(beta, X)
        skor = auc(y, p)
        hasil[nama] = (penebak, beta, p, skor, konv, kov, X)
        print(f"\nModel {nama}: AUC = {skor:.3f}"
              f"{'' if konv else '  (PERINGATAN: IRLS tidak konvergen)'}")
        print(f"  intersep {beta[0]:+.3f}")
        for k, b in zip(penebak, beta[1:]):
            print(f"  {k:<24} {b:+.3f}   (rasio odds {np.exp(b):.2f})")

    penebak, beta, p_model, skor, _, kov_r, X_r = hasil["R"]
    p_rs = hasil["RS"][2]

    baris = []
    for d, pm, prs in zip(data, p_model, p_rs):
        langsung = int(d["E_lubang_pra_izin"] or d["F_ippkh_lebih_tua"])
        p_akhir = 1.0 if langsung else float(pm)
        n_sinyal = sum(d[k] for k in PENEBAK_RS) + langsung
        harapan = (p_akhir * d["loss_sejak_2001_ha"]
                   + (1 - p_akhir) * d["loss_sejak_sk_ha"])
        baris.append((
            d["kode_wiup"], d["iup_year"], d["kelas"], d["durasi_sk"],
            d["E_lubang_pra_izin"], d["F_ippkh_lebih_tua"], d["A_kontrak_karya"],
            d["B_durasi_pendek"], d["C_masa_diwarisi"], d["G_registri_beda"],
            d["D_hansen_pra_dominan"], n_sinyal, langsung,
            round(float(pm), 4), round(float(prs), 4), round(p_akhir, 4),
            d["loss_sejak_2001_ha"], d["loss_sejak_sk_ha"], round(harapan, 4)))

    # ── Akibat untuk atribusi + selang bootstrap ─────────────────────────
    p_arr = np.array([b[15] for b in baris])
    l2001 = np.array([b[16] for b in baris])
    lsk = np.array([b[17] for b in baris])
    harapan = float((p_arr * l2001 + (1 - p_arr) * lsk).sum())

    # Selang menampung DUA sumber ketidakpastian, bukan satu:
    #   (i)  galat penaksiran koefisien — beta ditarik dari MVN(beta_hat, kov);
    #   (ii) keragaman Bernoulli(p_i) antar konsesi.
    # Konsesi berbukti langsung tetap p=1 di tiap tarikan: kita MELIHAT
    # kegiatannya, jadi ia bukan sumber ketidakpastian.
    rng = np.random.default_rng(BENIH)
    langsung = np.array([b[12] for b in baris], dtype=bool)
    tarikan = np.empty(N_BOOTSTRAP)
    for i in range(N_BOOTSTRAP):
        beta_i = rng.multivariate_normal(beta, kov_r)
        p_i = ramal(beta_i, X_r)
        p_i = np.where(langsung, 1.0, p_i)
        tarikan[i] = float(np.where(rng.random(len(p_i)) < p_i, l2001, lsk).sum())
    lo, hi = np.percentile(tarikan, [2.5, 97.5])

    print(f"\n── Akibat untuk atribusi (2001-{TAHUN_MAX}) ──")
    print(f"  batas bawah (semua sejak SK)     : {lsk.sum():,.0f} ha")
    print(f"  taksiran harapan berbobot peluang: {harapan:,.0f} ha")
    print(f"  selang bootstrap 95%             : {lo:,.0f} – {hi:,.0f} ha")
    print(f"  batas atas (semua sejak 2001)    : {l2001.sum():,.0f} ha")

    tulis(con, baris, hasil, harapan, lo, hi, float(lsk.sum()), float(l2001.sum()))
    con.close()
    print("\nSelesai.")
    return 0


_SUMBER_K = ("wiup_geoportal + klasifikasi_izin + wiup_tanggal_pulih + konsesi_ippkh "
             "+ mapbiomas.landuse_konsesi (kelas 30) + wiup_loss_yearly")

KOLOM = [
    ("keyakinan_pra_izin", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER_K),
    ("keyakinan_pra_izin", "iup_year", "Tahun izin tercatat di registri — inilah yang "
     "sedang diuji, bukan diandaikan benar.", "-", _SUMBER_K),
    ("keyakinan_pra_izin", "kelas", "Vonis klasifikasi_izin (IZIN_PERTAMA/PERPANJANGAN/"
     "TAK_DINILAI) — dibawa untuk pembanding, BUKAN penebak model.", "-", _SUMBER_K),
    ("keyakinan_pra_izin", "durasi_sk", "Jangka SK (tahun). Diutamakan dari "
     "wiup_tanggal_pulih; jatuh ke MinerbaOne bila tak ada.", "-", _SUMBER_K),
    ("keyakinan_pra_izin", "e_lubang_pra_izin",
     "BUKTI LANGSUNG (fisik). 1 bila lubang tambang MapBiomas >= 5 ha bertahan >= 2 tahun "
     "beruntun pada tahun-tahun sebelum iup_year. Ambang & keteguhan menahan derau piksel.",
     "MAX(ha kelas 30) >= 5 pada >= 2 tahun beruntun < iup_year", _SUMBER_K),
    ("keyakinan_pra_izin", "f_ippkh_lebih_tua",
     "BUKTI LANGSUNG (dokumen). 1 bila SK IPPKH terawal lebih tua dari iup_year.",
     "tahun(konsesi_ippkh.tgl_ippkh_awal) < iup_year", _SUMBER_K),
    ("keyakinan_pra_izin", "a_kontrak_karya",
     "PETUNJUK REGISTRI. 1 bila PKP2B/KK ber-iup_year >= 2009 — kemustahilan hukum "
     "(kontrak karya UU 11/1967 berhenti terbit sejak UU 4/2009).",
     "jenis_izin IN ('PKP2B','KK') AND iup_year >= 2009", _SUMBER_K),
    ("keyakinan_pra_izin", "b_durasi_pendek",
     "PETUNJUK REGISTRI. 1 bila SK Operasi Produksi berjangka < 20 tahun (UU 4/2009 "
     "Ps. 47). Rasio odds terbesar di model — penalarannya terkonfirmasi empiris.",
     "kegiatan='OPERASI PRODUKSI' AND durasi_sk < 20", _SUMBER_K),
    ("keyakinan_pra_izin", "c_masa_diwarisi",
     "PETUNJUK REGISTRI. 1 bila tahun tgl_berlaku < iup_year — izin 'baru' yang membawa "
     "masa berlaku pendahulunya.", "klasifikasi_izin.masa_berlaku_diwarisi", _SUMBER_K),
    ("keyakinan_pra_izin", "g_registri_beda",
     "PETUNJUK REGISTRI. 1 bila MinerbaOne dan Geoportal TIDAK sepakat soal durasi SK. "
     "Hasilnya rasio odds ~0,91 — sinyal ini TIDAK berfungsi; dilaporkan apa adanya.",
     "durasi_geoportal <> durasi_minerba", _SUMBER_K),
    ("keyakinan_pra_izin", "d_hansen_pra_dominan",
     "PETUNJUK REGISTRI (satelit, hanya model RS). 1 bila > 50% kehilangan Hansen terjadi "
     "sebelum iup_year.", "klasifikasi_izin.pra_izin_dominan", _SUMBER_K),
    ("keyakinan_pra_izin", "n_sinyal", "Cacah sinyal yang terpenuhi (A..G + bukti "
     "langsung) — ringkasan kasar, BUKAN skor berbobot.", "SUM sinyal", _SUMBER_K),
    ("keyakinan_pra_izin", "bukti_langsung", "1 bila E atau F teramati. Konsesi ini "
     "dipatok peluang 1,0: kegiatannya TERLIHAT, bukan ditebak.", "E OR F", _SUMBER_K),
    ("keyakinan_pra_izin", "peluang_model_r", "Peluang hasil model R (registri saja).",
     "logistik(A,B,C,G) -> E|F", _SUMBER_K),
    ("keyakinan_pra_izin", "peluang_model_rs", "Peluang hasil model RS (+ satelit D). "
     "Uji ketahanan; D berhimpitan makna dgn sasaran, jadi bukan angka utama.",
     "logistik(A,B,C,G,D) -> E|F", _SUMBER_K),
    ("keyakinan_pra_izin", "peluang_akhir", "Peluang yang dipakai atribusi.",
     "1,0 bila bukti_langsung, selain itu peluang_model_r", _SUMBER_K),
    ("keyakinan_pra_izin", "loss_sejak_2001_ha", "Cabang 'sudah aktif sejak awal': "
     "kehilangan Hansen 2001-2025.", "SUM(loss_ha) 2001..2025", _SUMBER_K),
    ("keyakinan_pra_izin", "loss_sejak_sk_ha", "Cabang 'baru mulai di tahun SK': "
     "kehilangan sejak max(iup_year, 2001).",
     "SUM(loss_ha) max(iup_year,2001)..2025", _SUMBER_K),
    ("keyakinan_pra_izin", "loss_harapan_ha", "Taksiran harapan per konsesi — inilah yang "
     "menggantikan kurung keras INDIKASI/POLOS.",
     "p * loss_sejak_2001_ha + (1-p) * loss_sejak_sk_ha", _SUMBER_K),
    ("keyakinan_model", "model", "R (registri saja) atau RS (+ satelit).", "-", _SUMBER_K),
    ("keyakinan_model", "penebak", "Nama penebak, atau '(intersep)'.", "-", _SUMBER_K),
    ("keyakinan_model", "koefisien", "Koefisien logistik (skala log-odds).",
     "IRLS", _SUMBER_K),
    ("keyakinan_model", "rasio_odds", "Rasio odds — lebih mudah dibaca daripada koefisien.",
     "exp(koefisien)", _SUMBER_K),
    ("keyakinan_model", "auc", "Daya pisah model. 0,5 = tak berinformasi. Dilaporkan apa "
     "adanya, tidak dipoles.", "Statistik Mann-Whitney", _SUMBER_K),
    ("keyakinan_ringkas", "kunci", "Nama besaran ringkasan.", "-", _SUMBER_K),
    ("keyakinan_ringkas", "nilai", "Nilainya (teks — bisa angka atau parameter).",
     "-", _SUMBER_K),
]


def tulis(con, baris, hasil, harapan, lo, hi, bawah, atas):
    con.executescript(META_DDL)
    con.executescript("""
        DROP TABLE IF EXISTS keyakinan_pra_izin;
        DROP TABLE IF EXISTS keyakinan_model;
        DROP TABLE IF EXISTS keyakinan_ringkas;
        CREATE TABLE keyakinan_pra_izin (
            kode_wiup TEXT PRIMARY KEY, iup_year INTEGER, kelas TEXT,
            durasi_sk INTEGER,
            e_lubang_pra_izin INTEGER, f_ippkh_lebih_tua INTEGER,
            a_kontrak_karya INTEGER, b_durasi_pendek INTEGER,
            c_masa_diwarisi INTEGER, g_registri_beda INTEGER,
            d_hansen_pra_dominan INTEGER,
            n_sinyal INTEGER, bukti_langsung INTEGER,
            peluang_model_r REAL, peluang_model_rs REAL, peluang_akhir REAL,
            loss_sejak_2001_ha REAL, loss_sejak_sk_ha REAL, loss_harapan_ha REAL);
        CREATE TABLE keyakinan_model (
            model TEXT, penebak TEXT, koefisien REAL, rasio_odds REAL,
            auc REAL, PRIMARY KEY (model, penebak));
        CREATE TABLE keyakinan_ringkas (kunci TEXT PRIMARY KEY, nilai TEXT);
    """)
    con.executemany("INSERT INTO keyakinan_pra_izin VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", baris)
    for nama, (penebak, beta, _p, skor, _k, _kov, _X) in hasil.items():
        con.execute("INSERT INTO keyakinan_model VALUES (?,?,?,?,?)",
                    (nama, "(intersep)", float(beta[0]), float(np.exp(beta[0])),
                     float(skor)))
        for k, b in zip(penebak, beta[1:]):
            con.execute("INSERT INTO keyakinan_model VALUES (?,?,?,?,?)",
                        (nama, k, float(b), float(np.exp(b)), float(skor)))
    for k, v in (("loss_batas_bawah_ha", f"{bawah:.2f}"),
                 ("loss_harapan_ha", f"{harapan:.2f}"),
                 ("loss_bootstrap_lo_ha", f"{lo:.2f}"),
                 ("loss_bootstrap_hi_ha", f"{hi:.2f}"),
                 ("loss_batas_atas_ha", f"{atas:.2f}"),
                 ("n_bootstrap", str(N_BOOTSTRAP)), ("benih", str(BENIH)),
                 ("jendela", f"{TAHUN_MIN}-{TAHUN_MAX}"),
                 ("ambang_lubang_ha", str(AMBANG_PIT_HA)),
                 ("ambang_lubang_tahun", str(AMBANG_PIT_TAHUN))):
        con.execute("INSERT INTO keyakinan_ringkas VALUES (?,?)", (k, v))

    con.executemany(
        "INSERT OR REPLACE INTO analysis_meta "
        "(nama_tabel,deskripsi,sumber,metode,script,status) VALUES (?,?,?,?,?,?)",
        [("keyakinan_pra_izin",
          "Peluang terkalibrasi bahwa tiap konsesi SUDAH BEROPERASI sebelum "
          "iup_year tercatat — bukan 'peluang izin ini perpanjangan'. Plus buku "
          "bukti tujuh sinyal (A,B,C,D,E,F,G) dan akibatnya untuk atribusi loss.",
          "wiup_geoportal + klasifikasi_izin + wiup_tanggal_pulih + konsesi_ippkh "
          "+ mapbiomas.landuse_konsesi (kelas 30) + wiup_loss_yearly",
          "Regresi logistik IRLS: petunjuk registri (A,B,C,G[,D]) -> bukti "
          "langsung teramati (E|F). Peluang = 1,0 bila bukti langsung ada. "
          "BATAS BAWAH: E dan F sama-sama kurang tercatat.",
          _SKRIP, "AKTIF"),
         ("keyakinan_model",
          "Koefisien & AUC tiap model keyakinan — supaya angkanya bisa diaudit, "
          "bukan kotak hitam.", "keyakinan_pra_izin", "IRLS numpy, tanpa sklearn.",
          _SKRIP, "AKTIF"),
         ("keyakinan_ringkas",
          "Akibat agregat untuk atribusi 2001-2024: batas bawah, taksiran harapan, selang "
          "bootstrap 95%, batas atas.", "keyakinan_pra_izin",
          f"Bootstrap {N_BOOTSTRAP} tarikan (benih {BENIH}): beta ~ MVN(beta_hat, "
          "(X'WX)^-1) lalu Bernoulli(p_i). Menampung galat koefisien DAN keragaman "
          "antar konsesi. Konsesi berbukti langsung dipatok p=1.",
          _SKRIP, "AKTIF")])
    con.executemany("INSERT OR REPLACE INTO column_meta "
                    "(nama_tabel,nama_kolom,deskripsi,rumus,sumber) VALUES (?,?,?,?,?)",
                    KOLOM)
    con.commit()


if __name__ == "__main__":
    raise SystemExit(main())
