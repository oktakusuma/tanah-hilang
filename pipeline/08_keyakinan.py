#!/usr/bin/env python3
"""Langkah 08 — keyakinan pra-izin: peluang terkalibrasi bahwa konsesi SUDAH BEROPERASI
sebelum tahun izinnya (SKEMA.md §7).

Port dari `scripts/build_keyakinan_pra_izin.py` (pipeline arsip). Algoritma, benih, dan
jumlah bootstrap SAMA; semua input kini dari SATU DB target (tak lagi membuka
`data/mapbiomas.db`). Penamaan: `iup_year` → `tahun_izin`, `loss_*` → `hilang_*`.

APA YANG DIUKUR. "Konsesi ini sudah beroperasi SEBELUM `tahun_izin` tercatat" — BUKAN
"izin ini perpanjangan": kegiatan pra-izin juga bisa berarti penambangan tanpa izin.

DUA GOLONGAN SINYAL — pemisahan ini yang membuat kalibrasinya sah (bobot tidak dikarang):
  BUKTI LANGSUNG (teramati) — SASARAN model:
    E  lubang tambang MapBiomas (kelas 30) >= 5 ha bertahan >= 2 tahun berturut pada tahun
       SEBELUM tahun_izin (mapbiomas_tahunan DB ini). Bukti FISIK.
    F  SK IPPKH terawal (ippkh.tgl_ippkh_awal) lebih tua dari tahun_izin. Bukti DOKUMENTER.
  PETUNJUK REGISTRI — PENEBAK:
    A  PKP2B/KK ber-tahun_izin >= 2009 (kontrak karya UU 11/1967 berhenti terbit sejak UU 4/2009).
    B  SK Operasi Produksi berjangka < 20 tahun (UU 4/2009 Ps. 47).
    C  masa berlaku diwarisi (izin_klasifikasi.masa_berlaku_diwarisi).
    G  MinerbaOne (konsesi_registri) dan Geoportal (konsesi.tgl_berlaku/tgl_berakhir) TIDAK
       SEPAKAT soal durasi SK.
    D  > 50% kehilangan Hansen sebelum tahun_izin (izin_klasifikasi.pra_izin_dominan; hanya model RS).

CARA. Regresi logistik IRLS (numpy, tanpa sklearn) dari penebak ke E|F. Model R = A,B,C,G
(angka utama); model RS = +D (uji ketahanan). Peluang akhir = 1,0 bila bukti langsung teramati,
selain itu peluang model R.

AKIBAT UNTUK ATRIBUSI:  hilang_harapan_i = p_i·hilang(2001..2024) + (1−p_i)·hilang(tahun_izin..2024),
selang 95% lewat bootstrap (beta ~ MVN(beta_hat, (X'WX)^-1), lalu Bernoulli(p_i)).

KAVEAT YANG TIDAK BOLEH DIHAPUS: E dan F sama-sama BATAS BAWAH (akurasi kelas Lubang Tambang
tak dipublikasikan MapBiomas; layer IPPKH hanya izin Aktif) → peluang di sini batas bawah;
tak ada label kebenaran mutlak; AUC dilaporkan apa adanya.

    .venv/bin/python pipeline/08_keyakinan.py --db data/tanah-hilang.db --himpunan minerba
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib.db import argparser, buka, gagal, tandai_selesai, wajib_tabel  # noqa: E402
from pipeline.lib.meta import LISENSI, tulis_meta  # noqa: E402

SKRIP = "pipeline/08_keyakinan.py"
AMBANG_PIT_HA = 5.0        # lubang tambang minimal, agar derau piksel tak terhitung
AMBANG_PIT_TAHUN = 2       # harus bertahan >= 2 tahun berturut-turut
TAHUN_MIN, TAHUN_MAX = 2001, 2024
N_BOOTSTRAP = 2000
BENIH = 20260831
KELAS_TAMBANG = 30

PENEBAK_R = ("A_kontrak_karya", "B_durasi_pendek", "C_masa_diwarisi", "G_registri_beda")
PENEBAK_RS = PENEBAK_R + ("D_hansen_pra_dominan",)

SUMBER = ("konsesi (Geoportal ESDM: tahun_izin, jenis_izin, kegiatan, tgl_berlaku/berakhir) + izin_klasifikasi + "
          "konsesi_registri (MinerbaOne) + ippkh.tgl_ippkh_awal (Geoportal) + hansen_tahunan (Hansen CC BY 4.0) + "
          "mapbiomas_tahunan kelas 30 (MapBiomas C4.1 CC BY-SA 4.0) — semuanya dari DB ini")
LISENSI_K = (LISENSI["turunan"] + "; memakai MapBiomas Indonesia Koleksi 4.1 (CC BY-SA 4.0) untuk sinyal E → "
             "tabel ini Adapted Material CC BY-SA 4.0")


# ───────────────────────── regresi logistik (IRLS) ────────────────────────
def logistik(X: np.ndarray, y: np.ndarray, maks_iter=100, tol=1e-8):
    """Fit logistik ber-intersep lewat IRLS → (beta, kovarians (X'WX)^-1, konvergen).
    Ridge 1e-6 hanya menjaga matriks tetap bisa dibalik saat ada pemisahan sempurna."""
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
    gabung = np.concatenate([pos, neg])
    peringkat = np.argsort(np.argsort(gabung)) + 1.0
    for v in np.unique(gabung):
        m = gabung == v
        if m.sum() > 1:
            peringkat[m] = peringkat[m].mean()
    r_pos = peringkat[:len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def _tahun(tgl: str | None) -> int | None:
    return int(tgl[:4]) if tgl and tgl[:4].isdigit() else None


# ─────────────────────────── kumpulkan sinyal ─────────────────────────────
def kumpulkan(con) -> list[dict]:
    baris = con.execute("""
        SELECT k.kode_wiup, k.tahun_izin, k.jenis_izin, k.kegiatan, k.tgl_berlaku, k.tgl_berakhir,
               z.kelas, z.durasi_sk AS durasi_klasifikasi, z.masa_berlaku_diwarisi, z.pra_izin_dominan,
               r.tanggal_berlaku AS reg_berlaku, r.tanggal_berakhir AS reg_berakhir,
               i.tgl_ippkh_awal
        FROM konsesi k
        LEFT JOIN izin_klasifikasi z USING (kode_wiup)
        LEFT JOIN konsesi_registri r USING (kode_wiup)
        LEFT JOIN ippkh i USING (kode_wiup)
        ORDER BY k.kode_wiup""").fetchall()
    nama_kolom = ["kode_wiup", "tahun_izin", "jenis_izin", "kegiatan", "tgl_berlaku", "tgl_berakhir", "kelas",
                  "durasi_klasifikasi", "masa_berlaku_diwarisi", "pra_izin_dominan", "reg_berlaku",
                  "reg_berakhir", "tgl_ippkh_awal"]

    # E — lubang tambang pra-izin yang teguh (>= AMBANG_PIT_TAHUN tahun beruntun), DB yang sama
    pit: dict[str, list[int]] = {}
    for kode, th in con.execute(
            "SELECT kode_wiup, tahun FROM mapbiomas_tahunan WHERE kelas=? AND ha>=?",
            (KELAS_TAMBANG, AMBANG_PIT_HA)):
        pit.setdefault(kode, []).append(int(th))
    if not pit:
        gagal("mapbiomas_tahunan tidak punya baris kelas 30 >= ambang — sinyal E tak bisa dihitung")

    loss: dict[str, dict[int, float]] = {}
    for kode, th, ha in con.execute(
            "SELECT kode_wiup, tahun, hilang_ha FROM hansen_tahunan WHERE tahun BETWEEN ? AND ?",
            (TAHUN_MIN, TAHUN_MAX)):
        loss.setdefault(kode, {})[int(th)] = ha or 0.0

    out, beda_durasi = [], 0
    for row in baris:
        r = dict(zip(nama_kolom, row))
        iy = r["tahun_izin"]
        if iy is None:
            continue                                  # tak bisa dinilai sama sekali
        iy = int(iy)
        t_sk = max(TAHUN_MIN, iy)

        tahun_pit = sorted(t for t in pit.get(r["kode_wiup"], []) if TAHUN_MIN <= t < iy)
        teguh = any(tahun_pit[i + AMBANG_PIT_TAHUN - 1] - tahun_pit[i] == AMBANG_PIT_TAHUN - 1
                    for i in range(len(tahun_pit) - AMBANG_PIT_TAHUN + 1))
        E = int(teguh)

        th_ippkh = _tahun(r["tgl_ippkh_awal"])
        F = int(th_ippkh is not None and th_ippkh < iy)

        # durasi SK menurut dua registri
        yb, ya = _tahun(r["tgl_berlaku"]), _tahun(r["tgl_berakhir"])
        durasi_geoportal = (ya - yb) if (yb is not None and ya is not None) else None
        rb, ra = _tahun(r["reg_berlaku"]), _tahun(r["reg_berakhir"])
        durasi_minerba = (ra - rb) if (rb is not None and ra is not None) else r["durasi_klasifikasi"]
        if r["durasi_klasifikasi"] is not None and durasi_minerba != r["durasi_klasifikasi"]:
            beda_durasi += 1
        dur = durasi_geoportal if durasi_geoportal is not None else durasi_minerba

        A = int((r["jenis_izin"] or "") in ("PKP2B", "KK") and iy >= 2009)
        B = int((r["kegiatan"] or "") == "OPERASI PRODUKSI" and dur is not None and dur < 20)
        C = int(bool(r["masa_berlaku_diwarisi"]))
        G = int(durasi_geoportal is not None and durasi_minerba is not None and durasi_geoportal != durasi_minerba)
        D = int(bool(r["pra_izin_dominan"]))

        th_loss = loss.get(r["kode_wiup"], {})
        out.append({
            "kode_wiup": r["kode_wiup"], "tahun_izin": iy, "kelas": r["kelas"], "durasi_sk": dur,
            "E_lubang_pra_izin": E, "F_ippkh_lebih_tua": F,
            "A_kontrak_karya": A, "B_durasi_pendek": B, "C_masa_diwarisi": C, "G_registri_beda": G,
            "D_hansen_pra_dominan": D,
            "hilang_sejak_2001_ha": round(sum(th_loss.values()), 4),
            "hilang_sejak_sk_ha": round(sum(v for t, v in th_loss.items() if t >= t_sk), 4),
        })
    if beda_durasi:
        print(f"  PERHATIAN: {beda_durasi} konsesi — durasi dari konsesi_registri ≠ izin_klasifikasi.durasi_sk "
              "(dipakai: konsesi_registri)")
    return out


# ─────────────────────────────── tulis ────────────────────────────────────
DDL = """
DROP TABLE IF EXISTS keyakinan_pra_izin;
DROP TABLE IF EXISTS keyakinan_model;
DROP TABLE IF EXISTS keyakinan_ringkas;
CREATE TABLE keyakinan_pra_izin (
    kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup), tahun_izin INTEGER, kelas TEXT,
    durasi_sk INTEGER,
    e_lubang_pra_izin INTEGER, f_ippkh_lebih_tua INTEGER,
    a_kontrak_karya INTEGER, b_durasi_pendek INTEGER,
    c_masa_diwarisi INTEGER, g_registri_beda INTEGER,
    d_hansen_pra_dominan INTEGER,
    n_sinyal INTEGER, bukti_langsung INTEGER,
    peluang_model_r REAL, peluang_model_rs REAL, peluang_akhir REAL,
    hilang_sejak_2001_ha REAL, hilang_sejak_sk_ha REAL, hilang_harapan_ha REAL);
CREATE TABLE keyakinan_model (
    model TEXT, penebak TEXT, koefisien REAL, rasio_odds REAL,
    auc REAL, PRIMARY KEY (model, penebak));
CREATE TABLE keyakinan_ringkas (kunci TEXT PRIMARY KEY, nilai TEXT);
"""

_METODE = ("Regresi logistik IRLS (numpy): petunjuk registri (A,B,C,G[,D]) → bukti langsung teramati (E|F). "
           "peluang_akhir = 1,0 bila bukti langsung ada, selain itu peluang model R. "
           f"hilang_harapan = p·hilang_sejak_2001 + (1−p)·hilang_sejak_sk; selang 95% = bootstrap {N_BOOTSTRAP} "
           f"tarikan (benih {BENIH}): beta ~ MVN(beta_hat, (X'WX)^-1) lalu Bernoulli(p_i); konsesi berbukti "
           "langsung dipatok p=1. Konsesi diurutkan kode_wiup supaya tarikan acak deterministik. "
           f"Jendela {TAHUN_MIN}–{TAHUN_MAX}. BATAS BAWAH: E dan F sama-sama kurang tercatat. "
           "Reproduksi: jalankan skrip ini setelah 01–06.")


def tulis_meta_semua(con) -> None:
    tulis_meta(con, "keyakinan_pra_izin",
               deskripsi="Per konsesi ber-tahun_izin: peluang terkalibrasi bahwa konsesi SUDAH BEROPERASI sebelum "
                         "tahun_izin tercatat (bukan 'peluang izin ini perpanjangan'), buku bukti tujuh sinyal "
                         "(A,B,C,D,E,F,G), dan akibatnya untuk atribusi kehilangan. Dipakai: tab 'keyakinan "
                         "pra-izin' halaman guna lahan, v_konsesi.keyakinan_pra_izin, atribusi jendela tesis. "
                         "Menggantikan kurung keras INDIKASI/POLOS.",
               sumber=SUMBER, metode=_METODE, skrip=SKRIP, lisensi=LISENSI_K, kolom=[
                   ("kode_wiup", "Kode WIUP konsesi.", "-", SUMBER),
                   ("tahun_izin", "Tahun izin tercatat di registri (konsesi.tahun_izin) — inilah yang diuji, "
                    "bukan diandaikan benar.", "-", SUMBER),
                   ("kelas", "Vonis izin_klasifikasi (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI) — pembanding, "
                    "BUKAN penebak model.", "-", SUMBER),
                   ("durasi_sk", "Jangka SK (tahun). Diutamakan Geoportal (konsesi.tgl_berakhir − tgl_berlaku); "
                    "jatuh ke MinerbaOne (konsesi_registri) bila tak ada.", "tahun(akhir) − tahun(berlaku)", SUMBER),
                   ("e_lubang_pra_izin", "BUKTI LANGSUNG (fisik). 1 bila lubang tambang MapBiomas kelas 30 >= "
                    f"{AMBANG_PIT_HA:g} ha bertahan >= {AMBANG_PIT_TAHUN} tahun beruntun pada tahun < tahun_izin. "
                    "Ambang & keteguhan menahan derau piksel.",
                    f"mapbiomas_tahunan.ha >= {AMBANG_PIT_HA:g} pada >= {AMBANG_PIT_TAHUN} tahun beruntun < tahun_izin",
                    SUMBER),
                   ("f_ippkh_lebih_tua", "BUKTI LANGSUNG (dokumen). 1 bila SK IPPKH terawal lebih tua dari "
                    "tahun_izin.", "tahun(ippkh.tgl_ippkh_awal) < tahun_izin", SUMBER),
                   ("a_kontrak_karya", "PETUNJUK REGISTRI. 1 bila PKP2B/KK ber-tahun_izin >= 2009 — kemustahilan "
                    "hukum (kontrak karya berhenti terbit sejak UU 4/2009).",
                    "jenis_izin IN ('PKP2B','KK') AND tahun_izin >= 2009", SUMBER),
                   ("b_durasi_pendek", "PETUNJUK REGISTRI. 1 bila SK Operasi Produksi berjangka < 20 tahun "
                    "(UU 4/2009 Ps. 47).", "kegiatan='OPERASI PRODUKSI' AND durasi_sk < 20", SUMBER),
                   ("c_masa_diwarisi", "PETUNJUK REGISTRI. 1 bila tahun tgl_berlaku < tahun_izin — izin 'baru' "
                    "yang membawa masa berlaku pendahulunya.", "izin_klasifikasi.masa_berlaku_diwarisi", SUMBER),
                   ("g_registri_beda", "PETUNJUK REGISTRI. 1 bila MinerbaOne (konsesi_registri) dan Geoportal "
                    "(konsesi) TIDAK sepakat soal durasi SK. Rasio odds ~0,9 — sinyal ini tidak berfungsi; "
                    "dilaporkan apa adanya.", "durasi_geoportal <> durasi_minerba", SUMBER),
                   ("d_hansen_pra_dominan", "PETUNJUK satelit (hanya model RS). 1 bila > 50% kehilangan Hansen "
                    "terjadi sebelum tahun_izin.", "izin_klasifikasi.pra_izin_dominan", SUMBER),
                   ("n_sinyal", "Cacah sinyal terpenuhi (A..G + bukti langsung) — ringkasan kasar, BUKAN skor "
                    "berbobot.", "A+B+C+D+G+bukti_langsung", SUMBER),
                   ("bukti_langsung", "1 bila E atau F teramati → peluang dipatok 1,0 (kegiatannya TERLIHAT).",
                    "E OR F", SUMBER),
                   ("peluang_model_r", "Peluang model R (registri saja: A,B,C,G).", "logistik(A,B,C,G) → E|F", SUMBER),
                   ("peluang_model_rs", "Peluang model RS (+D satelit). Uji ketahanan; D berhimpitan makna dgn "
                    "sasaran, jadi bukan angka utama.", "logistik(A,B,C,G,D) → E|F", SUMBER),
                   ("peluang_akhir", "Peluang yang dipakai atribusi (0..1).",
                    "1,0 bila bukti_langsung, selain itu peluang_model_r", SUMBER),
                   ("hilang_sejak_2001_ha", f"Cabang 'sudah aktif sejak awal': kehilangan Hansen {TAHUN_MIN}–"
                    f"{TAHUN_MAX} (ha).", f"Σ hansen_tahunan.hilang_ha {TAHUN_MIN}..{TAHUN_MAX}", SUMBER),
                   ("hilang_sejak_sk_ha", "Cabang 'baru mulai di tahun SK': kehilangan sejak max(tahun_izin, "
                    f"{TAHUN_MIN}) (ha).", f"Σ hilang_ha max(tahun_izin,{TAHUN_MIN})..{TAHUN_MAX}", SUMBER),
                   ("hilang_harapan_ha", "Taksiran harapan per konsesi (ha) — inilah pengganti kurung keras "
                    "INDIKASI/POLOS.", "p·hilang_sejak_2001_ha + (1−p)·hilang_sejak_sk_ha", SUMBER),
               ])
    tulis_meta(con, "keyakinan_model",
               deskripsi="Koefisien, rasio odds, dan AUC tiap model keyakinan (R & RS) — supaya angkanya bisa "
                         "diaudit, bukan kotak hitam. Dipakai: tabel model di tab keyakinan pra-izin.",
               sumber="keyakinan_pra_izin (tabel ini)", metode="IRLS numpy tanpa sklearn; AUC Mann-Whitney.",
               skrip=SKRIP, lisensi=LISENSI_K, kolom=[
                   ("model", "R (registri saja) atau RS (+ satelit D).", "-", SUMBER),
                   ("penebak", "Nama penebak, atau '(intersep)'.", "-", SUMBER),
                   ("koefisien", "Koefisien logistik (skala log-odds).", "IRLS", SUMBER),
                   ("rasio_odds", "Rasio odds — lebih mudah dibaca daripada koefisien.", "exp(koefisien)", SUMBER),
                   ("auc", "Daya pisah model (sama untuk semua baris model itu). 0,5 = tak berinformasi; "
                    "dilaporkan apa adanya.", "statistik Mann-Whitney", SUMBER),
               ])
    tulis_meta(con, "keyakinan_ringkas",
               deskripsi=f"Akibat agregat untuk atribusi {TAHUN_MIN}–{TAHUN_MAX}: batas bawah (semua sejak SK), "
                         "taksiran harapan berbobot peluang, selang bootstrap 95%, batas atas (semua sejak 2001), "
                         "plus parameter (n_bootstrap, benih, jendela, ambang). Dipakai: dashboard-stats.keyakinan, "
                         "angka atribusi tesis.",
               sumber="keyakinan_pra_izin (tabel ini)", metode=_METODE, skrip=SKRIP, lisensi=LISENSI_K, kolom=[
                   ("kunci", "Nama besaran: hilang_batas_bawah_ha, hilang_harapan_ha, hilang_bootstrap_lo_ha, "
                    "hilang_bootstrap_hi_ha, hilang_batas_atas_ha, n_bootstrap, benih, jendela, ambang_lubang_ha, "
                    "ambang_lubang_tahun.", "-", SUMBER),
                   ("nilai", "Nilainya sebagai teks (angka ha 2 desimal, atau parameter).", "-", SUMBER),
               ])


def main(argv=None) -> int:
    ap = argparser(__doc__)
    a = ap.parse_args(argv)
    t_mulai = time.time()
    con = buka(a.db)
    wajib_tabel(con, "konsesi", "konsesi_registri", "izin_klasifikasi", "ippkh", "hansen_tahunan", "mapbiomas_tahunan")
    data = kumpulkan(con)
    if not data:
        gagal("tidak ada konsesi ber-tahun_izin")
    print(f"{len(data)} konsesi ber-tahun_izin")

    y = np.array([d["E_lubang_pra_izin"] or d["F_ippkh_lebih_tua"] for d in data], dtype=float)
    print(f"Bukti langsung pra-izin teramati: {int(y.sum())} ({y.mean() * 100:.1f}%) — "
          f"E={sum(d['E_lubang_pra_izin'] for d in data)}, F={sum(d['F_ippkh_lebih_tua'] for d in data)}")

    hasil = {}
    for nama, penebak in (("R", PENEBAK_R), ("RS", PENEBAK_RS)):
        X = np.array([[d[k] for k in penebak] for d in data], dtype=float)
        beta, kov, konv = logistik(X, y)
        p = ramal(beta, X)
        skor = auc(y, p)
        hasil[nama] = (penebak, beta, p, skor, konv, kov, X)
        print(f"Model {nama}: AUC = {skor:.3f}{'' if konv else '  (PERINGATAN: IRLS tidak konvergen)'}")
        for k, b in zip(("(intersep)",) + penebak, beta):
            print(f"  {k:<24} {b:+.3f}   (rasio odds {np.exp(b):.2f})")

    _, beta, p_model, _, _, kov_r, X_r = hasil["R"]
    p_rs = hasil["RS"][2]

    baris = []
    for d, pm, prs in zip(data, p_model, p_rs):
        langsung = int(d["E_lubang_pra_izin"] or d["F_ippkh_lebih_tua"])
        p_akhir = 1.0 if langsung else float(pm)
        n_sinyal = sum(d[k] for k in PENEBAK_RS) + langsung
        harapan = p_akhir * d["hilang_sejak_2001_ha"] + (1 - p_akhir) * d["hilang_sejak_sk_ha"]
        baris.append((
            d["kode_wiup"], d["tahun_izin"], d["kelas"], d["durasi_sk"],
            d["E_lubang_pra_izin"], d["F_ippkh_lebih_tua"], d["A_kontrak_karya"], d["B_durasi_pendek"],
            d["C_masa_diwarisi"], d["G_registri_beda"], d["D_hansen_pra_dominan"], n_sinyal, langsung,
            round(float(pm), 4), round(float(prs), 4), round(p_akhir, 4),
            d["hilang_sejak_2001_ha"], d["hilang_sejak_sk_ha"], round(harapan, 4)))

    # ── akibat untuk atribusi + selang bootstrap (galat koefisien + keragaman Bernoulli) ──
    p_arr = np.array([b[15] for b in baris])
    l2001 = np.array([b[16] for b in baris])
    lsk = np.array([b[17] for b in baris])
    harapan = float((p_arr * l2001 + (1 - p_arr) * lsk).sum())
    rng = np.random.default_rng(BENIH)
    langsung = np.array([b[12] for b in baris], dtype=bool)
    tarikan = np.empty(N_BOOTSTRAP)
    for i in range(N_BOOTSTRAP):
        beta_i = rng.multivariate_normal(beta, kov_r)
        p_i = np.where(langsung, 1.0, ramal(beta_i, X_r))
        tarikan[i] = float(np.where(rng.random(len(p_i)) < p_i, l2001, lsk).sum())
    lo, hi = (float(v) for v in np.percentile(tarikan, [2.5, 97.5]))
    bawah, atas = float(lsk.sum()), float(l2001.sum())
    if not (bawah <= lo <= harapan <= hi <= atas):
        gagal(f"selang tak konsisten: bawah {bawah:.0f} lo {lo:.0f} harapan {harapan:.0f} hi {hi:.0f} atas {atas:.0f}")

    print(f"\n── Akibat untuk atribusi ({TAHUN_MIN}-{TAHUN_MAX}) ──")
    print(f"  batas bawah (semua sejak SK)     : {bawah:,.0f} ha")
    print(f"  taksiran harapan berbobot peluang: {harapan:,.0f} ha")
    print(f"  selang bootstrap 95%             : {lo:,.0f} – {hi:,.0f} ha")
    print(f"  batas atas (semua sejak 2001)    : {atas:,.0f} ha")

    con.executescript(DDL)
    con.executemany("INSERT INTO keyakinan_pra_izin VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", baris)
    for nama, (penebak, beta_m, _p, skor, _k, _kov, _X) in hasil.items():
        for k, b in zip(("(intersep)",) + penebak, beta_m):
            con.execute("INSERT INTO keyakinan_model VALUES (?,?,?,?,?)",
                        (nama, k, float(b), float(np.exp(b)), float(skor)))
    con.executemany("INSERT INTO keyakinan_ringkas VALUES (?,?)", [
        ("hilang_batas_bawah_ha", f"{bawah:.2f}"), ("hilang_harapan_ha", f"{harapan:.2f}"),
        ("hilang_bootstrap_lo_ha", f"{lo:.2f}"), ("hilang_bootstrap_hi_ha", f"{hi:.2f}"),
        ("hilang_batas_atas_ha", f"{atas:.2f}"), ("n_bootstrap", str(N_BOOTSTRAP)), ("benih", str(BENIH)),
        ("jendela", f"{TAHUN_MIN}-{TAHUN_MAX}"), ("ambang_lubang_ha", str(AMBANG_PIT_HA)),
        ("ambang_lubang_tahun", str(AMBANG_PIT_TAHUN))])
    tulis_meta_semua(con)
    tandai_selesai(con, "08_keyakinan", himpunan=a.himpunan, n_konsesi=len(baris),
                   hilang_harapan_ha=f"{harapan:.2f}", auc_r=f"{hasil['R'][3]:.4f}", auc_rs=f"{hasil['RS'][3]:.4f}")
    con.close()
    print(f"Selesai {SKRIP} ({time.time() - t_mulai:.1f} s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
