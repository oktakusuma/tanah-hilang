"""HANYA UNTUK TEST/PENGEMBANGAN — siapkan DB uji berskema baru dengan menyalin tabel dari DB ARSIP
(kalimantan.db / mapbiomas.db lama) lewat pemetaan nama SKEMA.md.

Dipakai agar workstream W2–W4 bisa mengembangkan & menguji skripnya sebelum W1 (01_identitas)
selesai, dan agar verifier punya bahan paritas. BUKAN bagian pipeline produksi.

    from pipeline.lib.uji_arsip import siapkan_db_uji
    siapkan_db_uji(Path('/tmp/uji.db'), tabel=['konsesi', 'hansen_tahunan'])
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import AKAR, buka, hash_geometri, tulis_bangun

ARSIP_K = AKAR / "data/kalimantan.db"
ARSIP_M = AKAR / "data/mapbiomas.db"
ARSIP_K_LENGKAP = AKAR / "data-full/kalimantan.db"


def _arsip(path_k: Path | None) -> Path:
    p = path_k or ARSIP_K
    if not p.exists():  # sesudah git mv ke data/arsip/
        alt = AKAR / "data/arsip" / p.name
        if alt.exists():
            return alt
    return p


SALIN = {
    "konsesi": """
        CREATE TABLE konsesi AS
        SELECT g.kode_wiup, g.nama_usaha, g.sk_iup, g.komoditas, g.jenis_izin, g.kegiatan,
               g.luas_sk AS luas_sk_ha, l.polygon_area_ha AS luas_poligon_ha, g.iup_year AS tahun_izin,
               p.tgl_berlaku, p.tgl_akhir AS tgl_berakhir,
               CASE WHEN p.tgl_berlaku IS NOT NULL THEN 'ippkh_pulih' END AS asal_tanggal,
               g.nama_prov AS provinsi, g.nama_kab AS kabupaten, g.kab_normalized AS kabupaten_norm,
               g.lokasi, g.cnc, g.geometry_geojson AS geometri_geojson,
               g.bbox_min_lon, g.bbox_min_lat, g.bbox_max_lon, g.bbox_max_lat
        FROM a.wiup_geoportal g
        LEFT JOIN a.wiup_loss l ON l.kode_wiup = g.kode_wiup
        LEFT JOIN a.wiup_tanggal_pulih p ON p.kode_wiup = g.kode_wiup""",
    "konsesi_registri": """
        CREATE TABLE konsesi_registri AS
        SELECT m.kode_wiup, CASE WHEN m.db_match='yes' THEN 1 ELSE 0 END AS cocok, m.match_strategy AS strategi_cocok,
               m.id_perizinan, m.id_badan_usaha, b.nama_badan_usaha, b.nib, b.npwp_badan_usaha AS npwp, b.alamat,
               b.kode_pos, b.jenis_badan_usaha, p.tanggal_berlaku, p.tanggal_berakhir, p.tanggal_penetapan,
               p.nama_tahap_kegiatan AS tahap_kegiatan, p.status_cnc, m.minerbaone_url AS url_minerbaone
        FROM a.wiup_match m
        LEFT JOIN a.badan_usaha b ON b.id_badan_usaha = m.id_badan_usaha
        LEFT JOIN a.perizinan p ON p.id_perizinan = m.id_perizinan""",
    "hansen_ringkas": """
        CREATE TABLE hansen_ringkas AS
        SELECT kode_wiup, forest_2000_ha AS hutan_2000_ha, loss_2001_2024_ha AS hilang_2001_2024_ha,
               loss_2001_2024_pct_hutan2000 AS pct_hutan_2000, NULL AS tahun_puncak, tiles AS tile_hansen
        FROM a.wiup_loss""",
    "hansen_tahunan": """
        CREATE TABLE hansen_tahunan AS
        SELECT kode_wiup, year AS tahun, loss_ha AS hilang_ha FROM a.wiup_loss_yearly WHERE year BETWEEN 2001 AND 2024""",
    "izin_laju": """
        CREATE TABLE izin_laju AS
        SELECT kode_wiup, iup_year AS tahun_izin,
               loss_2001_sampai_tahun_izin_ha AS hilang_pra_ha, n_tahun_dari_2001_sampai_tahun_izin AS n_tahun_pra,
               rate_2001_sampai_tahun_izin_ha_per_year AS laju_pra_ha_thn,
               loss_tahun_izin_sampai_2024_ha AS hilang_pasca_ha, n_tahun_dari_tahun_izin_sampai_2024 AS n_tahun_pasca,
               rate_tahun_izin_sampai_2024_ha_per_year AS laju_pasca_ha_thn,
               CASE WHEN ratio_laju_sesudah_vs_sebelum_jendela_2024 = 'inf' THEN NULL
                    ELSE CAST(ratio_laju_sesudah_vs_sebelum_jendela_2024 AS REAL) END AS rasio_pasca_pra,
               COALESCE(REPLACE(verdict_jendela_2024, 'izin_setelah_jendela_2024', 'izin_setelah_jendela'), 'tanpa_tahun_izin') AS vonis
        FROM a.wiup_temporal""",
    "izin_klasifikasi": """
        CREATE TABLE izin_klasifikasi AS
        SELECT k.*, CASE WHEN g.iup_year IS NULL THEN NULL
                         WHEN k.kelas = 'PERPANJANGAN' THEN g.iup_year - 20
                         ELSE g.iup_year END AS tahun_mulai_indikasi
        FROM a.klasifikasi_izin k LEFT JOIN a.wiup_geoportal g USING (kode_wiup)""",
    "ippkh": """
        CREATE TABLE ippkh AS SELECT * FROM a.konsesi_ippkh""",
    "kawasan_hutan": """
        CREATE TABLE kawasan_hutan AS SELECT * FROM a.konsesi_kawasan_hutan""",
    "kepadatan_penduduk": """
        CREATE TABLE kepadatan_penduduk AS
        SELECT kode_kabkot, provinsi, kabupaten, kab_normalized AS kabupaten_norm, tahun, kepadatan, satuan, sumber
        FROM a.kepadatan_penduduk""",
    # dari mapbiomas.db (schema m)
    "mapbiomas_kelas": """
        CREATE TABLE mapbiomas_kelas AS
        SELECT DISTINCT class_code AS kelas, class_name AS nama, kelompok, '' AS hex FROM m.landuse_konsesi ORDER BY 1""",
    "mapbiomas_tahunan": """
        CREATE TABLE mapbiomas_tahunan AS
        SELECT kode_wiup, year AS tahun, class_code AS kelas, pixels AS piksel, ha
        FROM m.landuse_konsesi WHERE year BETWEEN 2001 AND 2024""",
    "transisi_kohort": """CREATE TABLE transisi_kohort AS SELECT * FROM m.transisi_kohort""",
    "transisi_konsesi": """
        CREATE TABLE transisi_konsesi AS
        SELECT kode_wiup, label, tahun_awal, tahun_akhir, kelas_awal, kelas_akhir, pixels AS piksel, ha FROM m.transisi_konsesi""",
    "transisi_pasangan": """
        CREATE TABLE transisi_pasangan AS
        SELECT tahun_awal, tahun_akhir, kelas_awal, kelas_akhir, n_konsesi, pixels AS piksel, ha FROM m.transisi_pasangan""",
    # Arsip TIDAK punya varian aktif (tabel lahir 25 Sep 2026) — dibuat KOSONG hanya supaya
    # skema DB uji lengkap; test yang menguji isinya membangun sendiri lewat 05_transisi.
    "transisi_pasangan_aktif": """
        CREATE TABLE transisi_pasangan_aktif (
          tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
          baru_aktif INTEGER NOT NULL CHECK (baru_aktif IN (0, 1)),
          kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
          n_konsesi INTEGER NOT NULL, piksel INTEGER NOT NULL, ha REAL NOT NULL,
          PRIMARY KEY (tahun_awal, tahun_akhir, baru_aktif, kelas_awal, kelas_akhir),
          CHECK (tahun_awal < tahun_akhir))""",
}
BUTUH_M = {"mapbiomas_kelas", "mapbiomas_tahunan", "transisi_kohort", "transisi_konsesi", "transisi_pasangan"}


def siapkan_db_uji(path: Path, tabel: list[str], *, arsip_k: Path | None = None, arsip_m: Path | None = None,
                   hanya_kode: list[str] | None = None) -> sqlite3.Connection:
    """Buat DB uji di `path` berisi `tabel` (nama skema BARU) hasil salinan arsip.
    `hanya_kode` membatasi ke subset konsesi (test cepat)."""
    if path.exists():
        path.unlink()
    con = buka(path)
    con.execute("ATTACH DATABASE ? AS a", (f"file:{_arsip(arsip_k)}?mode=ro",))
    m = arsip_m or ARSIP_M
    if not m.exists():
        m = AKAR / "data/arsip" / m.name
    if any(t in BUTUH_M for t in tabel):
        con.execute("ATTACH DATABASE ? AS m", (f"file:{m}?mode=ro",))
    for t in tabel:
        if t not in SALIN:
            raise KeyError(f"tabel uji tak dikenal: {t}")
        con.execute(SALIN[t])
        if hanya_kode and "kode_wiup" in {r[1] for r in con.execute(f"PRAGMA table_xinfo('{t}')")}:
            q = ",".join("?" * len(hanya_kode))
            con.execute(f"DELETE FROM {t} WHERE kode_wiup NOT IN ({q})", hanya_kode)
    # CREATE TABLE AS tidak membawa PRIMARY KEY → tambah indeks unik supaya FK REFERENCES di
    # DDL skrip pipeline (SKEMA.md) tidak gagal "foreign key mismatch" (temuan W3).
    if "konsesi" in tabel:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_konsesi_kode ON konsesi(kode_wiup)")
    if "mapbiomas_kelas" in tabel:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_mapbiomas_kelas ON mapbiomas_kelas(kelas)")
    if "transisi_kohort" in tabel:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_transisi_kohort ON transisi_kohort(label)")
    con.commit()
    if "konsesi" in tabel:
        tulis_bangun(con, "konsesi.hash_geometri", hash_geometri(con))
        tulis_bangun(con, "konsesi.n", con.execute("SELECT COUNT(*) FROM konsesi").fetchone()[0])
        con.commit()
    con.execute("DETACH DATABASE a")
    if any(t in BUTUH_M for t in tabel):
        con.execute("DETACH DATABASE m")
    return con
