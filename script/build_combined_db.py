"""
Build combined master SQLite database for thesis analysis.

Sources merged:
  - minerba-kalimantan.db          → badan_usaha + perizinan (copied)
  - data/wiup/kalimantan_unique.geojson → wiup_geoportal (polygons + attrs)
  - data/analysis/batch_KALIMANTAN_t30_wide.csv → wiup_loss + wiup_loss_yearly
  - data/analysis/temporal_iup_analysis.csv    → wiup_temporal
  - data/analysis/batch_KALIMANTAN_t30_enriched.csv → wiup_match
  - Hansen v1.13 metadata + tile assignment

Output: kalimantan.db (single relational master DB)

Schema:
  - badan_usaha (1,042)             [from MinerbaOne scrape]
  - perizinan (1,104)               [from MinerbaOne scrape]
  - wiup_geoportal (824)            [Geoportal polygons + attributes]
  - wiup_loss (824)                 [Hansen loss summary per WIUP]
  - wiup_loss_yearly (20,600)       [Long format: per WIUP × year]
  - wiup_temporal (824)             [Pre/post-IUP rates + verdict]
  - wiup_match (824)                [Link Geoportal ↔ MinerbaOne via sk_iup]
  - wiup_master (VIEW)              [Flattened join, query-ready]

Usage:
    python build_combined_db.py                                   # --phase full (perilaku lama utuh)
    python build_combined_db.py --output kalimantan.db --force

Pipeline 4-bagian (F24a) — dua fase terpisah:
    # BAGIAN 1: registry izin SAJA (tanpa CSV pengukuran) — geoportal + minerba
    # + kepadatan + match-SK-persis LANGSUNG geoportal×perizinan + cangkang
    # kosong wiup_loss/wiup_loss_yearly/wiup_temporal + indeks + view.
    python build_combined_db.py --phase registry --output data-full/kalimantan.db --force

    # BAGIAN 2: tempel pengukuran (step_loss + step_temporal) ke DB target yang
    # SUDAH ADA; baris CSV dibatasi ke kode_wiup yang ada di wiup_geoportal
    # target (setara cascade-delete filter_minerba di jalur lama).
    python build_combined_db.py --phase pengukuran --db data/kalimantan.db
"""

import argparse
import csv
import json
import math
import sqlite3
import sys
import time
from pathlib import Path


def norm_upper(v):
    """Normalisasi teks kategorikal: strip + UPPER, None bila kosong.

    Cegah casing campuran memecah agregasi GROUP BY (mis. 'Menteri' vs 'MENTERI',
    'Batubara' vs 'BATUBARA') — WIUP_Publish mencampur casing.
    """
    s = (v or "").strip().upper()
    return s or None


# ---- Helpers ----

def pick_tile(min_lat, max_lat, min_lon, max_lon):
    """Hansen tile names covering bbox."""
    tiles = set()
    lat = math.floor(min_lat / 10) * 10
    while lat <= math.floor(max_lat / 10) * 10:
        lon = math.floor(min_lon / 10) * 10
        while lon <= math.floor(max_lon / 10) * 10:
            top = lat + 10
            ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
            ew = f"{abs(lon):03d}{'E' if lon >= 0 else 'W'}"
            tiles.add(f"{ns}_{ew}")
            lon += 10
        lat += 10
    return sorted(tiles)


def feature_bbox(geom):
    """Return (minx, miny, maxx, maxy) from a GeoJSON geometry."""
    coords = geom["coordinates"]
    if geom["type"] == "Polygon":
        xs = [pt[0] for r in coords for pt in r]
        ys = [pt[1] for r in coords for pt in r]
    else:  # MultiPolygon
        xs = [pt[0] for poly in coords for r in poly for pt in r]
        ys = [pt[1] for poly in coords for r in poly for pt in r]
    return min(xs), min(ys), max(xs), max(ys)


def to_num(v, default=None):
    """Cast to number, return default if empty/None."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def to_int(v, default=None):
    n = to_num(v, None)
    return int(n) if n is not None else default


# ---- Lapisan (cangkang) — dibuat di build_combined_db, DIISI oleh skrip lain
# (attribution_sawit.py / klasifikasi_izin — Task 4) sehingga urutan langkah
# pipeline tak menentukan: view wiup_master selalu valid walau lapisan belum
# diisi (LEFT JOIN ke tabel kosong -> NULL, bukan error). `IF NOT EXISTS`
# supaya rerun tak menimpa data yang sudah ditulis skrip lapisan.
LAPISAN_SHELLS = """
CREATE TABLE IF NOT EXISTS atribusi_sawit (
  kode_wiup                      TEXT PRIMARY KEY REFERENCES wiup_geoportal(kode_wiup),
  loss_2001_2021_ha              REAL,
  loss_sawit_tol2th_ha           REAL,
  loss_sawit_jeda5th_ha          REAL,
  loss_sawit_tahunsama_ha        REAL,
  loss_2022_2025_ha              REAL,
  n_tile_hansen                  INTEGER,
  loss_sawit_pra_izin_ha         REAL,
  loss_sawit_pasca_izin_2021_ha  REAL,
  loss_pasca_izin_2021_ha        REAL
);

CREATE TABLE IF NOT EXISTS klasifikasi_izin (
  kode_wiup              TEXT PRIMARY KEY,
  kelas                  TEXT NOT NULL,
  bukti                  TEXT,
  dasar                  TEXT NOT NULL,
  durasi_sk              INTEGER,
  masa_berlaku_diwarisi  INTEGER NOT NULL,
  pra_izin_dominan       INTEGER
);
"""


# ---- Build steps ----

def step_copy_minerba(conn, src_path):
    """Copy badan_usaha + perizinan from minerba-kalimantan.db."""
    print(f"\n[1/7] Copying tables from {src_path}", file=sys.stderr)
    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Drop & recreate to ensure clean state
    cur.execute("DROP TABLE IF EXISTS badan_usaha")
    cur.execute("DROP TABLE IF EXISTS perizinan")

    cur.execute("""
        CREATE TABLE badan_usaha (
            id_badan_usaha TEXT PRIMARY KEY,
            id_jenis_badan_usaha TEXT,
            nib TEXT,
            nama_badan_usaha TEXT,
            kode_badan_usaha TEXT,
            no_telp TEXT,
            email TEXT,
            fax TEXT,
            npwp_badan_usaha TEXT,
            rt TEXT,
            rw TEXT,
            alamat TEXT,
            kode_pos TEXT,
            kode_desa TEXT,
            jenis_badan_usaha TEXT,
            deskripsi_jenis_badan_usaha TEXT,
            minerbaone_url TEXT,
            created_at TIMESTAMP        )
    """)
    cur.execute("""
        CREATE TABLE perizinan (
            id_perizinan TEXT PRIMARY KEY,
            id_badan_usaha TEXT,
            id_komoditas TEXT,
            id_golongan TEXT,
            id_jenis_perizinan TEXT,
            id_tahap_kegiatan TEXT,
            id_wiup TEXT,
            id_status_cnc TEXT,
            nomor_izin TEXT,
            luas_ha TEXT,
            tanggal_penetapan TEXT,
            tanggal_berlaku TEXT,
            tanggal_berakhir TEXT,
            lokasi_perizinan TEXT,
            nama_komoditas TEXT,
            nama_golongan TEXT,
            nama_tahap_kegiatan TEXT,
            jenis_perizinan TEXT,
            status_cnc TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (id_badan_usaha) REFERENCES badan_usaha(id_badan_usaha)
        )
    """)

    n_bu = n_pz = 0
    for row in src.execute("SELECT * FROM badan_usaha"):
        d = dict(row)
        url = (f"https://minerbaone.esdm.go.id/publik/badan-usaha/detail/"
               f"{d['id_badan_usaha']}") if d['id_badan_usaha'] else None
        cur.execute("""
            INSERT INTO badan_usaha (id_badan_usaha, id_jenis_badan_usaha, nib,
                nama_badan_usaha, kode_badan_usaha, no_telp, email, fax,
                npwp_badan_usaha, rt, rw, alamat, kode_pos, kode_desa,
                jenis_badan_usaha, deskripsi_jenis_badan_usaha, minerbaone_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (d['id_badan_usaha'], d['id_jenis_badan_usaha'], d['nib'],
              d['nama_badan_usaha'], d['kode_badan_usaha'], d['no_telp'],
              d['email'], d['fax'], d['npwp_badan_usaha'], d['rt'], d['rw'],
              d['alamat'], d['kode_pos'], d['kode_desa'],
              d['jenis_badan_usaha'], d['deskripsi_jenis_badan_usaha'], url))
        n_bu += 1

    for row in src.execute("SELECT * FROM perizinan"):
        d = dict(row)
        cur.execute("""
            INSERT INTO perizinan (id_perizinan, id_badan_usaha, id_komoditas,
                id_golongan, id_jenis_perizinan, id_tahap_kegiatan, id_wiup,
                id_status_cnc, nomor_izin, luas_ha, tanggal_penetapan,
                tanggal_berlaku, tanggal_berakhir, lokasi_perizinan,
                nama_komoditas, nama_golongan, nama_tahap_kegiatan,
                jenis_perizinan, status_cnc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (d['id_perizinan'], d['id_badan_usaha'], d['id_komoditas'],
              d['id_golongan'], d['id_jenis_perizinan'], d['id_tahap_kegiatan'],
              d['id_wiup'], d['id_status_cnc'], d['nomor_izin'], d['luas_ha'],
              d['tanggal_penetapan'], d['tanggal_berlaku'], d['tanggal_berakhir'],
              d['lokasi_perizinan'], d['nama_komoditas'], d['nama_golongan'],
              d['nama_tahap_kegiatan'], d['jenis_perizinan'], d['status_cnc']))
        n_pz += 1

    conn.commit()
    src.close()
    print(f"     ✓ badan_usaha: {n_bu} rows", file=sys.stderr)
    print(f"     ✓ perizinan  : {n_pz} rows", file=sys.stderr)


def step_geoportal(conn, geojson_path):
    """Insert WIUP polygons + attrs from Geoportal."""
    print(f"\n[2/7] Loading Geoportal polygons", file=sys.stderr)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS wiup_geoportal")
    cur.execute("""
        CREATE TABLE wiup_geoportal (
            kode_wiup TEXT PRIMARY KEY,
            nama_usaha TEXT,
            sk_iup TEXT,
            komoditas TEXT,
            nama_prov TEXT,
            nama_kab TEXT,
            kab_normalized TEXT,
            luas_sk REAL,
            tgl_berlak_ms INTEGER,
            tgl_akhir_ms INTEGER,
            iup_year INTEGER,
            cnc TEXT,
            jenis_izin TEXT,
            kegiatan TEXT,
            pulau TEXT,
            lokasi TEXT,
            kode_prov TEXT,
            kode_golon TEXT,
            kode_jnsko TEXT,
            badan_usah TEXT,
            pejabat TEXT,
            bbox_min_lon REAL,
            bbox_min_lat REAL,
            bbox_max_lon REAL,
            bbox_max_lat REAL,
            tiles TEXT,
            geometry_type TEXT,
            geometry_geojson TEXT
        )
    """)

    def normalize_kab(name):
        if not name:
            return None
        n = name.strip().upper()
        for pfx in ("KAB. ", "KABUPATEN ", "KOTA "):
            if n.startswith(pfx):
                n = n[len(pfx):]
                break
        return n.strip() or None

    gj = json.loads(Path(geojson_path).read_text())
    n = 0
    for f in gj["features"]:
        p = f["properties"]
        g = f["geometry"]
        kode = p.get("kode_wiup")
        if not kode:
            continue
        minx, miny, maxx, maxy = feature_bbox(g)
        tiles = pick_tile(miny, maxy, minx, maxx)
        # IUP year: WIUP_Publish gives tgl_berlaku as ISO 'YYYY-MM-DD'; the older
        # Join_WIUP_vs_IPPKH layer gave tgl_berlak as epoch ms. Handle both.
        iup_year = None
        raw_date = p.get("tgl_berlaku") or p.get("tgl_berlak")
        if raw_date not in (None, ""):
            if isinstance(raw_date, str) and "-" in raw_date:
                try:
                    iup_year = int(raw_date[:4])
                except ValueError:
                    pass
            else:
                try:
                    iup_year = time.gmtime(float(raw_date) / 1000).tm_year
                except (ValueError, OSError):
                    pass
        # Normalize commodity & pejabat casing so "BATUBARA"/"Batubara" and
        # "MENTERI"/"Menteri" don't split GROUP BY aggregations (WIUP_Publish mixes cases).
        komoditas = norm_upper(p.get("komoditas"))
        pejabat = norm_upper(p.get("pejabat"))
        cur.execute("""
            INSERT OR REPLACE INTO wiup_geoportal VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            kode, p.get("nama_usaha"), p.get("sk_iup"), komoditas,
            p.get("nama_prov"), p.get("nama_kab"), normalize_kab(p.get("nama_kab")),
            to_num(p.get("luas_sk")), to_int(p.get("tgl_berlak")),
            to_int(p.get("tgl_akhir")), iup_year, p.get("cnc"),
            p.get("jenis_izin"), p.get("kegiatan"), p.get("pulau"),
            p.get("lokasi"), p.get("kode_prov"), p.get("kode_golon"),
            p.get("kode_jnsko"), p.get("badan_usah"), pejabat,
            minx, miny, maxx, maxy, "|".join(tiles),
            g["type"], json.dumps(g, separators=(",", ":")),
        ))
        n += 1
    conn.commit()
    print(f"     ✓ wiup_geoportal: {n} rows", file=sys.stderr)


def _create_loss_tables(cur, if_not_exists=False):
    """SATU pemilik skema wiup_loss + wiup_loss_yearly. Dipakai step_loss
    (DROP+CREATE, isi data) dan --phase registry (IF NOT EXISTS, cangkang
    kosong) supaya skema tak pernah bisa beda antara dua fase."""
    ine = "IF NOT EXISTS " if if_not_exists else ""
    cur.execute(f"""
        CREATE TABLE {ine}wiup_loss (
            kode_wiup TEXT PRIMARY KEY,
            polygon_area_ha REAL,
            forest_2000_ha REAL,
            total_loss_ha REAL,
            loss_pct_of_polygon REAL,
            loss_pct_of_forest REAL,
            tiles TEXT,
            threshold INTEGER DEFAULT 30,
            hansen_version TEXT DEFAULT 'GFC-2025-v1.13',
            FOREIGN KEY (kode_wiup) REFERENCES wiup_geoportal(kode_wiup)
        )
    """)
    cur.execute(f"""
        CREATE TABLE {ine}wiup_loss_yearly (
            kode_wiup TEXT,
            year INTEGER,
            loss_ha REAL,
            PRIMARY KEY (kode_wiup, year),
            FOREIGN KEY (kode_wiup) REFERENCES wiup_geoportal(kode_wiup)
        )
    """)


def _create_temporal_table(cur, if_not_exists=False):
    """SATU pemilik skema wiup_temporal (lihat _create_loss_tables)."""
    ine = "IF NOT EXISTS " if if_not_exists else ""
    cur.execute(f"""
        CREATE TABLE {ine}wiup_temporal (
            kode_wiup TEXT PRIMARY KEY,
            iup_year INTEGER,
            loss_pre_iup_ha REAL,
            loss_post_iup_ha REAL,
            n_years_pre INTEGER,
            n_years_post INTEGER,
            rate_pre_ha_per_year REAL,
            rate_post_ha_per_year REAL,
            ratio_post_pre TEXT,
            verdict TEXT,
            FOREIGN KEY (kode_wiup) REFERENCES wiup_geoportal(kode_wiup)
        )
    """)


def step_loss(conn, batch_csv, only_wiup=None):
    """Insert per-WIUP loss summary + yearly long-format.

    only_wiup: bila diberikan (set kode_wiup — fase pengukuran), baris CSV di
    luar set DILEWATI. Setara persis dgn jalur lama "insert semua → filter_minerba
    cascade-delete WHERE kode_wiup NOT IN wiup_geoportal", tapi tanpa pernah
    melanggar FOREIGN KEY ke wiup_geoportal yang sudah tersaring."""
    print(f"\n[3/7] Loading Hansen loss analysis", file=sys.stderr)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS wiup_loss")
    cur.execute("DROP TABLE IF EXISTS wiup_loss_yearly")
    _create_loss_tables(cur)

    n_summary = n_yearly = 0
    with open(batch_csv) as f:
        for row in csv.DictReader(f):
            kw = row["kode_wiup"]
            if only_wiup is not None and kw not in only_wiup:
                continue
            cur.execute("""
                INSERT OR REPLACE INTO wiup_loss
                (kode_wiup, polygon_area_ha, forest_2000_ha, total_loss_ha,
                 loss_pct_of_polygon, loss_pct_of_forest, tiles)
                VALUES (?,?,?,?,?,?,?)
            """, (kw,
                  to_num(row.get("polygon_area_ha")),
                  to_num(row.get("forest_2000_ha")),
                  to_num(row.get("total_loss_ha")),
                  to_num(row.get("loss_pct_of_polygon")),
                  to_num(row.get("loss_pct_of_forest")),
                  row.get("tiles")))
            n_summary += 1
            for y in range(2001, 2026):
                v = to_num(row.get(f"loss_{y}_ha"), 0)
                if v and v > 0:
                    cur.execute("""
                        INSERT OR REPLACE INTO wiup_loss_yearly
                        (kode_wiup, year, loss_ha) VALUES (?,?,?)
                    """, (kw, y, v))
                    n_yearly += 1
    conn.commit()
    print(f"     ✓ wiup_loss        : {n_summary} rows", file=sys.stderr)
    print(f"     ✓ wiup_loss_yearly : {n_yearly} non-zero entries", file=sys.stderr)


def step_temporal(conn, csv_path, only_wiup=None):
    """Insert temporal pre/post-IUP analysis (only_wiup: lihat step_loss)."""
    print(f"\n[4/7] Loading temporal IUP analysis", file=sys.stderr)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS wiup_temporal")
    _create_temporal_table(cur)
    n = 0
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if only_wiup is not None and row["kode_wiup"] not in only_wiup:
                continue
            cur.execute("""
                INSERT OR REPLACE INTO wiup_temporal VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (row["kode_wiup"], to_int(row.get("iup_year")),
                  to_num(row.get("loss_pre_iup_ha")),
                  to_num(row.get("loss_post_iup_ha")),
                  to_int(row.get("n_years_pre")),
                  to_int(row.get("n_years_post")),
                  to_num(row.get("rate_pre_ha_per_year")),
                  to_num(row.get("rate_post_ha_per_year")),
                  row.get("ratio_post_pre"), row.get("verdict")))
            n += 1
    conn.commit()
    print(f"     ✓ wiup_temporal: {n} rows", file=sys.stderr)


def step_kepadatan(conn, csv_path):
    """Load BPS population density per kabupaten/kota (2015–2024) from CSV.

    Source: data/kepadatan_penduduk.csv (BPS). Previously this table was ingested
    manually outside the pipeline, so a rebuilt DB silently lost it — now it is a
    first-class, reproducible step keyed off the committed CSV.
    """
    print(f"\n[+] Loading kepadatan_penduduk from {csv_path}", file=sys.stderr)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS kepadatan_penduduk")
    cur.execute("""
        CREATE TABLE kepadatan_penduduk (
            kode_kabkot TEXT PRIMARY KEY,
            provinsi TEXT,
            kabupaten TEXT,
            kab_normalized TEXT,
            d2015 REAL, d2016 REAL, d2017 REAL, d2018 REAL, d2019 REAL,
            d2020 REAL, d2021 REAL, d2022 REAL, d2023 REAL, d2024 REAL,
            satuan TEXT,
            sumber TEXT
        )
    """)
    years = [f"d{y}" for y in range(2015, 2025)]
    n = 0
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            cur.execute(
                "INSERT OR REPLACE INTO kepadatan_penduduk VALUES (" + ",".join(["?"] * 16) + ")",
                (row["kode_kabkot"], row["provinsi"], row["kabupaten"], row["kab_normalized"],
                 *[to_num(row.get(y)) for y in years], row.get("satuan"), row.get("sumber")))
            n += 1
    conn.commit()
    print(f"     ✓ kepadatan_penduduk: {n} rows", file=sys.stderr)


def _match_pairs(conn, pairs):
    """Inti pencocokan SK persis — SATU pemilik, dipakai kedua jalur (enriched
    CSV lama & registry langsung). Semantik PORT PERSIS dari enrich_with_db.py:
    lookup dict VERBATIM nomor_izin ↔ sk_iup (TANPA normalisasi apa pun —
    strip/kapital/format beda = TIDAK match; itu jatah match_harder T1+),
    baris perizinan dgn nomor_izin NULL/'' dikecualikan, duplikat nomor_izin
    → baris terakhir menang (assignment dict, bukan setdefault).

    pairs: iterable (kode_wiup, sk_iup) — sk '' bila kosong (perilaku CSV
    round-trip jalur lama: None jadi '')."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS wiup_match")
    cur.execute("""
        CREATE TABLE wiup_match (
            kode_wiup TEXT PRIMARY KEY,
            sk_iup TEXT,
            id_perizinan TEXT,
            id_badan_usaha TEXT,
            db_match TEXT,
            minerbaone_url TEXT,
            FOREIGN KEY (kode_wiup) REFERENCES wiup_geoportal(kode_wiup),
            FOREIGN KEY (id_perizinan) REFERENCES perizinan(id_perizinan),
            FOREIGN KEY (id_badan_usaha) REFERENCES badan_usaha(id_badan_usaha)
        )
    """)

    # Build sk → id_perizinan lookup
    sk_lookup = {}
    for r in cur.execute("""
        SELECT nomor_izin, id_perizinan, id_badan_usaha
        FROM perizinan WHERE nomor_izin IS NOT NULL AND nomor_izin != ''
    """):
        sk_lookup[r[0]] = (r[1], r[2])

    n = matched = 0
    for kode_wiup, sk in pairs:
        found = sk_lookup.get(sk)
        id_pz = id_bu = None
        db_match = "no"
        url = None
        if found:
            id_pz, id_bu = found
            db_match = "yes"
            if id_bu:
                url = (f"https://minerbaone.esdm.go.id/publik/"
                       f"badan-usaha/detail/{id_bu}")
            matched += 1
        cur.execute("""
            INSERT OR REPLACE INTO wiup_match VALUES (?,?,?,?,?,?)
        """, (kode_wiup, sk, id_pz, id_bu, db_match, url))
        n += 1
    conn.commit()
    print(f"     ✓ wiup_match: {n} rows, {matched} matched ({100*matched/n:.1f}%)",
          file=sys.stderr)


def step_match(conn, enriched_csv):
    """Link Geoportal WIUP → MinerbaOne — jalur LAMA (--phase full): baca
    kode_wiup + sk_iup dari enriched CSV (hasil enrich_with_db)."""
    print(f"\n[5/7] Building match table (Geoportal ↔ MinerbaOne)", file=sys.stderr)
    with open(enriched_csv) as f:
        pairs = [(row["kode_wiup"], row.get("sk_iup"))
                 for row in csv.DictReader(f)]
    _match_pairs(conn, pairs)


def step_match_registry(conn):
    """Link Geoportal WIUP → MinerbaOne — jalur BARU (--phase registry): match
    LANGSUNG wiup_geoportal × perizinan, tanpa lewat enriched CSV. sk_iup NULL
    → '' meniru persis CSV round-trip jalur lama (nilai tersimpan identik).
    Beda satu-satunya dgn jalur lama: cakupan = SEMUA baris geoportal, termasuk
    WIUP yang tak pernah muncul di CSV batch (poligon yang batch_analyze skip)."""
    print(f"\n[5/7] Building match table (Geoportal ↔ MinerbaOne, langsung)",
          file=sys.stderr)
    cur = conn.cursor()
    pairs = [(kode, sk if sk is not None else "")
             for kode, sk in cur.execute(
                 "SELECT kode_wiup, sk_iup FROM wiup_geoportal")]
    _match_pairs(conn, pairs)


def step_indexes(conn):
    """Add indexes for query performance."""
    print(f"\n[6/7] Creating indexes", file=sys.stderr)
    cur = conn.cursor()
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_pz_badan ON perizinan(id_badan_usaha)",
        "CREATE INDEX IF NOT EXISTS idx_pz_nomor ON perizinan(nomor_izin)",
        "CREATE INDEX IF NOT EXISTS idx_bu_nama ON badan_usaha(nama_badan_usaha)",
        "CREATE INDEX IF NOT EXISTS idx_geo_komo ON wiup_geoportal(komoditas)",
        "CREATE INDEX IF NOT EXISTS idx_geo_prov ON wiup_geoportal(nama_prov)",
        "CREATE INDEX IF NOT EXISTS idx_geo_kab ON wiup_geoportal(kab_normalized)",
        "CREATE INDEX IF NOT EXISTS idx_geo_usaha ON wiup_geoportal(nama_usaha)",
        "CREATE INDEX IF NOT EXISTS idx_loss_total ON wiup_loss(total_loss_ha)",
        "CREATE INDEX IF NOT EXISTS idx_loss_pct ON wiup_loss(loss_pct_of_forest)",
        "CREATE INDEX IF NOT EXISTS idx_yearly_year ON wiup_loss_yearly(year)",
        "CREATE INDEX IF NOT EXISTS idx_temporal_verdict ON wiup_temporal(verdict)",
        "CREATE INDEX IF NOT EXISTS idx_match_bu ON wiup_match(id_badan_usaha)",
    ]
    for stmt in indexes:
        cur.execute(stmt)
    conn.commit()
    print(f"     ✓ {len(indexes)} indexes created", file=sys.stderr)


def _drop_stale_empty_lapisan_shell(conn, table, expected_cols):
    """LAPISAN_SHELLS pakai `CREATE TABLE IF NOT EXISTS` supaya DB tanpa
    attribution_sawit.py (mis. data-full/kalimantan.db, yang skrip itu TIDAK
    disentuh — "hanya data/", lihat rescrape/process.sh) tetap punya cangkang
    kosong utk wiup_master. Tapi IF NOT EXISTS berarti skema LAMA tak pernah
    ikut naik kalau kolomnya berubah (Task F15 menambah 3 kolom) — cangkang
    lama akan bikin CREATE VIEW gagal DIQUERY (SQLite lazy-resolve kolom view).
    Aman dibongkar HANYA kalau tabelnya KOSONG (0 baris) — data ASLI dari
    attribution_sawit.py (825 baris di data/kalimantan.db) tak pernah kosong,
    jadi ini TAK PERNAH menghapus data nyata, hanya cangkang audit yang belum
    diisi pipeline sawit."""
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute(f'PRAGMA table_info("{table}")')}
    if not cols or not (expected_cols - cols):
        return  # tabel belum ada (CREATE TABLE IF NOT EXISTS akan bikin skema baru) ATAU sudah skema baru
    n = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if n == 0:
        cur.execute(f'DROP TABLE "{table}"')


def step_master_view(conn):
    """Create wiup_master VIEW joining all relevant tables (termasuk lapisan
    atribusi_sawit/klasifikasi_izin). Satu-satunya sumber definisi view —
    dipanggil dari main() (build penuh) maupun refresh_view() (--refresh-view)."""
    print(f"\n[7/7] Creating wiup_master view", file=sys.stderr)
    cur = conn.cursor()
    _drop_stale_empty_lapisan_shell(conn, "atribusi_sawit", {
        "loss_sawit_pra_izin_ha", "loss_sawit_pasca_izin_2021_ha", "loss_pasca_izin_2021_ha"})
    cur.executescript(LAPISAN_SHELLS)
    cur.execute("DROP VIEW IF EXISTS wiup_master")
    cur.execute("""
        CREATE VIEW wiup_master AS
        SELECT
            g.kode_wiup,
            g.nama_usaha,
            g.sk_iup,
            g.komoditas,
            g.nama_prov,
            g.nama_kab,
            g.kab_normalized,
            g.luas_sk,
            g.iup_year,
            g.cnc,
            g.jenis_izin,
            g.lokasi,
            l.polygon_area_ha,
            l.forest_2000_ha,
            l.total_loss_ha,
            l.loss_pct_of_polygon,
            l.loss_pct_of_forest,
            l.tiles AS hansen_tiles,
            t.loss_pre_iup_ha,
            t.loss_post_iup_ha,
            t.rate_pre_ha_per_year,
            t.rate_post_ha_per_year,
            t.ratio_post_pre,
            t.verdict AS temporal_verdict,
            m.db_match,
            m.minerbaone_url,
            m.id_badan_usaha,
            b.nama_badan_usaha,
            b.nib,
            b.npwp_badan_usaha,
            b.alamat,
            b.kode_pos,
            b.no_telp,
            b.email,
            b.jenis_badan_usaha,
            p.tanggal_berlaku,
            p.tanggal_berakhir,
            p.tanggal_penetapan,
            p.nama_tahap_kegiatan,
            p.status_cnc,
            s.loss_2001_2021_ha, s.loss_sawit_tol2th_ha, s.loss_sawit_jeda5th_ha,
            s.loss_sawit_tahunsama_ha, s.loss_2022_2025_ha,
            ROUND(s.loss_2001_2021_ha - s.loss_sawit_tol2th_ha, 2)          AS loss_bersih_ha,
            CASE WHEN s.loss_2001_2021_ha > 0
                 THEN ROUND(100.0 * s.loss_sawit_tol2th_ha / s.loss_2001_2021_ha, 2)
            END                                                              AS persen_sawit,
            -- Task F15: silang dua sumbu pra/pasca-izin × sawit (passthrough +
            -- 2 kolom "bersih" dihitung di sini, satu-satunya sumber definisi).
            s.loss_sawit_pra_izin_ha, s.loss_sawit_pasca_izin_2021_ha,
            s.loss_pasca_izin_2021_ha,
            CASE WHEN g.iup_year IS NOT NULL AND g.iup_year <= 2022
                 THEN ROUND(t.loss_pre_iup_ha - s.loss_sawit_pra_izin_ha, 2)
            END                                                              AS loss_pra_izin_bersih_ha,
            ROUND(s.loss_pasca_izin_2021_ha - s.loss_sawit_pasca_izin_2021_ha, 2)
                                                                               AS loss_pasca_izin_2021_bersih_ha,
            z.kelas  AS kelas_izin, z.bukti AS bukti_izin, z.dasar AS dasar_kelas,
            z.durasi_sk, z.masa_berlaku_diwarisi, z.pra_izin_dominan
        FROM wiup_geoportal g
        LEFT JOIN wiup_loss l ON l.kode_wiup = g.kode_wiup
        LEFT JOIN wiup_temporal t ON t.kode_wiup = g.kode_wiup
        LEFT JOIN wiup_match m ON m.kode_wiup = g.kode_wiup
        LEFT JOIN badan_usaha b ON b.id_badan_usaha = m.id_badan_usaha
        LEFT JOIN perizinan p ON p.id_perizinan = m.id_perizinan
        LEFT JOIN atribusi_sawit  s ON s.kode_wiup = g.kode_wiup
        LEFT JOIN klasifikasi_izin z ON z.kode_wiup = g.kode_wiup
    """)
    conn.commit()
    print(f"     ✓ wiup_master view created", file=sys.stderr)


def refresh_view(db_path):
    """Buat cangkang lapisan (bila absen) + rebuild wiup_master pada DB yang
    SUDAH ADA — dipakai test unit maupun CLI `--refresh-view`. Tak menyentuh
    tabel lain (badan_usaha, wiup_geoportal, dst.); satu sumber definisi view
    = step_master_view (DRY), supaya build penuh & refresh tak pernah bisa
    beda skema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    step_master_view(conn)
    conn.close()


def step_demo_queries(conn):
    """Run a few demo queries to show DB working."""
    cur = conn.cursor()
    print("\n" + "="*60, file=sys.stderr)
    print("  DEMO QUERIES", file=sys.stderr)
    print("="*60, file=sys.stderr)

    print("\n[Top 5 konsesi by absolute loss]", file=sys.stderr)
    for r in cur.execute("""
        SELECT nama_usaha, komoditas, nama_prov,
               ROUND(total_loss_ha, 0) as loss_ha,
               ROUND(loss_pct_of_forest, 1) as pct
        FROM wiup_master
        WHERE total_loss_ha IS NOT NULL
        ORDER BY total_loss_ha DESC LIMIT 5
    """):
        print(f"  {r[0]:<28} {r[1]:<12} {r[2][:18]:<18} "
              f"{int(r[3]):>8,} ha ({r[4]:>5.1f}%)", file=sys.stderr)

    print("\n[Total loss per komoditas]", file=sys.stderr)
    for r in cur.execute("""
        SELECT komoditas, COUNT(*) as n,
               ROUND(SUM(total_loss_ha), 0) as total_loss
        FROM wiup_master
        GROUP BY komoditas
        ORDER BY total_loss DESC LIMIT 5
    """):
        print(f"  {r[0]:<24} n={r[1]:>3}  total={int(r[2]):>10,} ha",
              file=sys.stderr)

    print("\n[Konsesi BERAU COAL full info]", file=sys.stderr)
    for r in cur.execute("""
        SELECT nama_usaha, sk_iup, ROUND(total_loss_ha, 0),
               ROUND(loss_pct_of_forest, 1), nib, jenis_badan_usaha,
               minerbaone_url, alamat
        FROM wiup_master WHERE nama_usaha = 'BERAU COAL'
    """):
        print(f"  Nama   : {r[0]}", file=sys.stderr)
        print(f"  SK IUP : {r[1]}", file=sys.stderr)
        print(f"  Loss   : {int(r[2]):,} ha ({r[3]}% of forest)", file=sys.stderr)
        print(f"  NIB    : {r[4]}", file=sys.stderr)
        print(f"  Jenis  : {r[5]}", file=sys.stderr)
        print(f"  URL    : {r[6]}", file=sys.stderr)
        print(f"  Alamat : {(r[7] or '')[:80]}", file=sys.stderr)


def attach_pengukuran(db_path, batch_csv, temporal_csv):
    """--phase pengukuran: tempel step_loss + step_temporal ke DB target yang
    SUDAH ADA (hasil --phase registry, sebelum/atau sesudah filter_minerba).
    Baris CSV dibatasi ke kode_wiup yang ada di wiup_geoportal target —
    setara persis cascade-delete filter_minerba jalur lama. step_indexes
    diulang karena DROP TABLE ikut membunuh indeks di tabel pengukuran."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    only = {r[0] for r in conn.execute("SELECT kode_wiup FROM wiup_geoportal")}
    step_loss(conn, batch_csv, only_wiup=only)
    step_temporal(conn, temporal_csv, only_wiup=only)
    step_indexes(conn)
    conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-db", type=Path,
                        default=Path("data/minerba-kalimantan.db"))
    parser.add_argument("--geojson", type=Path,
                        default=Path("data/wiup/kalimantan_unique.geojson"))
    parser.add_argument("--batch-csv", type=Path,
                        default=Path("data/analysis/batch_KALIMANTAN_t30_wide.csv"))
    parser.add_argument("--temporal-csv", type=Path,
                        default=Path("data/analysis/temporal_iup_analysis.csv"))
    parser.add_argument("--enriched-csv", type=Path,
                        default=Path("data/analysis/batch_KALIMANTAN_t30_enriched.csv"))
    parser.add_argument("--kepadatan-csv", type=Path,
                        default=Path("data/kepadatan_penduduk.csv"),
                        help="BPS population density CSV (optional; skipped if absent)")
    parser.add_argument("--output", type=Path, default=Path("data/kalimantan.db"))
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing output DB")
    parser.add_argument("--refresh-view", type=Path, default=None, metavar="DB",
                        help="hanya buat cangkang lapisan (bila absen) + rebuild "
                             "wiup_master pada DB yang sudah ada, lalu keluar "
                             "(tak butuh --source-db/--geojson/dst.)")
    parser.add_argument("--phase", choices=["registry", "pengukuran", "full"],
                        default="full",
                        help="registry = bagian 1 (identitas izin + cangkang "
                             "pengukuran kosong); pengukuran = bagian 2 (tempel "
                             "loss+temporal ke --db); full = perilaku lama utuh")
    parser.add_argument("--db", type=Path, default=None, metavar="DB",
                        help="DB target utk --phase pengukuran (harus sudah ada)")
    args = parser.parse_args(argv)

    if args.refresh_view is not None:
        if not args.refresh_view.exists():
            print(f"ERROR: {args.refresh_view} tak ada", file=sys.stderr)
            return 1
        print(f"Refreshing wiup_master view di {args.refresh_view}", file=sys.stderr)
        refresh_view(args.refresh_view)
        print(f"  ✓ selesai", file=sys.stderr)
        return 0

    if args.phase == "pengukuran":
        if args.db is None:
            print("ERROR: --phase pengukuran butuh --db <target>", file=sys.stderr)
            return 1
        for name, p in [("db", args.db), ("batch-csv", args.batch_csv),
                        ("temporal-csv", args.temporal_csv)]:
            if not p.exists():
                print(f"ERROR: missing input '{name}': {p}", file=sys.stderr)
                return 1
        print(f"Attaching pengukuran → {args.db}", file=sys.stderr)
        attach_pengukuran(args.db, args.batch_csv, args.temporal_csv)
        print(f"  ✓ selesai", file=sys.stderr)
        return 0

    # Verify inputs (registry tak butuh CSV pengukuran/enriched)
    required = [("source-db", args.source_db), ("geojson", args.geojson)]
    if args.phase == "full":
        required += [("batch-csv", args.batch_csv),
                     ("temporal-csv", args.temporal_csv),
                     ("enriched-csv", args.enriched_csv)]
    for name, p in required:
        if not p.exists():
            print(f"ERROR: missing input '{name}': {p}", file=sys.stderr)
            return 1

    if args.output.exists():
        if not args.force:
            print(f"ERROR: {args.output} exists. Use --force to overwrite.",
                  file=sys.stderr)
            return 1
        args.output.unlink()

    print(f"Building {args.output}", file=sys.stderr)
    print(f"  Source DB : {args.source_db}", file=sys.stderr)
    print(f"  GeoJSON   : {args.geojson}", file=sys.stderr)
    print(f"  Batch CSV : {args.batch_csv}", file=sys.stderr)

    conn = sqlite3.connect(args.output)
    conn.execute("PRAGMA foreign_keys = ON")

    t0 = time.time()
    step_copy_minerba(conn, args.source_db)
    step_geoportal(conn, args.geojson)
    if args.phase == "registry":
        # Cangkang pengukuran KOSONG (skema PERSIS step_loss/step_temporal via
        # pemilik skema yang sama) supaya indeks + view valid sebelum bagian 2.
        cur = conn.cursor()
        _create_loss_tables(cur, if_not_exists=True)
        _create_temporal_table(cur, if_not_exists=True)
        conn.commit()
        step_match_registry(conn)
    else:
        step_loss(conn, args.batch_csv)
        step_temporal(conn, args.temporal_csv)
        step_match(conn, args.enriched_csv)
    # Optional BPS density — kept optional so older input sets still build.
    if args.kepadatan_csv.exists():
        step_kepadatan(conn, args.kepadatan_csv)
    else:
        print(f"     ⚠ skip kepadatan (tak ada {args.kepadatan_csv})", file=sys.stderr)
    step_indexes(conn)
    step_master_view(conn)

    elapsed = time.time() - t0
    size_mb = args.output.stat().st_size / 1024 / 1024

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  ✅ Built {args.output} ({size_mb:.2f} MB) in {elapsed:.1f}s",
          file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if args.phase == "full":
        # Demo mengasumsikan pengukuran terisi (SUM/int atas loss) — di
        # registry tabel loss masih cangkang kosong, jadi dilewati.
        step_demo_queries(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
