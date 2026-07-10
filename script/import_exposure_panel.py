#!/usr/bin/env python3
"""Impor cross-section paparan sentralisasi dari panel tesis ke SQLite.

Sumber kebenaran = `stata/Data all v0.7.dta` (panel 56 kab × 2015–2024 yang
dipakai di tesis). `exp_sentralisasi` bersifat time-invariant per kabupaten
(intensitas keterpaparan baseline terhadap sentralisasi izin 2020), jadi kita
ambil satu baris per kabupaten dan tulis ke tabel `exposure_kabupaten`.

Tujuan: angka di web = angka di tesis, 1:1 (mis. 22 kabupaten "kontrol murni"
dengan exp = 0). `kab_normalized` diambil dari tabel `kepadatan_penduduk` lewat
join `kode_kabkot` supaya konsisten dengan tabel lain (peta, dsb).

Butuh: pandas (baca .dta). Jalankan setelah DB utama terbentuk.

    python3 scripts/import_exposure_panel.py \
        --dta "stata/Data all v0.7.dta" --db data/kalimantan.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import pandas as pd


def kab_normalized_lookup(con: sqlite3.Connection) -> dict[int, str]:
    """kode_kabkot -> kab_normalized dari tabel kepadatan_penduduk (kanonik)."""
    try:
        rows = con.execute(
            "SELECT kode_kabkot, kab_normalized FROM kepadatan_penduduk"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[int, str] = {}
    for kode, norm in rows:
        try:
            out[int(kode)] = norm
        except (TypeError, ValueError):
            continue
    return out


def fallback_norm(kabkot: str) -> str:
    """Normalisasi cadangan bila kode tak ketemu di kepadatan_penduduk."""
    s = (kabkot or "").upper().strip()
    if s.startswith("KOTA "):
        s = s[5:]
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dta", default="stata/Data all v0.7.dta")
    ap.add_argument("--db", default="data/kalimantan.db")
    args = ap.parse_args()

    df = pd.read_stata(args.dta)
    needed = {"Kode_Kabkot", "Provinsi", "KabKot", "Tahun",
              "exp_sentralisasi", "exp_coal", "exp_z"}
    missing = needed - set(df.columns)
    if missing:
        print(f"ERROR: kolom hilang di .dta: {sorted(missing)}", file=sys.stderr)
        return 1

    # Cross-section: exp time-invariant → satu baris per kabupaten.
    # Sanity: pastikan exp memang konstan antar tahun sebelum ambil satu tahun.
    spread = df.groupby("Kode_Kabkot")["exp_sentralisasi"].std(ddof=0).max()
    if spread and spread > 1e-9:
        print(f"WARNING: exp_sentralisasi tidak konstan antar tahun (std={spread}); "
              "mengambil tahun paling awal.", file=sys.stderr)
    year0 = int(df["Tahun"].min())
    cs = df[df["Tahun"] == year0].copy()

    con = sqlite3.connect(args.db)
    lookup = kab_normalized_lookup(con)

    con.execute("DROP TABLE IF EXISTS exposure_kabupaten")
    con.execute(
        """
        CREATE TABLE exposure_kabupaten (
            kode_kabkot       INTEGER PRIMARY KEY,
            kabupaten         TEXT,
            kab_normalized    TEXT,
            provinsi          TEXT,
            exp_sentralisasi  REAL,   -- luas izin DAERAH pre-2020 / luas kab (semua komoditas)
            exp_coal          REAL,   -- idem, batubara saja
            exp_z             REAL,   -- z-score exp_sentralisasi
            is_control        INTEGER -- 1 jika exp_sentralisasi = 0 (kontrol murni)
        )
        """
    )

    inserted = 0
    for _, r in cs.iterrows():
        kode = int(r["Kode_Kabkot"])
        exp = float(r["exp_sentralisasi"])
        con.execute(
            "INSERT INTO exposure_kabupaten "
            "(kode_kabkot, kabupaten, kab_normalized, provinsi, "
            " exp_sentralisasi, exp_coal, exp_z, is_control) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                kode,
                str(r["KabKot"]),
                lookup.get(kode) or fallback_norm(str(r["KabKot"])),
                str(r["Provinsi"]),
                exp,
                float(r["exp_coal"]),
                float(r["exp_z"]),
                1 if exp == 0 else 0,
            ),
        )
        inserted += 1

    con.execute("CREATE INDEX idx_exp_norm ON exposure_kabupaten(kab_normalized)")
    con.commit()

    n_ctrl = con.execute(
        "SELECT COUNT(*) FROM exposure_kabupaten WHERE is_control=1"
    ).fetchone()[0]
    top = con.execute(
        "SELECT kabupaten, exp_sentralisasi FROM exposure_kabupaten "
        "ORDER BY exp_sentralisasi DESC LIMIT 1"
    ).fetchone()

    # DB dilayani read-only tanpa direktori writable → wajib mode DELETE (bukan WAL).
    con.execute("PRAGMA journal_mode=DELETE")
    con.commit()
    con.close()

    print(f"OK: {inserted} kabupaten → exposure_kabupaten "
          f"(kontrol murni exp=0: {n_ctrl}; tertinggi: {top[0]} {top[1]:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
