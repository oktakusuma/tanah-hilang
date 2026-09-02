"""Buka DB, path akar repo, sidik jari geometri, tabel `bangun`.

Semua skrip pipeline memakai modul ini supaya: (1) tidak ada path hard-coded — semua
input relatif ke AKAR repo; (2) `--db` wajib eksplisit; (3) jejak build tertulis seragam.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

AKAR = Path(__file__).resolve().parents[2]          # …/tanah-hilang
PIPELINE_VERSI = "3.0 (tanah-hilang.db, 2 Sep 2026)"
HIMPUNAN = ("minerba", "lengkap")
WIB = timezone(timedelta(hours=7))


def sekarang() -> str:
    return datetime.now(WIB).isoformat(timespec="seconds")


def gagal(pesan: str, kode: int = 2) -> None:
    """Gagal keras — pipeline TIDAK boleh melewati langkah inti secara diam-diam."""
    print(f"GAGAL: {pesan}", file=sys.stderr)
    raise SystemExit(kode)


def argparser(deskripsi: str, himpunan: bool = True) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=deskripsi)
    ap.add_argument("--db", required=True, type=Path, help="DB target (wajib eksplisit)")
    if himpunan:
        ap.add_argument("--himpunan", choices=HIMPUNAN, required=True,
                        help="minerba = 825 (batubara + logam, dipakai app); lengkap = 1.765 (+ galian C)")
    return ap


def buka(path: Path, baca_saja: bool = False) -> sqlite3.Connection:
    if baca_saja:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(AKAR), "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001 — di luar git tetap boleh jalan
        return "tanpa-git"


def pastikan_bangun(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS bangun (
        kunci TEXT PRIMARY KEY, nilai TEXT NOT NULL, ditulis TEXT NOT NULL)""")


def tulis_bangun(con: sqlite3.Connection, kunci: str, nilai) -> None:
    pastikan_bangun(con)
    con.execute("INSERT OR REPLACE INTO bangun VALUES (?,?,?)", (kunci, str(nilai), sekarang()))


def baca_bangun(con: sqlite3.Connection, kunci: str) -> str | None:
    pastikan_bangun(con)
    r = con.execute("SELECT nilai FROM bangun WHERE kunci=?", (kunci,)).fetchone()
    return r[0] if r else None


def hash_geometri(con: sqlite3.Connection) -> str:
    """sha256 atas 'kode_wiup\\ngeometri_geojson' diurutkan kode_wiup — sidik jari himpunan konsesi.
    Dipakai untuk memastikan tabel MapBiomas dibangun atas geometri yang sama."""
    h = hashlib.sha256()
    for kode, geo in con.execute("SELECT kode_wiup, geometri_geojson FROM konsesi ORDER BY kode_wiup"):
        h.update(kode.encode()); h.update(b"\n"); h.update((geo or "").encode()); h.update(b"\n")
    return h.hexdigest()


def tandai_selesai(con: sqlite3.Connection, skrip: str, **info) -> None:
    tulis_bangun(con, f"{skrip}.selesai", sekarang())
    tulis_bangun(con, "pipeline_versi", PIPELINE_VERSI)
    tulis_bangun(con, "git_commit", git_commit())
    for k, v in info.items():
        tulis_bangun(con, f"{skrip}.{k}", v)
    con.commit()


def wajib_ada(*paths: Path, keterangan: str = "") -> None:
    kurang = [str(p) for p in paths if not Path(p).exists()]
    if kurang:
        gagal(f"prasyarat absen{(' (' + keterangan + ')') if keterangan else ''}: " + ", ".join(kurang))


def wajib_tabel(con: sqlite3.Connection, *tabel: str) -> None:
    ada = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    kurang = [t for t in tabel if t not in ada]
    if kurang:
        gagal("tabel hulu belum ada di DB target: " + ", ".join(kurang) + " — jalankan langkah sebelumnya")
