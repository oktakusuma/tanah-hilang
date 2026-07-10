"""
Enrich batch results dengan metadata dari MinerbaOne DB.

Join strategy: sk_iup (Geoportal) ↔ nomor_izin (DB).
Tambah field: NIB, jenis_badan_usaha, alamat, npwp, kontak, email.

Usage:
    python enrich_with_db.py --input data/analysis/batch_KALIMANTAN_t30_wide.csv
"""

import argparse
import csv
import sqlite3
from pathlib import Path


DB_PATH = Path("minerba-kalimantan.db")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    output = args.output or args.input.with_name(
        args.input.stem.replace("_wide", "_enriched") + ".csv"
    )

    # Load DB into a lookup dict (one-time)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            p.nomor_izin,
            b.id_badan_usaha,
            b.nama_badan_usaha,
            b.nib,
            b.jenis_badan_usaha,
            b.deskripsi_jenis_badan_usaha,
            b.npwp_badan_usaha,
            b.alamat,
            b.kode_pos,
            b.no_telp,
            b.email,
            p.tanggal_berlaku,
            p.tanggal_berakhir,
            p.tanggal_penetapan,
            p.nama_tahap_kegiatan
        FROM perizinan p
        LEFT JOIN badan_usaha b ON b.id_badan_usaha = p.id_badan_usaha
        WHERE p.nomor_izin IS NOT NULL AND p.nomor_izin != ''
    """)
    by_sk = {}
    for row in cur.fetchall():
        by_sk[row["nomor_izin"]] = dict(row)
    print(f"Loaded {len(by_sk)} DB records for matching")

    # Load batch CSV and join
    with args.input.open() as f:
        rows = list(csv.DictReader(f))
        original_fields = list(rows[0].keys())

    enrich_fields = [
        "db_match", "id_badan_usaha", "minerbaone_url",
        "nib", "jenis_badan_usaha", "deskripsi_jenis_badan_usaha",
        "npwp", "alamat", "kode_pos", "no_telp", "email",
        "tanggal_penetapan", "tanggal_berlaku", "tanggal_berakhir",
        "nama_tahap_kegiatan",
    ]
    out_fields = original_fields + enrich_fields

    matched = 0
    enriched_rows = []
    for r in rows:
        sk = r.get("sk_iup", "")
        m = by_sk.get(sk)
        if m:
            matched += 1
            r["db_match"] = "yes"
            idb = m.get("id_badan_usaha") or ""
            r["id_badan_usaha"] = idb
            r["minerbaone_url"] = (
                f"https://minerbaone.esdm.go.id/publik/badan-usaha/detail/{idb}"
                if idb else ""
            )
            r["nib"] = m.get("nib") or ""
            r["jenis_badan_usaha"] = m.get("jenis_badan_usaha") or ""
            r["deskripsi_jenis_badan_usaha"] = (m.get("deskripsi_jenis_badan_usaha") or "")
            r["npwp"] = m.get("npwp_badan_usaha") or ""
            r["alamat"] = m.get("alamat") or ""
            r["kode_pos"] = m.get("kode_pos") or ""
            r["no_telp"] = m.get("no_telp") or ""
            r["email"] = m.get("email") or ""
            r["tanggal_penetapan"] = m.get("tanggal_penetapan") or ""
            r["tanggal_berlaku"] = m.get("tanggal_berlaku") or ""
            r["tanggal_berakhir"] = m.get("tanggal_berakhir") or ""
            r["nama_tahap_kegiatan"] = m.get("nama_tahap_kegiatan") or ""
        else:
            r["db_match"] = "no"
            for f in enrich_fields[1:]:
                r[f] = ""
        enriched_rows.append(r)

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(enriched_rows)

    print(f"\nTotal rows         : {len(rows)}")
    print(f"Matched in DB      : {matched} ({100*matched/len(rows):.1f}%)")
    print(f"Unmatched          : {len(rows) - matched}")
    print(f"Saved → {output}")

    # Sample unmatched (first 5) for inspection
    unmatched = [r for r in rows if r["db_match"] == "no"]
    if unmatched:
        print(f"\nSample 5 unmatched (likely PMDN/national-level permits):")
        for r in unmatched[:5]:
            print(f"  {r['kode_wiup']} | {r['nama_usaha'][:30]:30s} | "
                  f"sk={r['sk_iup']!r}")

    # Breakdown by jenis_badan_usaha for matched
    matched_rows = [r for r in rows if r["db_match"] == "yes"]
    if matched_rows:
        from collections import Counter
        jb = Counter(r["jenis_badan_usaha"] or "(blank)" for r in matched_rows)
        print(f"\nBreakdown by jenis_badan_usaha (matched only):")
        for k, v in jb.most_common(10):
            print(f"  {k:15s} {v:5d}")

    # Top 10 by loss with DB info
    print("\nTop 10 konsesi dengan DB info:")
    rows_sorted = sorted(rows, key=lambda r: -float(r["total_loss_ha"]))
    for i, r in enumerate(rows_sorted[:10], 1):
        m = "✓" if r["db_match"] == "yes" else "?"
        nu = (r["nama_usaha"] or "")[:30]
        loss = float(r["total_loss_ha"])
        jb = (r.get("jenis_badan_usaha") or "?")[:8]
        print(f"  {i:>2}. [{m}] {nu:<30} | loss={loss:>9,.0f}ha | "
              f"jenis={jb:<8} | NIB={r.get('nib') or '-'}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
