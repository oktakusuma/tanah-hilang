"""Ekspektasi skema `tanah-hilang.db` untuk verifier (W4) — salinan EKSPLISIT dari pipeline/SKEMA.md.

Dua bentuk yang saling mengunci:
  * `DDL`   : pernyataan CREATE per objek (verbatim SKEMA.md §0–§8; §6–§7 = kolom arsip + rename).
              Dipakai test untuk membangun DB sintetis yang memenuhi kontrak.
  * `KOLOM` : daftar kolom per objek yang DITUNTUT verifier (10_verifikasi.py pemeriksaan 1).
              Ditulis tangan — bukan hasil parsing markdown — dan test_w4 memastikan
              KOLOM == kolom hasil eksekusi DDL, supaya keduanya tak bisa saling menyimpang.

Kalau SKEMA.md berubah, ubah KEDUANYA di sini (dan test akan menegur bila lupa salah satu).
"""
from __future__ import annotations

import sqlite3

from .w1_util import DDL_V_KONSESI

# ── DDL verbatim SKEMA.md (urutan = urutan dependensi FK) ────────────────────────────────────
DDL: dict[str, str] = {
    "sumber": """CREATE TABLE sumber (
  id TEXT PRIMARY KEY, nama TEXT NOT NULL, versi TEXT, lisensi TEXT NOT NULL, url TEXT,
  tanggal_akses TEXT, cakupan_tahun TEXT, sitasi TEXT, catatan TEXT)""",
    "bangun": """CREATE TABLE bangun (kunci TEXT PRIMARY KEY, nilai TEXT NOT NULL, ditulis TEXT NOT NULL)""",
    "analysis_meta": """CREATE TABLE analysis_meta (
  nama_tabel TEXT PRIMARY KEY, deskripsi TEXT NOT NULL, sumber TEXT NOT NULL,
  metode TEXT NOT NULL, skrip TEXT NOT NULL, lisensi TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'INTI')""",
    "column_meta": """CREATE TABLE column_meta (
  nama_tabel TEXT NOT NULL, nama_kolom TEXT NOT NULL, deskripsi TEXT NOT NULL,
  rumus TEXT, sumber TEXT, PRIMARY KEY (nama_tabel, nama_kolom))""",
    # §1
    "konsesi": """CREATE TABLE konsesi (
  kode_wiup TEXT PRIMARY KEY, nama_usaha TEXT NOT NULL, sk_iup TEXT, komoditas TEXT NOT NULL,
  jenis_izin TEXT, kegiatan TEXT, luas_sk_ha REAL, luas_poligon_ha REAL NOT NULL, tahun_izin INTEGER,
  tgl_berlaku TEXT, tgl_berakhir TEXT, asal_tanggal TEXT,
  provinsi TEXT, kabupaten TEXT, kabupaten_norm TEXT, lokasi TEXT, cnc TEXT,
  geometri_geojson TEXT NOT NULL,
  bbox_min_lon REAL, bbox_min_lat REAL, bbox_max_lon REAL, bbox_max_lat REAL)""",
    "konsesi_registri": """CREATE TABLE konsesi_registri (
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  cocok INTEGER NOT NULL, strategi_cocok TEXT, id_perizinan TEXT, id_badan_usaha TEXT,
  nama_badan_usaha TEXT, nib TEXT, npwp TEXT, alamat TEXT, kode_pos TEXT, jenis_badan_usaha TEXT,
  tanggal_berlaku TEXT, tanggal_berakhir TEXT, tanggal_penetapan TEXT,
  tahap_kegiatan TEXT, status_cnc TEXT, url_minerbaone TEXT)""",
    "kepadatan_penduduk": """CREATE TABLE kepadatan_penduduk (
  kode_kabkot TEXT, provinsi TEXT, kabupaten TEXT, kabupaten_norm TEXT,
  tahun INTEGER, kepadatan REAL, satuan TEXT, sumber TEXT, PRIMARY KEY (kode_kabkot, tahun))""",
    # §2
    "hansen_ringkas": """CREATE TABLE hansen_ringkas (
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  hutan_2000_ha REAL NOT NULL, hilang_2001_2024_ha REAL NOT NULL, pct_hutan_2000 REAL,
  tahun_puncak INTEGER, tile_hansen TEXT)""",
    "hansen_tahunan": """CREATE TABLE hansen_tahunan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup),
  tahun INTEGER NOT NULL CHECK (tahun BETWEEN 2001 AND 2024), hilang_ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, tahun))""",
    "izin_laju": """CREATE TABLE izin_laju (
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup), tahun_izin INTEGER,
  hilang_pra_ha REAL, n_tahun_pra INTEGER, laju_pra_ha_thn REAL,
  hilang_pasca_ha REAL, n_tahun_pasca INTEGER, laju_pasca_ha_thn REAL,
  rasio_pasca_pra REAL, vonis TEXT NOT NULL)""",
    # §3
    "izin_klasifikasi": """CREATE TABLE izin_klasifikasi (
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup), kelas TEXT NOT NULL, bukti TEXT,
  dasar TEXT NOT NULL, durasi_sk INTEGER, masa_berlaku_diwarisi INTEGER NOT NULL, pra_izin_dominan INTEGER,
  tahun_mulai_indikasi INTEGER)""",
    # §4
    "mapbiomas_kelas": """CREATE TABLE mapbiomas_kelas (
  kelas INTEGER PRIMARY KEY, nama TEXT NOT NULL, kelompok TEXT NOT NULL, hex TEXT NOT NULL)""",
    "mapbiomas_gabungan": """CREATE TABLE mapbiomas_gabungan (
  gabungan TEXT NOT NULL, kelas INTEGER NOT NULL REFERENCES mapbiomas_kelas(kelas), keterangan TEXT,
  PRIMARY KEY (gabungan, kelas))""",
    "mapbiomas_tahunan": """CREATE TABLE mapbiomas_tahunan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup),
  tahun INTEGER NOT NULL CHECK (tahun BETWEEN 2001 AND 2024),
  kelas INTEGER NOT NULL REFERENCES mapbiomas_kelas(kelas),
  piksel INTEGER NOT NULL, ha REAL NOT NULL, PRIMARY KEY (kode_wiup, tahun, kelas))""",
    "v_mapbiomas_ringkas": """CREATE VIEW v_mapbiomas_ringkas AS
  SELECT kode_wiup, tahun,
         SUM(CASE WHEN kelas IN (3,5,76) THEN ha ELSE 0 END) AS hutan_ha,
         SUM(CASE WHEN kelas = 30 THEN ha ELSE 0 END)        AS tambang_ha,
         SUM(CASE WHEN kelas = 35 THEN ha ELSE 0 END)        AS sawit_ha,
         SUM(CASE WHEN kelas <> 27 THEN ha ELSE 0 END)       AS teramati_ha,
         SUM(ha) AS total_ha
  FROM mapbiomas_tahunan GROUP BY kode_wiup, tahun""",
    "transisi_kohort": """CREATE TABLE transisi_kohort (
  jenis TEXT NOT NULL, label TEXT PRIMARY KEY, awal INTEGER NOT NULL, akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, n_keluar INTEGER NOT NULL, catatan TEXT)""",
    "transisi_konsesi": """CREATE TABLE transisi_konsesi (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), label TEXT NOT NULL REFERENCES transisi_kohort(label),
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, label, kelas_awal, kelas_akhir))""",
    "v_transisi_aliran": """CREATE VIEW v_transisi_aliran AS
  SELECT t.label, t.kelas_awal, t.kelas_akhir, a.nama AS nama_awal, b.nama AS nama_akhir,
         COUNT(DISTINCT t.kode_wiup) AS n_konsesi, SUM(t.piksel) AS piksel, ROUND(SUM(t.ha),2) AS ha
  FROM transisi_konsesi t JOIN mapbiomas_kelas a ON a.kelas=t.kelas_awal JOIN mapbiomas_kelas b ON b.kelas=t.kelas_akhir
  GROUP BY t.label, t.kelas_awal, t.kelas_akhir""",
    "transisi_pasangan_aktif": """CREATE TABLE transisi_pasangan_aktif (
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  baru_aktif INTEGER NOT NULL CHECK (baru_aktif IN (0, 1)),
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (tahun_awal, tahun_akhir, baru_aktif, kelas_awal, kelas_akhir),
  CHECK (tahun_awal < tahun_akhir))""",
    "transisi_pasangan": """CREATE TABLE transisi_pasangan (
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (tahun_awal, tahun_akhir, kelas_awal, kelas_akhir), CHECK (tahun_awal < tahun_akhir))""",
    # §5
    "kawasan_hutan": """CREATE TABLE kawasan_hutan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), fungsi_kode TEXT, fungsi_nama TEXT NOT NULL,
  luas_ha REAL NOT NULL, PRIMARY KEY (kode_wiup, fungsi_nama))""",
    "ippkh": """CREATE TABLE ippkh (
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  punya_ippkh INTEGER NOT NULL, punya_ippkh_tambang INTEGER NOT NULL,
  n_ippkh INTEGER NOT NULL, n_ippkh_tambang INTEGER NOT NULL,
  luas_irisan_ha REAL, luas_ippkh_sk_ha REAL, rasio_ippkh_thd_luas_sk REAL,
  tgl_ippkh_awal TEXT, tgl_ippkh_akhir TEXT, cocok_nama INTEGER)""",
    "ippkh_irisan": """CREATE TABLE ippkh_irisan (
  kode_wiup TEXT NOT NULL, id_ippkh TEXT NOT NULL, layer TEXT, nama_ppkh TEXT, no_ppkh TEXT,
  tgl_ppkh TEXT, tgl_berakhir TEXT, jenis_ppkh TEXT, status TEXT,
  luas_ppkh_sk_ha REAL, luas_ppkh_hitung_ha REAL, luas_irisan_ha REAL, pangsa_ippkh_di_konsesi REAL, cocok_nama INTEGER,
  PRIMARY KEY (kode_wiup, id_ippkh))""",
    # §6 — kolom arsip, loss_ha → hilang_ha
    "umur_izin_kurun": """CREATE TABLE umur_izin_kurun (
  rancangan TEXT NOT NULL, kurun TEXT NOT NULL, umur_awal INTEGER NOT NULL, umur_akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, konsesi_tahun REAL NOT NULL, hutan_awal_ha REAL NOT NULL, hutan_tahun_ha REAL NOT NULL,
  hilang_ha REAL NOT NULL, laju_bahaya_per_tahun REAL, konsesi_luas_tahun_ha REAL NOT NULL, laju_luas_per_tahun REAL,
  catatan TEXT NOT NULL, PRIMARY KEY (rancangan, kurun))""",
    "umur_izin_tahunan": """CREATE TABLE umur_izin_tahunan (
  rancangan TEXT NOT NULL, umur_relatif INTEGER NOT NULL, n_konsesi INTEGER NOT NULL, hilang_ha REAL NOT NULL,
  hutan_ha REAL NOT NULL, laju_bahaya_per_tahun REAL, laju_luas_per_tahun REAL,
  PRIMARY KEY (rancangan, umur_relatif))""",
    "umur_izin_konsesi": """CREATE TABLE umur_izin_konsesi (
  rancangan TEXT NOT NULL, kode_wiup TEXT NOT NULL, kurun TEXT NOT NULL,
  tahun_teramati INTEGER NOT NULL, tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  hutan_awal_ha REAL NOT NULL, hilang_ha REAL NOT NULL, laju_bahaya_per_tahun REAL,
  PRIMARY KEY (rancangan, kode_wiup, kurun))""",
    # §7 — kolom arsip, iup_year → tahun_izin, loss_* → hilang_*
    "keyakinan_pra_izin": """CREATE TABLE keyakinan_pra_izin (
  kode_wiup TEXT PRIMARY KEY, tahun_izin INTEGER, kelas TEXT, durasi_sk INTEGER,
  e_lubang_pra_izin INTEGER, f_ippkh_lebih_tua INTEGER, a_kontrak_karya INTEGER, b_durasi_pendek INTEGER,
  c_masa_diwarisi INTEGER, g_registri_beda INTEGER, d_hansen_pra_dominan INTEGER,
  n_sinyal INTEGER, bukti_langsung INTEGER,
  peluang_model_r REAL, peluang_model_rs REAL, peluang_akhir REAL,
  hilang_sejak_2001_ha REAL, hilang_sejak_sk_ha REAL, hilang_harapan_ha REAL)""",
    "keyakinan_model": """CREATE TABLE keyakinan_model (
  model TEXT, penebak TEXT, koefisien REAL, rasio_odds REAL, auc REAL, PRIMARY KEY (model, penebak))""",
    "keyakinan_ringkas": """CREATE TABLE keyakinan_ringkas (kunci TEXT PRIMARY KEY, nilai TEXT)""",
    # §8
    "v_konsesi": DDL_V_KONSESI,   # satu sumber: pipeline/lib/w1_util.py (W1)
}

VIEW = {"v_mapbiomas_ringkas", "v_transisi_aliran", "v_konsesi"}

# ── Kolom yang DITUNTUT verifier (ditulis tangan; test_w4 mengunci ke DDL di atas) ──────────
_KONSESI = ["kode_wiup", "nama_usaha", "sk_iup", "komoditas", "jenis_izin", "kegiatan", "luas_sk_ha",
            "luas_poligon_ha", "tahun_izin", "tgl_berlaku", "tgl_berakhir", "asal_tanggal", "provinsi",
            "kabupaten", "kabupaten_norm", "lokasi", "cnc", "geometri_geojson",
            "bbox_min_lon", "bbox_min_lat", "bbox_max_lon", "bbox_max_lat"]

KOLOM: dict[str, list[str]] = {
    "sumber": ["id", "nama", "versi", "lisensi", "url", "tanggal_akses", "cakupan_tahun", "sitasi", "catatan"],
    "bangun": ["kunci", "nilai", "ditulis"],
    "analysis_meta": ["nama_tabel", "deskripsi", "sumber", "metode", "skrip", "lisensi", "status"],
    "column_meta": ["nama_tabel", "nama_kolom", "deskripsi", "rumus", "sumber"],
    "konsesi": _KONSESI,
    "konsesi_registri": ["kode_wiup", "cocok", "strategi_cocok", "id_perizinan", "id_badan_usaha",
                         "nama_badan_usaha", "nib", "npwp", "alamat", "kode_pos", "jenis_badan_usaha",
                         "tanggal_berlaku", "tanggal_berakhir", "tanggal_penetapan", "tahap_kegiatan",
                         "status_cnc", "url_minerbaone"],
    "kepadatan_penduduk": ["kode_kabkot", "provinsi", "kabupaten", "kabupaten_norm", "tahun", "kepadatan",
                           "satuan", "sumber"],
    "hansen_ringkas": ["kode_wiup", "hutan_2000_ha", "hilang_2001_2024_ha", "pct_hutan_2000", "tahun_puncak",
                       "tile_hansen"],
    "hansen_tahunan": ["kode_wiup", "tahun", "hilang_ha"],
    "izin_laju": ["kode_wiup", "tahun_izin", "hilang_pra_ha", "n_tahun_pra", "laju_pra_ha_thn",
                  "hilang_pasca_ha", "n_tahun_pasca", "laju_pasca_ha_thn", "rasio_pasca_pra", "vonis"],
    "izin_klasifikasi": ["kode_wiup", "kelas", "bukti", "dasar", "durasi_sk", "masa_berlaku_diwarisi",
                         "pra_izin_dominan", "tahun_mulai_indikasi"],
    "mapbiomas_kelas": ["kelas", "nama", "kelompok", "hex"],
    "mapbiomas_gabungan": ["gabungan", "kelas", "keterangan"],
    "mapbiomas_tahunan": ["kode_wiup", "tahun", "kelas", "piksel", "ha"],
    "v_mapbiomas_ringkas": ["kode_wiup", "tahun", "hutan_ha", "tambang_ha", "sawit_ha", "teramati_ha", "total_ha"],
    "transisi_kohort": ["jenis", "label", "awal", "akhir", "n_konsesi", "n_keluar", "catatan"],
    "transisi_konsesi": ["kode_wiup", "label", "tahun_awal", "tahun_akhir", "kelas_awal", "kelas_akhir",
                         "piksel", "ha"],
    "v_transisi_aliran": ["label", "kelas_awal", "kelas_akhir", "nama_awal", "nama_akhir", "n_konsesi",
                          "piksel", "ha"],
    "transisi_pasangan": ["tahun_awal", "tahun_akhir", "kelas_awal", "kelas_akhir", "n_konsesi", "piksel", "ha"],
    "transisi_pasangan_aktif": ["tahun_awal", "tahun_akhir", "baru_aktif", "kelas_awal", "kelas_akhir", "n_konsesi", "piksel", "ha"],
    "kawasan_hutan": ["kode_wiup", "fungsi_kode", "fungsi_nama", "luas_ha"],
    "ippkh": ["kode_wiup", "punya_ippkh", "punya_ippkh_tambang", "n_ippkh", "n_ippkh_tambang",
              "luas_irisan_ha", "luas_ippkh_sk_ha", "rasio_ippkh_thd_luas_sk", "tgl_ippkh_awal",
              "tgl_ippkh_akhir", "cocok_nama"],
    "ippkh_irisan": ["kode_wiup", "id_ippkh", "layer", "nama_ppkh", "no_ppkh", "tgl_ppkh", "tgl_berakhir",
                     "jenis_ppkh", "status", "luas_ppkh_sk_ha", "luas_ppkh_hitung_ha", "luas_irisan_ha",
                     "pangsa_ippkh_di_konsesi", "cocok_nama"],
    "umur_izin_kurun": ["rancangan", "kurun", "umur_awal", "umur_akhir", "n_konsesi", "konsesi_tahun",
                        "hutan_awal_ha", "hutan_tahun_ha", "hilang_ha", "laju_bahaya_per_tahun",
                        "konsesi_luas_tahun_ha", "laju_luas_per_tahun", "catatan"],
    "umur_izin_tahunan": ["rancangan", "umur_relatif", "n_konsesi", "hilang_ha", "hutan_ha",
                          "laju_bahaya_per_tahun", "laju_luas_per_tahun"],
    "umur_izin_konsesi": ["rancangan", "kode_wiup", "kurun", "tahun_teramati", "tahun_awal", "tahun_akhir",
                          "hutan_awal_ha", "hilang_ha", "laju_bahaya_per_tahun"],
    "keyakinan_pra_izin": ["kode_wiup", "tahun_izin", "kelas", "durasi_sk", "e_lubang_pra_izin",
                           "f_ippkh_lebih_tua", "a_kontrak_karya", "b_durasi_pendek", "c_masa_diwarisi",
                           "g_registri_beda", "d_hansen_pra_dominan", "n_sinyal", "bukti_langsung",
                           "peluang_model_r", "peluang_model_rs", "peluang_akhir",
                           "hilang_sejak_2001_ha", "hilang_sejak_sk_ha", "hilang_harapan_ha"],
    "keyakinan_model": ["model", "penebak", "koefisien", "rasio_odds", "auc"],
    "keyakinan_ringkas": ["kunci", "nilai"],
    "v_konsesi": _KONSESI + [
        "cocok", "strategi_cocok", "nama_badan_usaha", "nib", "alamat", "jenis_badan_usaha",
        "registri_tanggal_berlaku", "registri_tanggal_berakhir", "url_minerbaone",
        "hutan_2000_ha", "hilang_2001_2024_ha", "pct_hutan_2000", "tahun_puncak", "tile_hansen",
        "hilang_pra_ha", "n_tahun_pra", "laju_pra_ha_thn", "hilang_pasca_ha", "n_tahun_pasca",
        "laju_pasca_ha_thn", "rasio_pasca_pra", "vonis",
        "kelas_izin", "bukti_izin", "durasi_sk", "masa_berlaku_diwarisi", "pra_izin_dominan",
        "tahun_mulai_indikasi",
        "punya_ippkh", "punya_ippkh_tambang", "tgl_ippkh_awal",
        "keyakinan_pra_izin", "hilang_harapan_ha"],
}

# Tabel per konsesi yang kode_wiup-nya wajib ada di `konsesi` (integritas rujukan).
ANAK_KONSESI = ["konsesi_registri", "hansen_ringkas", "hansen_tahunan", "izin_laju", "izin_klasifikasi",
                "mapbiomas_tahunan", "transisi_konsesi", "kawasan_hutan", "ippkh", "ippkh_irisan",
                "umur_izin_konsesi", "keyakinan_pra_izin"]

# Kunci `bangun` wajib (SKEMA §0).
BANGUN_WAJIB = ["himpunan", "pipeline_versi", "git_commit", "konsesi.n", "konsesi.hash_geometri",
                "mapbiomas.hash_geometri"]

# Kunci `keyakinan_ringkas` (arsip `loss_*` → `hilang_*`).
KEYAKINAN_KUNCI = ["hilang_batas_bawah_ha", "hilang_harapan_ha", "hilang_bootstrap_lo_ha",
                   "hilang_bootstrap_hi_ha", "hilang_batas_atas_ha", "n_bootstrap", "benih", "jendela",
                   "ambang_lubang_ha", "ambang_lubang_tahun"]

VONIS = ["accelerated_post_iup", "decelerated_post_iup", "loss_only_after_iup", "stable", "no_loss_either",
         "izin_setelah_jendela", "tanpa_tahun_izin"]

TAHUN_AWAL, TAHUN_AKHIR = 2001, 2024
N_PASANGAN = (TAHUN_AKHIR - TAHUN_AWAL + 1) * (TAHUN_AKHIR - TAHUN_AWAL) // 2   # 276


def kolom_objek(con: sqlite3.Connection, nama: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_xinfo('{nama}')")]


def objek_di_db(con: sqlite3.Connection) -> dict[str, str]:
    """nama → 'table'|'view' untuk semua objek non-internal."""
    return {r[0]: r[1] for r in con.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")}


def buat_skema_kosong(con: sqlite3.Connection) -> None:
    """Eksekusi seluruh DDL (untuk DB sintetis di test)."""
    for ddl in DDL.values():
        con.execute(ddl)
