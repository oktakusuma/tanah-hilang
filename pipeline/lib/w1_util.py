"""Helper bersama workstream W1 (01_identitas, 02_hansen, 03_izin).

Berisi (1) konversi nilai mentah (angka/tanggal Geoportal), (2) penormal SK & nama badan
usaha — PORT PERSIS dari scripts/match_harder.py supaya hasil pencocokan T1–T3 identik
dengan arsip, (3) definisi view `v_konsesi` (SKEMA.md §8) + pendaftaran meta-nya.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher

from .meta import LISENSI, tulis_meta

TAHUN_AWAL, TAHUN_AKHIR = 2001, 2024          # jendela tesis (SKEMA.md)
TAHUN_JENDELA = range(TAHUN_AWAL, TAHUN_AKHIR + 1)


# ─────────────────────────── nilai mentah ────────────────────────────────
def ke_angka(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def ke_bulat(v, default=None):
    n = ke_angka(v)
    return int(n) if n is not None else default


def teks_upper(v) -> str | None:
    """strip + UPPER, None bila kosong — cegah 'Batubara' vs 'BATUBARA' memecah GROUP BY
    (port build_combined_db.norm_upper)."""
    s = (v or "").strip().upper()
    return s or None


def tanggal_iso(v) -> str | None:
    """Terima 'YYYY-MM-DD…' (WIUP_Publish) atau epoch milidetik (layer lama / overlay
    kawasan hutan; dikonversi UTC seperti build_kawasan_hutan.tanggal). None bila tak
    terbaca."""
    if v is None or v == "":
        return None
    if isinstance(v, str):
        s = v.strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit():
            return s[:10]
        try:
            v = float(s)
        except ValueError:
            return None
    try:
        ms = float(v)
    except (TypeError, ValueError):
        return None
    if ms < 0 or ms > 5e12:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def tahun_dari(tgl: str | None) -> int | None:
    if not tgl or len(tgl) < 4 or not tgl[:4].isdigit():
        return None
    return int(tgl[:4])


def kabupaten_norm(nama: str | None) -> str | None:
    """Buang awalan 'KAB. ' / 'KABUPATEN ' / 'KOTA ' lalu UPPER — kunci join ke BPS
    (port build_combined_db.step_geoportal.normalize_kab)."""
    if not nama:
        return None
    n = nama.strip().upper()
    for pfx in ("KAB. ", "KABUPATEN ", "KOTA "):
        if n.startswith(pfx):
            n = n[len(pfx):]
            break
    return n.strip() or None


# ───────────────────── pencocokan registri (port match_harder) ──────────
def normalisasi_sk(sk: str | None) -> str:
    if not sk:
        return ""
    s = sk.upper().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[‐–—-]", "-", s)
    s = re.sub(r"[ \t]*([/\.,])[ \t]*", r"\1", s)
    return s


def hanya_digit(sk: str | None) -> str:
    return re.sub(r"\D", "", sk or "")


def normalisasi_nama(nama: str | None) -> str:
    if not nama:
        return ""
    n = nama.upper().strip()
    for sfx in [", PT", " PT", " TBK", " (TBK)", " CV", ", CV", " PERSERO", " (PERSERO)", " INDONESIA"]:
        n = n.replace(sfx, "")
    return re.sub(r"\s+", " ", n).strip()


def cocok_nama_fuzzy(nama_usaha: str | None, peta_nama: dict, ambang: float = 0.88):
    """T2: nama badan usaha termirip (SequenceMatcher.ratio) ≥ ambang.
    Hasil IDENTIK dgn match_harder.try_match_t2_fuzzy_name: kandidat dipangkas hanya
    bila batas atas rasionya (quick_ratio) sudah < skor terbaik saat ini / ambang —
    kandidat yang dipangkas tak mungkin menang, jadi pemenang & skornya sama persis."""
    target = normalisasi_nama(nama_usaha)
    if not target or len(target) < 4:
        return None
    # Urutan seq1=target, seq2=nama dipertahankan persis (SequenceMatcher tak simetris sempurna).
    sm = SequenceMatcher()
    sm.set_seq1(target)
    terbaik, skor = None, 0.0
    for nama, ids in peta_nama.items():
        if not nama:
            continue
        sm.set_seq2(nama)
        if sm.real_quick_ratio() < max(skor, ambang) or sm.quick_ratio() < max(skor, ambang):
            continue
        s = sm.ratio()
        if s > skor:
            skor, terbaik = s, (nama, ids, s)
    return terbaik if terbaik and skor >= ambang else None


# ───────────────────────────── v_konsesi ─────────────────────────────────
DDL_V_KONSESI = """
CREATE VIEW v_konsesi AS
  SELECT k.*, r.cocok, r.strategi_cocok, r.nama_badan_usaha, r.nib, r.alamat, r.jenis_badan_usaha,
         r.tanggal_berlaku AS registri_tanggal_berlaku, r.tanggal_berakhir AS registri_tanggal_berakhir, r.url_minerbaone,
         h.hutan_2000_ha, h.hilang_2001_2024_ha, h.pct_hutan_2000, h.tahun_puncak, h.tile_hansen,
         l.hilang_pra_ha, l.n_tahun_pra, l.laju_pra_ha_thn, l.hilang_pasca_ha, l.n_tahun_pasca, l.laju_pasca_ha_thn, l.rasio_pasca_pra, l.vonis,
         z.kelas AS kelas_izin, z.bukti AS bukti_izin, z.durasi_sk, z.masa_berlaku_diwarisi, z.pra_izin_dominan,
         z.tahun_mulai_indikasi,
         i.punya_ippkh, i.punya_ippkh_tambang, i.tgl_ippkh_awal,
         y.peluang_akhir AS keyakinan_pra_izin, y.hilang_harapan_ha
  FROM konsesi k
  LEFT JOIN konsesi_registri r USING (kode_wiup) LEFT JOIN hansen_ringkas h USING (kode_wiup)
  LEFT JOIN izin_laju l USING (kode_wiup) LEFT JOIN izin_klasifikasi z USING (kode_wiup)
  LEFT JOIN ippkh i USING (kode_wiup) LEFT JOIN keyakinan_pra_izin y USING (kode_wiup)
"""
TABEL_V_KONSESI = ("konsesi", "konsesi_registri", "hansen_ringkas", "izin_laju", "izin_klasifikasi",
                   "ippkh", "keyakinan_pra_izin")

# Kolom v_konsesi di luar k.* (nama → (deskripsi, rumus, sumber)). Kolom k.* disalin dari
# column_meta konsesi saat pendaftaran.
_KOLOM_V = [
    ("cocok", "1 bila konsesi tercocokkan ke registri MinerbaOne.", "konsesi_registri.cocok", "konsesi_registri"),
    ("strategi_cocok", "Strategi pencocokan (T0_exact/T1_norm_sk/T2_fuzzy_name/T3_digits).", "konsesi_registri.strategi_cocok", "konsesi_registri"),
    ("nama_badan_usaha", "Nama badan usaha menurut MinerbaOne.", "konsesi_registri.nama_badan_usaha", "konsesi_registri"),
    ("nib", "Nomor Induk Berusaha.", "konsesi_registri.nib", "konsesi_registri"),
    ("alamat", "Alamat badan usaha.", "konsesi_registri.alamat", "konsesi_registri"),
    ("jenis_badan_usaha", "Bentuk badan usaha (PT/CV/…).", "konsesi_registri.jenis_badan_usaha", "konsesi_registri"),
    ("registri_tanggal_berlaku", "Tanggal berlaku izin menurut MinerbaOne.", "konsesi_registri.tanggal_berlaku", "konsesi_registri"),
    ("registri_tanggal_berakhir", "Tanggal berakhir izin menurut MinerbaOne.", "konsesi_registri.tanggal_berakhir", "konsesi_registri"),
    ("url_minerbaone", "Tautan profil badan usaha di MinerbaOne.", "konsesi_registri.url_minerbaone", "konsesi_registri"),
    ("hutan_2000_ha", "Hutan tahun 2000 (kanopi ≥30%) di dalam konsesi, ha.", "hansen_ringkas.hutan_2000_ha", "hansen_ringkas"),
    ("hilang_2001_2024_ha", "Kehilangan tutupan pohon 2001–2024, ha.", "hansen_ringkas.hilang_2001_2024_ha", "hansen_ringkas"),
    ("pct_hutan_2000", "Persen kehilangan 2001–2024 terhadap hutan 2000.", "hansen_ringkas.pct_hutan_2000", "hansen_ringkas"),
    ("tahun_puncak", "Tahun kehilangan tahunan terbesar.", "hansen_ringkas.tahun_puncak", "hansen_ringkas"),
    ("tile_hansen", "Ubin GFC yang menaungi konsesi.", "hansen_ringkas.tile_hansen", "hansen_ringkas"),
    ("hilang_pra_ha", "Kehilangan 2001..tahun_izin−1, ha.", "izin_laju.hilang_pra_ha", "izin_laju"),
    ("n_tahun_pra", "Jumlah tahun pra-izin dalam jendela.", "izin_laju.n_tahun_pra", "izin_laju"),
    ("laju_pra_ha_thn", "Laju kehilangan pra-izin, ha/tahun.", "izin_laju.laju_pra_ha_thn", "izin_laju"),
    ("hilang_pasca_ha", "Kehilangan tahun_izin..2024, ha.", "izin_laju.hilang_pasca_ha", "izin_laju"),
    ("n_tahun_pasca", "Jumlah tahun pasca-izin dalam jendela.", "izin_laju.n_tahun_pasca", "izin_laju"),
    ("laju_pasca_ha_thn", "Laju kehilangan pasca-izin, ha/tahun.", "izin_laju.laju_pasca_ha_thn", "izin_laju"),
    ("rasio_pasca_pra", "Rasio laju pasca/pra.", "izin_laju.rasio_pasca_pra", "izin_laju"),
    ("vonis", "Vonis laju pra vs pasca izin.", "izin_laju.vonis", "izin_laju"),
    ("kelas_izin", "Kelas izin (IZIN_PERTAMA/PERPANJANGAN/TAK_DINILAI) — indikasi.", "izin_klasifikasi.kelas", "izin_klasifikasi"),
    ("tahun_mulai_indikasi", "Tahun konsesi dianggap mulai aktif menurut indikasi kelas izin (PERPANJANGAN: tahun_izin − 20) — jam poligon peta; INDIKASI, bukan kepastian.", "izin_klasifikasi.tahun_mulai_indikasi", "izin_klasifikasi"),
    ("bukti_izin", "Kekuatan bukti kelas izin (KUAT/INDIKASI).", "izin_klasifikasi.bukti", "izin_klasifikasi"),
    ("durasi_sk", "Jangka SK registri, tahun.", "izin_klasifikasi.durasi_sk", "izin_klasifikasi"),
    ("masa_berlaku_diwarisi", "1 bila tahun berlaku registri < tahun_izin.", "izin_klasifikasi.masa_berlaku_diwarisi", "izin_klasifikasi"),
    ("pra_izin_dominan", "1 bila >50% kehilangan terjadi sebelum tahun_izin.", "izin_klasifikasi.pra_izin_dominan", "izin_klasifikasi"),
    ("punya_ippkh", "1 bila memegang IPPKH aktif (batas bawah).", "ippkh.punya_ippkh", "ippkh"),
    ("punya_ippkh_tambang", "1 bila IPPKH-nya berjenis tambang.", "ippkh.punya_ippkh_tambang", "ippkh"),
    ("tgl_ippkh_awal", "Tanggal IPPKH tertua.", "ippkh.tgl_ippkh_awal", "ippkh"),
    ("keyakinan_pra_izin", "Peluang terkalibrasi sudah beroperasi sebelum tahun_izin.", "keyakinan_pra_izin.peluang_akhir", "keyakinan_pra_izin"),
    ("hilang_harapan_ha", "Harapan kehilangan yang teratribusi ke masa aktif izin, ha.", "keyakinan_pra_izin.hilang_harapan_ha", "keyakinan_pra_izin"),
]


def pastikan_v_konsesi(con: sqlite3.Connection, skrip: str) -> bool:
    """Buat ulang `v_konsesi` + meta-nya HANYA bila semua tabel yang di-join sudah ada
    (SQLite menolak CREATE VIEW yang merujuk tabel absen — bukan lazy). Kembalikan True
    bila view dibuat. Dipanggil di akhir 01/03 (W1) dan boleh dipanggil 09_sajikan (W4)."""
    ada = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    kurang = [t for t in TABEL_V_KONSESI if t not in ada]
    if kurang:
        print(f"  v_konsesi belum dibuat — tabel belum ada: {', '.join(kurang)} (akan dibuat di 09_sajikan)")
        con.execute("DROP VIEW IF EXISTS v_konsesi")
        con.execute("DELETE FROM analysis_meta WHERE nama_tabel='v_konsesi'")
        con.execute("DELETE FROM column_meta WHERE nama_tabel='v_konsesi'")
        return False
    con.execute("DROP VIEW IF EXISTS v_konsesi")
    con.execute(DDL_V_KONSESI)
    kolom_k = [(r[0], r[1], r[2], "konsesi") for r in con.execute(
        "SELECT nama_kolom, deskripsi, rumus FROM column_meta WHERE nama_tabel='konsesi'")]
    if not kolom_k:
        raise SystemExit("GAGAL: column_meta konsesi kosong — jalankan 01_identitas dulu")
    tulis_meta(con, "v_konsesi",
               deskripsi="View gabungan satu baris per konsesi (pengganti wiup_master): identitas + registri "
                         "MinerbaOne + ringkasan Hansen + laju pra/pasca izin + klasifikasi izin + IPPKH + "
                         "keyakinan pra-izin. Sumber tunggal API daftar/detail konsesi.",
               sumber="konsesi ⋈ konsesi_registri ⋈ hansen_ringkas ⋈ izin_laju ⋈ izin_klasifikasi ⋈ ippkh ⋈ keyakinan_pra_izin",
               metode="LEFT JOIN USING (kode_wiup); tak ada perhitungan baru — semua nilai apa adanya dari tabel sumber.",
               skrip=skrip, lisensi=LISENSI["turunan"], kolom=kolom_k + _KOLOM_V)
    return True
