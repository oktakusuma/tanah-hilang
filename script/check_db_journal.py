#!/usr/bin/env python3
"""Pastikan kalimantan.db berada di mode DELETE (rollback), bukan WAL.

KENAPA ADA: DB di repo ini IKUT di-commit. Server melayaninya READ-ONLY sebagai
user non-root (uid 10001) dengan direktori data/ tak writable. DB bermode WAL
menuntut SQLite MEMBUAT sidecar -wal/-shm sebelum bisa dibaca → gagal →
SQLITE_READONLY_DIRECTORY (1544) → container restart-loop. (Lihat commit ea0fa63.)

Mode journal itu tersimpan DI DALAM file DB (byte 18 header), bukan setelan
koneksi. Jadi kalau DB dibuka pakai GUI SQLite (DB Browser/TablePlus/DBeaver)
yang lazim menyetel WAL, file-nya berubah, ikut ter-commit, ter-push, lalu
menjatuhkan server saat `git pull`. Guard ini mencegahnya lolos sejak awal.

Pipeline sudah menulis mode DELETE (filter_minerba.py, build_periode_tables.py);
skrip ini VERIFIKASI terakhir — murah, dan menangkap flip yang datang dari luar
pipeline.

Pakai:
    python3 scripts/check_db_journal.py data/kalimantan.db data-full/kalimantan.db
    python3 scripts/check_db_journal.py data/kalimantan.db --fix   # perbaiki, jangan cuma lapor

Keluar 1 bila masih ada masalah (menghentikan pipeline lewat `set -e`).
Untuk host deployment lihat scripts/prep_deploy_db.sh (yang juga menyetel permission).
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

# Sidecar yang tak boleh ikut ter-commit / tertinggal di host.
_SIDECARS = ("-wal", "-shm", "-journal")


def journal_mode(path) -> str:
    """Baca mode journal dari HEADER file (byte 18) — tanpa membuka koneksi.

    Membuka koneksi untuk mengecek justru bisa MENGUBAH file (SQLite membuat
    sidecar), jadi pengecekan sengaja dilakukan di level byte.
    byte 18: 1 = rollback/DELETE, 2 = WAL.
    """
    with open(path, "rb") as fh:
        header = fh.read(19)
    if len(header) < 19:
        return "DELETE"  # file kosong/kecil — belum ada header WAL
    return "WAL" if header[18] == 2 else "DELETE"


def _sidecars(path: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in (pathlib.Path(str(path) + s) for s in _SIDECARS) if p.exists()]


def check(paths) -> list[str]:
    """Laporkan masalah tanpa mengubah apa pun. File yang tak ada → dilewati."""
    problems: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if not path.exists():
            continue  # data-full/ boleh absen (mis. pipeline parsial)
        if journal_mode(path) == "WAL":
            problems.append(
                f"{path}: mode WAL — server read-only non-root akan gagal (1544)"
            )
        for side in _sidecars(path):
            problems.append(f"{path}: sidecar tertinggal ({side.name})")
    return problems


def fix(path) -> None:
    """Konversi ke DELETE lalu buang sidecar.

    Urutannya penting: checkpoint DULU (memindahkan isi -wal ke DB utama),
    baru hapus sidecar — kebalikannya akan membuang transaksi yang belum
    ter-checkpoint.
    """
    path = pathlib.Path(path)
    if journal_mode(path) == "WAL":
        con = sqlite3.connect(path)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("PRAGMA journal_mode=DELETE")
            con.commit()
        finally:
            con.close()
    for side in _sidecars(path):
        side.unlink()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    do_fix = "--fix" in argv
    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        print("pakai: check_db_journal.py <db…> [--fix]", file=sys.stderr)
        return 2

    if do_fix:
        # Catat kondisi SEBELUM diperbaiki: perbaikan senyap menyembunyikan
        # fakta penting — ada yang membalik DB ke WAL di luar pipeline.
        before = check(paths)
        for p in paths:
            if pathlib.Path(p).exists():
                fix(p)
        for problem in before:
            print(f"[journal] DIPERBAIKI {problem}", file=sys.stderr)
        if before:
            print("[journal] ^ DB dibalik ke WAL di luar pipeline (biasanya GUI SQLite). "
                  "Sudah dikembalikan ke DELETE — commit ulang file DB-nya.", file=sys.stderr)

    problems = check(paths)
    for p in problems:
        print(f"[journal] MASALAH {p}", file=sys.stderr)
    if problems:
        print(f"[journal] Perbaiki: python3 scripts/check_db_journal.py {' '.join(paths)} --fix",
              file=sys.stderr)
        return 1

    for p in paths:
        if pathlib.Path(p).exists():
            print(f"[journal] OK {p} — mode DELETE, tanpa sidecar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
