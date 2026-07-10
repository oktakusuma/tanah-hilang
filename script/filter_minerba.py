#!/usr/bin/env python3
"""Turn a FULL kalimantan.db (all WIUP: minerba + galian C) into the DEFAULT
"minerba" version — mineral logam + batubara only.

The WIUP_Publish layer includes construction-material concessions (batuan /
galian C: pasir, andesit, batu, tanah) and non-metallic minerals (pasir kuarsa,
kaolin, …). For a deforestation thesis focused on serious extractive mining we
keep only coal + metallic minerals, matching the historical 824-concession
baseline (Join_WIUP_vs_IPPKH).

Filters every per-WIUP table by commodity; the shared company registry
(badan_usaha, perizinan, kepadatan_penduduk) is left intact — a company still
exists even if we don't surface its galian-C concessions.

Usage:
  python filter_minerba.py --input data-full/kalimantan.db --output data/kalimantan.db --force
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

# Kelompok 1 (Batubara) + Kelompok 2 (Mineral logam). Uppercase to match the
# normalized `komoditas` column build_combined_db writes.
MINERBA_COMMODITIES = {
    "BATUBARA",
    "BAUKSIT", "BAUKSIT DMP",
    "EMAS", "EMAS DMP",
    "BIJIH BESI", "BIJIH BESI DMP", "BESI",
    "ZIRKON", "TIMAH", "MANGAN", "ANTIMONI", "INTAN ALLUVIAL",
}

# Per-WIUP tables keyed by kode_wiup that must be filtered to the kept set.
CHILD_TABLES = ["wiup_loss", "wiup_loss_yearly", "wiup_temporal", "wiup_match"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=Path("data-full/kalimantan.db"),
                    help="FULL DB (all commodities) — source of truth")
    ap.add_argument("--output", type=Path, default=Path("data/kalimantan.db"),
                    help="DEFAULT minerba DB to write (app reads this)")
    ap.add_argument("--force", action="store_true", help="overwrite output if it exists")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"ERROR: input {args.input} tidak ada", file=sys.stderr)
        return 1
    if args.output.exists() and not args.force:
        print(f"ERROR: {args.output} sudah ada. Pakai --force.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.input, args.output)

    conn = sqlite3.connect(args.output)
    placeholders = ",".join("?" * len(MINERBA_COMMODITIES))
    keep = tuple(MINERBA_COMMODITIES)

    before = conn.execute("SELECT COUNT(*) FROM wiup_geoportal").fetchone()[0]
    # 1) drop non-minerba concessions
    conn.execute(
        f"DELETE FROM wiup_geoportal WHERE UPPER(TRIM(komoditas)) NOT IN ({placeholders})",
        keep)
    # 2) cascade: drop child rows whose WIUP no longer exists
    for tbl in CHILD_TABLES:
        conn.execute(
            f"DELETE FROM {tbl} WHERE kode_wiup NOT IN (SELECT kode_wiup FROM wiup_geoportal)")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM wiup_geoportal").fetchone()[0]

    # Report commodity breakdown of what remains
    print(f"Filtered {args.input} → {args.output}")
    print(f"  WIUP: {before} → {after}  (dibuang {before - after} non-minerba)")
    rows = conn.execute(
        "SELECT komoditas, COUNT(*) FROM wiup_geoportal GROUP BY komoditas ORDER BY 2 DESC"
    ).fetchall()
    for komo, n in rows:
        print(f"    {n:5}  {komo}")

    # Mode DELETE (rollback), bukan WAL → server bisa membacanya read-only tanpa
    # butuh direktori writable (hindari SQLITE_READONLY_DIRECTORY 1544 di container).
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
