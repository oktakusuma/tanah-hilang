"""SATU pemilik skema `sumber`, `analysis_meta`, `column_meta` (lihat SKEMA.md §0).

Tiap skrip pipeline mendaftarkan tabel & kolom MILIKNYA lewat `tulis_meta(...)`.
Verifier (10_verifikasi.py) menuntut cakupan 100% dua arah: setiap tabel/view non-infrastruktur
punya baris analysis_meta, dan setiap kolomnya punya baris column_meta — tanpa baris yatim.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

INFRA = {"sumber", "bangun", "analysis_meta", "column_meta"}

LISENSI = {
    "hansen": "CC BY 4.0 (Hansen/UMD/Google/USGS/NASA GFC 2025 v1.13)",
    "mapbiomas": "CC BY-SA (versi tak disebut penerbit; MapBiomas Indonesia Koleksi 4.1) — tabel turunan = Adapted Material",
    "geoportal": "Data publik Geoportal ESDM (WIUP_Publish; IPPKH & kawasan hutan)",
    "minerbaone": "Data publik MinerbaOne Kementerian ESDM",
    "bps": "Data publik BPS",
    "turunan": "Turunan analisis tesis (mengikuti lisensi sumber terketat yang dipakai)",
}

DDL = """
CREATE TABLE IF NOT EXISTS sumber (
  id TEXT PRIMARY KEY, nama TEXT NOT NULL, versi TEXT, lisensi TEXT NOT NULL, url TEXT,
  tanggal_akses TEXT, cakupan_tahun TEXT, sitasi TEXT, catatan TEXT);
CREATE TABLE IF NOT EXISTS analysis_meta (
  nama_tabel TEXT PRIMARY KEY, deskripsi TEXT NOT NULL, sumber TEXT NOT NULL,
  metode TEXT NOT NULL, skrip TEXT NOT NULL, lisensi TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'INTI');
CREATE TABLE IF NOT EXISTS column_meta (
  nama_tabel TEXT NOT NULL, nama_kolom TEXT NOT NULL, deskripsi TEXT NOT NULL,
  rumus TEXT, sumber TEXT, PRIMARY KEY (nama_tabel, nama_kolom));
"""


def pastikan_meta(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def tulis_sumber(con: sqlite3.Connection, id: str, nama: str, lisensi: str, *, versi: str | None = None,
                 url: str | None = None, tanggal_akses: str | None = None, cakupan_tahun: str | None = None,
                 sitasi: str | None = None, catatan: str | None = None) -> None:
    pastikan_meta(con)
    con.execute("INSERT OR REPLACE INTO sumber VALUES (?,?,?,?,?,?,?,?,?)",
                (id, nama, versi, lisensi, url, tanggal_akses, cakupan_tahun, sitasi, catatan))


def tulis_meta(con: sqlite3.Connection, nama_tabel: str, *, deskripsi: str, sumber: str, metode: str,
               skrip: str, lisensi: str, kolom: Iterable[tuple], status: str = "INTI") -> None:
    """Daftarkan satu tabel/view + SEMUA kolomnya. `kolom` = iterable (nama, deskripsi[, rumus[, sumber]]).

    Menghapus baris lama milik tabel yang sama lalu menulis ulang — idempoten, tidak menyentuh
    tabel lain. Kolom yang ada di DB tapi tak didaftarkan → verifier FAIL, jadi daftar lengkap."""
    pastikan_meta(con)
    con.execute("DELETE FROM analysis_meta WHERE nama_tabel=?", (nama_tabel,))
    con.execute("DELETE FROM column_meta WHERE nama_tabel=?", (nama_tabel,))
    con.execute("INSERT INTO analysis_meta VALUES (?,?,?,?,?,?,?)",
                (nama_tabel, deskripsi, sumber, metode, skrip, lisensi, status))
    for k in kolom:
        nama, desk = k[0], k[1]
        rumus = k[2] if len(k) > 2 else None
        smb = k[3] if len(k) > 3 else None
        con.execute("INSERT INTO column_meta VALUES (?,?,?,?,?)", (nama_tabel, nama, desk, rumus, smb))
    # Cek langsung: kolom nyata vs terdaftar (supaya kesalahan ketahuan di skrip pemilik, bukan di verifier)
    nyata = [r[1] for r in con.execute(f"PRAGMA table_xinfo('{nama_tabel}')")]
    terdaftar = {k[0] for k in kolom}
    kurang = [c for c in nyata if c not in terdaftar]
    lebih = [c for c in terdaftar if c not in nyata]
    if kurang or lebih:
        raise SystemExit(f"GAGAL meta {nama_tabel}: kolom tanpa meta {kurang}; meta tanpa kolom {lebih}")


def cakupan_dua_arah(con: sqlite3.Connection) -> list[str]:
    """Kembalikan daftar masalah (kosong = PASS). Dipakai verifier."""
    masalah = []
    objek = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")} - INFRA
    ada_meta = {r[0] for r in con.execute("SELECT nama_tabel FROM analysis_meta")}
    for t in sorted(objek - ada_meta):
        masalah.append(f"{t}: tanpa analysis_meta")
    for t in sorted(ada_meta - objek):
        masalah.append(f"{t}: analysis_meta yatim (objek tak ada)")
    for t in sorted(objek & ada_meta):
        nyata = {r[1] for r in con.execute(f"PRAGMA table_xinfo('{t}')")}
        meta = {r[0] for r in con.execute("SELECT nama_kolom FROM column_meta WHERE nama_tabel=?", (t,))}
        if nyata - meta:
            masalah.append(f"{t}: kolom tanpa meta {sorted(nyata - meta)}")
        if meta - nyata:
            masalah.append(f"{t}: meta tanpa kolom {sorted(meta - nyata)}")
    return masalah
