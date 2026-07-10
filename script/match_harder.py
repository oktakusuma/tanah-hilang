"""
Try harder matching strategies untuk unmatched WIUPs (sisa 205 dari 824).

Strategy progression:
  T0 (already done): EXACT sk_iup ↔ nomor_izin                  → 619 match
  T1: Normalized exact (strip whitespace, uppercase, etc)
  T2: Fuzzy nama_usaha matching (substring + Jaro-Winkler-lite)
  T3: Loose nomor_izin matching (digit-only comparison)
  T4: Match via lokasi + komoditas + luas similarity

For each strategy applied, mark in DB with match_strategy = 'exact' | 't1' | 't2' | ...
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from difflib import SequenceMatcher


def normalize_sk(sk: str | None) -> str:
    """Lowercase, strip whitespace + punctuation that doesn't matter."""
    if not sk:
        return ""
    s = sk.upper().strip()
    s = re.sub(r"\s+", " ", s)          # collapse whitespace
    s = re.sub(r"[‐–—-]", "-", s)       # normalize various dashes
    s = re.sub(r"[ \t]*([/\.,])[ \t]*", r"\1", s)  # trim around delimiters
    return s


def digits_only(sk: str | None) -> str:
    return re.sub(r"\D", "", sk or "")


def normalize_name(name: str | None) -> str:
    """Normalize company name for fuzzy matching."""
    if not name:
        return ""
    n = name.upper().strip()
    # Strip common corporate suffixes
    for sfx in [", PT", " PT", " TBK", " (TBK)", " CV", ", CV",
                " PERSERO", " (PERSERO)", " INDONESIA"]:
        n = n.replace(sfx, "")
    n = re.sub(r"\s+", " ", n).strip()
    return n


def similarity(a, b):
    """Quick string similarity 0..1 (SequenceMatcher)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def try_match_t1_normalized(geo, db_lookup):
    """T1: normalize SK and try exact match again."""
    norm_sk = normalize_sk(geo["sk_iup"])
    if not norm_sk:
        return None
    return db_lookup.get(norm_sk)


def try_match_t3_digits(geo, db_digit_lookup):
    """T3: match by digit-only sk_iup."""
    digits = digits_only(geo["sk_iup"])
    if len(digits) < 8:
        return None
    return db_digit_lookup.get(digits)


def try_match_t2_fuzzy_name(geo, name_lookup, min_sim=0.88):
    """T2: fuzzy company name match. Returns best match if above threshold."""
    target = normalize_name(geo["nama_usaha"])
    if not target or len(target) < 4:
        return None
    best, best_score = None, 0
    for name, ids in name_lookup.items():
        s = similarity(target, name)
        if s > best_score:
            best_score = s
            best = (name, ids, s)
    if best and best_score >= min_sim:
        return best
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/kalimantan.db"))
    parser.add_argument("--apply", action="store_true",
                        help="Apply matches to DB (otherwise dry-run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Build lookup tables from DB perizinan
    db_by_norm_sk = {}      # normalized SK → (id_perizinan, id_badan_usaha, original_sk)
    db_by_digits = {}       # digit-only SK → ...
    db_by_name = {}         # normalized name → list of (id_pz, id_bu, original_name)
    for r in cur.execute("""
        SELECT p.id_perizinan, p.id_badan_usaha, p.nomor_izin, b.nama_badan_usaha
        FROM perizinan p
        LEFT JOIN badan_usaha b ON b.id_badan_usaha = p.id_badan_usaha
        WHERE p.nomor_izin IS NOT NULL AND p.nomor_izin != ''
    """):
        norm = normalize_sk(r["nomor_izin"])
        db_by_norm_sk.setdefault(norm, (r["id_perizinan"], r["id_badan_usaha"],
                                         r["nomor_izin"]))
        digits = digits_only(r["nomor_izin"])
        if len(digits) >= 8:
            db_by_digits.setdefault(digits, (r["id_perizinan"], r["id_badan_usaha"],
                                              r["nomor_izin"]))
        if r["nama_badan_usaha"]:
            nname = normalize_name(r["nama_badan_usaha"])
            db_by_name.setdefault(nname, []).append(
                (r["id_perizinan"], r["id_badan_usaha"], r["nama_badan_usaha"])
            )

    print(f"DB lookups built:", file=sys.stderr)
    print(f"  By normalized SK: {len(db_by_norm_sk)} unique keys", file=sys.stderr)
    print(f"  By digits only  : {len(db_by_digits)} unique keys", file=sys.stderr)
    print(f"  By normalized name: {len(db_by_name)} unique keys", file=sys.stderr)

    # Get current unmatched WIUPs
    unmatched = list(cur.execute("""
        SELECT g.kode_wiup, g.nama_usaha, g.sk_iup, g.komoditas, g.nama_kab,
               g.luas_sk
        FROM wiup_geoportal g
        JOIN wiup_match m ON m.kode_wiup = g.kode_wiup
        WHERE m.db_match = 'no'
        ORDER BY g.kode_wiup
    """))
    print(f"\nUnmatched WIUPs to try: {len(unmatched)}", file=sys.stderr)

    new_matches = []  # (kode_wiup, id_pz, id_bu, strategy, evidence)
    counts = {"T1_norm_sk": 0, "T3_digits": 0, "T2_name": 0}

    for r in unmatched:
        geo = dict(r)

        # T1: normalized SK
        hit = try_match_t1_normalized(geo, db_by_norm_sk)
        if hit:
            new_matches.append((geo["kode_wiup"], hit[0], hit[1], "T1_norm_sk",
                                f"sk='{geo['sk_iup']}' → norm='{hit[2]}'"))
            counts["T1_norm_sk"] += 1
            continue

        # T3: digits-only SK (less reliable, only if no T1)
        hit = try_match_t3_digits(geo, db_by_digits)
        if hit:
            new_matches.append((geo["kode_wiup"], hit[0], hit[1], "T3_digits",
                                f"digits={digits_only(geo['sk_iup'])} → '{hit[2]}'"))
            counts["T3_digits"] += 1
            continue

        # T2: fuzzy name (last resort, lots of false positive risk)
        hit = try_match_t2_fuzzy_name(geo, db_by_name)
        if hit:
            db_name, ids_list, score = hit
            # Pick first matching id
            id_pz, id_bu, orig_name = ids_list[0]
            new_matches.append((geo["kode_wiup"], id_pz, id_bu, "T2_fuzzy_name",
                                f"'{geo['nama_usaha']}' ~ '{orig_name}' "
                                f"(sim={score:.2f})"))
            counts["T2_name"] += 1

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  ADDITIONAL MATCHES FOUND", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    for strat, n in counts.items():
        print(f"  {strat:<20} {n:>4} matches", file=sys.stderr)
    total_extra = sum(counts.values())
    print(f"  {'TOTAL':<20} {total_extra:>4}", file=sys.stderr)
    print(f"\n  Combined match rate: {(619 + total_extra)/824*100:.1f}% "
          f"({619 + total_extra}/824)", file=sys.stderr)

    # Show sample of each strategy
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  SAMPLE NEW MATCHES (showing 5 per strategy)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    for strat in ["T1_norm_sk", "T3_digits", "T2_fuzzy_name"]:
        samples = [m for m in new_matches if m[3] == strat][:5]
        if not samples:
            continue
        print(f"\n  [{strat}]", file=sys.stderr)
        for kw, id_pz, id_bu, _, evidence in samples:
            geo_row = next(r for r in unmatched if r["kode_wiup"] == kw)
            print(f"    WIUP {kw} | {geo_row['nama_usaha'][:30]:30s}", file=sys.stderr)
            print(f"      ↳ {evidence[:120]}", file=sys.stderr)

    # Apply to DB?
    if args.apply and new_matches:
        print(f"\n  Applying {len(new_matches)} matches to DB...", file=sys.stderr)
        cur.execute("""
            ALTER TABLE wiup_match ADD COLUMN match_strategy TEXT
        """) if "match_strategy" not in [c[1] for c in cur.execute(
            "PRAGMA table_info(wiup_match)").fetchall()] else None
        # Re-check schema
        cols = [c[1] for c in cur.execute("PRAGMA table_info(wiup_match)")]
        has_strat = "match_strategy" in cols
        if not has_strat:
            cur.execute("ALTER TABLE wiup_match ADD COLUMN match_strategy TEXT")
        # Update the exact matches first (mark as exact)
        cur.execute("UPDATE wiup_match SET match_strategy = 'T0_exact' WHERE db_match = 'yes'")
        for kw, id_pz, id_bu, strat, _ in new_matches:
            url = (f"https://minerbaone.esdm.go.id/publik/badan-usaha/detail/"
                   f"{id_bu}") if id_bu else None
            cur.execute("""
                UPDATE wiup_match
                SET db_match = 'yes', id_perizinan = ?, id_badan_usaha = ?,
                    minerbaone_url = ?, match_strategy = ?
                WHERE kode_wiup = ?
            """, (id_pz, id_bu, url, strat, kw))
        conn.commit()
        # Verify
        verify = cur.execute("""
            SELECT match_strategy, COUNT(*) FROM wiup_match
            WHERE db_match = 'yes' GROUP BY match_strategy
        """).fetchall()
        print(f"\n  Final match breakdown:", file=sys.stderr)
        for s, c in verify:
            print(f"    {s or '(legacy)':<20} {c:>4}", file=sys.stderr)
        print(f"  ✅ DB updated", file=sys.stderr)
    elif new_matches:
        print(f"\n  (dry run — use --apply to write to DB)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
