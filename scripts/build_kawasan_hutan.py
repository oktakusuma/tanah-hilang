#!/usr/bin/env python3
# STATUS  : ARSIP — konsesi_ippkh / ippkh_konsesi_irisan / konsesi_kawasan_hutan / wiup_tanggal_pulih di kalimantan.db v2
# CATATAN : pengganti: pipeline/06_kawasan_hutan.py (ippkh, ippkh_irisan, kawasan_hutan); bagian tanggal pulih pindah ke 01_identitas.py
# LABEL   : 3 Sep 2026 (bundel publik disetel ke pipeline v3 `pipeline/bangun.sh` — lihat README §7; jangan dihapus, tidak dipanggil bangun.sh)
"""Bangun tabel IPPKH + status kawasan hutan per konsesi.

MASALAH YANG DIJAWAB (pertanyaan istri, 31 Agu 2026):
"berapa dari 825 konsesi kita yang benar-benar memegang IPK/IPPKH?"

SUMBER. Tiga layer publik Geoportal ESDM gis1, diunduh oleh
`scripts/fetch_geoportal_hutan.py` ke `data/geoportal/*.geojson`.

DUA JALUR PENCOCOKAN IPPKH → KONSESI, dan keduanya dilaporkan terpisah
supaya bisa saling memeriksa:

  spasial  irisan poligon IPPKH x poligon WIUP. Ini yang MENENTUKAN — nama
           perusahaan sering beda ejaan antar registri, sedangkan letak tak
           bisa berbohong. Ambang: irisan >= 1 ha ATAU >= 1% luas IPPKH,
           supaya sentuhan tepi tak terhitung sebagai kepemilikan.
  nama     `nama_ppkh` vs `nama_usaha`, memakai `match_harder.normalize_name`
           (jangan bikin penormal kedua). Ini PEMERIKSA, bukan penentu.

Konsesi dianggap ber-IPPKH bila **jalur spasial** cocok. Kolom `cocok_nama`
disediakan agar selisih kedua jalur bisa diaudit, bukan disembunyikan.

KAVEAT WAJIB (diverifikasi 31 Agu 2026, jangan dihapus dari laporan mana pun):
  * Seluruh 397 IPPKH Kalimantan berstatus **Aktif** — nol yang tidak aktif.
    Layer ini POTRET izin yang berlaku, BUKAN register sejarah. Konsesi yang
    membuka hutan dengan IPPKH 2010 yang kini habis akan tampak seolah tak
    pernah punya. Sebaran tanggalnya membenarkan: 2008-2015 hanya 59 izin,
    2018-2023 ada 313. Layer berhenti 2023.
  * Karena itu angka di sini **BATAS BAWAH**. TIDAK boleh dibaca sebagai
    "sisanya membuka hutan tanpa izin" — itu butuh register historis KLHK.
  * **IPK (Izin Pemanfaatan Kayu) tidak ada di sini sama sekali** — instrumen
    KLHK/SIPUHH yang berbeda; belum ada jalan publik untuknya.
  * Layer eksplorasi (22 poligon Kalimantan) TANPA kolom nama — spasial saja.

BONUS YANG IKUT TERAMBIL. Layer `Overlay_WIUP_vs_Kawasan_Hutan` memberi
fungsi kawasan hutan (APL/HP/HPK/HL/HK/…) di dalam tiap WIUP, tersambung
lewat `kode_wiup` — tanpa perlu pencocokan spasial. Ia juga membawa
`tgl_berlak`/`tgl_akhir` yang di `wiup_geoportal` kita **semuanya NULL**
(regresi re-scrape), jadi sekaligus memulihkan durasi SK.

LUAS. Dihitung dari derajat² dikali cos(lintang pusat), memakai
`_geo_common.DEG_LAT_METERS` — konvensi luas yang SAMA dengan seluruh
pipeline raster proyek ini. Dipilih demi konsistensi angka antar tabel,
bukan demi presisi geodesi maksimum; galatnya jauh di bawah galat
digitasi poligon.

    python3 scripts/build_kawasan_hutan.py
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _geo_common as gc  # noqa: E402
from match_harder import normalize_name  # noqa: E402

GEO_DIR = Path("data/geoportal")
DB = Path("data/kalimantan.db")

# Ambang "IPPKH ini benar milik konsesi ini" — irisan harus berarti, bukan
# sekadar poligon bersinggungan di tepi.
AMBANG_HA = 1.0
AMBANG_PANGSA = 0.01

_SKRIP = "scripts/build_kawasan_hutan.py"

# Pipeline v2: skrip ini bisa jadi PENULIS PERTAMA analysis_meta/column_meta
# pada DB segar (blok arsip pemilik lamanya kini jalan belakangan & opsional).

META_DDL = """
    CREATE TABLE IF NOT EXISTS analysis_meta (
        nama_tabel TEXT PRIMARY KEY, deskripsi TEXT, sumber TEXT, metode TEXT,
        script TEXT, status TEXT NOT NULL DEFAULT 'AKTIF'
        CHECK (status IN ('AKTIF','ARSIP','PROYEKSI')));
    CREATE TABLE IF NOT EXISTS column_meta (
        nama_tabel TEXT, nama_kolom TEXT, deskripsi TEXT, rumus TEXT, sumber TEXT,
        PRIMARY KEY (nama_tabel, nama_kolom));
"""

_SUMBER = ("Geoportal ESDM gis1 — Izin_Pinjam_Pakai_Kawasan_Hutan (layer 0+1) "
           "dan Overlay_WIUP_vs_Kawasan_Hutan (layer 0), publik tanpa token, "
           "diunduh scripts/fetch_geoportal_hutan.py")


def luas_ha(geom) -> float:
    """Luas poligon lon/lat → hektar, koreksi cos(lintang pusat)."""
    if geom.is_empty:
        return 0.0
    lat = geom.centroid.y
    m_per_deg_lat = gc.DEG_LAT_METERS
    m_per_deg_lon = gc.DEG_LAT_METERS * math.cos(math.radians(lat))
    return geom.area * m_per_deg_lat * m_per_deg_lon / 10_000.0


def tanggal(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def muat(nama: str) -> list[dict]:
    p = GEO_DIR / f"{nama}.geojson"
    if not p.exists():
        raise SystemExit(f"{p} belum ada — jalankan fetch_geoportal_hutan.py dulu.")
    return json.loads(p.read_text(encoding="utf-8"))["features"]


# ─────────────────────────── IPPKH ────────────────────────────────────────
def baca_ippkh() -> list[dict]:
    """Gabung layer operasi + eksplorasi jadi satu daftar berbentuk sama."""
    out = []
    for layer, sumber in (("ippkh_operasi", "operasi"),
                          ("ippkh_eksplorasi", "eksplorasi")):
        for i, f in enumerate(muat(layer)):
            a = f.get("properties") or {}
            g = shape(f["geometry"])
            if g.is_empty:
                continue
            if not g.is_valid:
                g = g.buffer(0)
            out.append({
                "id": f"{sumber}:{a.get('objectid_1', i)}",
                "layer": sumber,
                # layer eksplorasi tak punya kolom nama/nomor — biarkan NULL,
                # jangan diisi tebakan.
                "nama_ppkh": a.get("nama_ppkh"),
                "no_ppkh": a.get("no_ppkh"),
                "tgl_ppkh": tanggal(a.get("tgl_ppkh")),
                "tgl_berakhir": tanggal(a.get("tgl_berakh")),
                "luas_ppkh_sk": a.get("luas_ppkh"),
                "jenis_ppkh": a.get("jenis_ppkh") or (
                    "Tambang (eksplorasi)" if sumber == "eksplorasi" else None),
                "status": a.get("status"),
                "kode_prov": a.get("kode_prov"),
                "luas_hitung_ha": round(luas_ha(g), 2),
                "geom": g,
            })
    return out


def cocokkan_ippkh(ippkh, wiup):
    """(baris konsesi_ippkh, baris ippkh_konsesi_irisan). Spasial menentukan."""
    pohon = STRtree([w["geom"] for w in wiup])
    nama_wiup = {}
    for i, w in enumerate(wiup):
        n = normalize_name(w["nama_usaha"])
        if n:
            nama_wiup.setdefault(n, []).append(i)

    irisan = []
    for p in ippkh:
        n_ppkh = normalize_name(p["nama_ppkh"]) if p["nama_ppkh"] else ""
        kandidat_nama = set(nama_wiup.get(n_ppkh, [])) if n_ppkh else set()
        for i in pohon.query(p["geom"]):
            i = int(i)
            w = wiup[i]
            try:
                pot = p["geom"].intersection(w["geom"])
            except Exception:                       # geometri bermasalah
                pot = p["geom"].buffer(0).intersection(w["geom"].buffer(0))
            if pot.is_empty:
                continue
            ha = luas_ha(pot)
            pangsa = ha / p["luas_hitung_ha"] if p["luas_hitung_ha"] else 0.0
            if ha < AMBANG_HA and pangsa < AMBANG_PANGSA:
                continue
            irisan.append((
                w["kode_wiup"], p["id"], p["layer"], p["nama_ppkh"], p["no_ppkh"],
                p["tgl_ppkh"], p["tgl_berakhir"], p["jenis_ppkh"], p["status"],
                p["luas_ppkh_sk"], p["luas_hitung_ha"], round(ha, 2),
                round(pangsa, 4), 1 if i in kandidat_nama else 0))
        # catat kecocokan nama yang TIDAK punya pasangan spasial (audit)
        for i in kandidat_nama:
            if not any(r[0] == wiup[i]["kode_wiup"] and r[1] == p["id"]
                       for r in irisan):
                irisan.append((
                    wiup[i]["kode_wiup"], p["id"], p["layer"], p["nama_ppkh"],
                    p["no_ppkh"], p["tgl_ppkh"], p["tgl_berakhir"],
                    p["jenis_ppkh"], p["status"], p["luas_ppkh_sk"],
                    p["luas_hitung_ha"], 0.0, 0.0, 1))
    return irisan


def ringkas_ippkh(irisan, wiup):
    """Satu baris per konsesi — termasuk yang TANPA IPPKH (nilai nol)."""
    per: dict[str, list] = {}
    for r in irisan:
        per.setdefault(r[0], []).append(r)
    out = []
    for w in wiup:
        rs = per.get(w["kode_wiup"], [])
        sp = [r for r in rs if r[11] > 0]                    # ada irisan spasial
        tambang = [r for r in sp if (r[7] or "").startswith("Tambang")]
        tgl = sorted(r[5] for r in sp if r[5])
        out.append((
            w["kode_wiup"],
            1 if sp else 0,
            1 if tambang else 0,
            len(sp), len(tambang),
            round(sum(r[11] for r in sp), 2),                # ha irisan
            round(sum(r[9] or 0 for r in sp), 2),            # ha menurut SK
            tgl[0] if tgl else None,
            tgl[-1] if tgl else None,
            1 if any(r[13] for r in rs) else 0,
            round(sum(r[11] for r in sp) / w["luas_sk"], 4)
            if w["luas_sk"] else None,
        ))
    return out


# ──────────────────── Kawasan hutan (overlay) ─────────────────────────────
def bangun_kawasan(wiup_kode: set[str]):
    """(baris fungsi kawasan per konsesi, baris tanggal SK yang dipulihkan)."""
    per: dict[tuple[str, int, str], float] = {}
    tanggal_sk: dict[str, tuple] = {}
    asing = set()
    for f in muat("overlay_hutan"):
        a = f.get("properties") or {}
        kode = a.get("kode_wiup")
        if not kode:
            continue
        if kode not in wiup_kode:
            asing.add(kode)
            continue
        g = shape(f["geometry"])
        if g.is_empty:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        kunci = (kode, a.get("fungsikws") or 0, (a.get("deskripsi") or "-").strip())
        per[kunci] = per.get(kunci, 0.0) + luas_ha(g)
        if kode not in tanggal_sk:
            tanggal_sk[kode] = (tanggal(a.get("tgl_berlak")),
                                tanggal(a.get("tgl_akhir")),
                                a.get("sk_iup"), a.get("jenis_izin"))
    if asing:
        print(f"  {len(asing)} kode_wiup di layer overlay TIDAK ada di 825 konsesi "
              "kita (WIUP Kalimantan lain / galian C) — dilewati.")
    baris = [(k, fk, desk, round(ha, 2)) for (k, fk, desk), ha in sorted(per.items())]

    tgl = []
    for kode, (b, a_, sk, ji) in sorted(tanggal_sk.items()):
        durasi = None
        if b and a_:
            durasi = int(a_[:4]) - int(b[:4])
        tgl.append((kode, b, a_, durasi, sk, ji))
    return baris, tgl


# ─────────────────────────── Tulis ────────────────────────────────────────
DDL = """
DROP TABLE IF EXISTS ippkh_konsesi_irisan;
DROP TABLE IF EXISTS konsesi_ippkh;
DROP TABLE IF EXISTS konsesi_kawasan_hutan;
DROP TABLE IF EXISTS wiup_tanggal_pulih;

CREATE TABLE ippkh_konsesi_irisan (
    kode_wiup TEXT NOT NULL, id_ippkh TEXT NOT NULL, layer TEXT NOT NULL,
    nama_ppkh TEXT, no_ppkh TEXT, tgl_ppkh TEXT, tgl_berakhir TEXT,
    jenis_ppkh TEXT, status TEXT,
    luas_ppkh_sk_ha REAL, luas_ppkh_hitung_ha REAL,
    luas_irisan_ha REAL NOT NULL, pangsa_ippkh_di_konsesi REAL NOT NULL,
    cocok_nama INTEGER NOT NULL,
    PRIMARY KEY (kode_wiup, id_ippkh));

CREATE TABLE konsesi_ippkh (
    kode_wiup TEXT PRIMARY KEY,
    punya_ippkh INTEGER NOT NULL, punya_ippkh_tambang INTEGER NOT NULL,
    n_ippkh INTEGER NOT NULL, n_ippkh_tambang INTEGER NOT NULL,
    luas_irisan_ha REAL NOT NULL, luas_ippkh_sk_ha REAL NOT NULL,
    tgl_ippkh_awal TEXT, tgl_ippkh_akhir TEXT,
    cocok_nama INTEGER NOT NULL, rasio_ippkh_thd_luas_sk REAL);

CREATE TABLE konsesi_kawasan_hutan (
    kode_wiup TEXT NOT NULL, fungsi_kode INTEGER NOT NULL,
    fungsi_nama TEXT NOT NULL, luas_ha REAL NOT NULL,
    PRIMARY KEY (kode_wiup, fungsi_kode, fungsi_nama));

CREATE TABLE wiup_tanggal_pulih (
    kode_wiup TEXT PRIMARY KEY, tgl_berlaku TEXT, tgl_akhir TEXT,
    durasi_sk_tahun INTEGER, sk_iup TEXT, jenis_izin TEXT);
"""

META = [
    ("ippkh_konsesi_irisan",
     "Setiap pasangan (konsesi, IPPKH) yang cocok — jalur spasial dan/atau nama. "
     "Baris beririsan-nol tapi cocok nama sengaja disimpan untuk audit.",
     _SUMBER, "Irisan poligon (shapely STRtree); ambang >=1 ha atau >=1% luas IPPKH. "
     "Nama dinormalkan match_harder.normalize_name.", _SKRIP, "AKTIF"),
    ("konsesi_ippkh",
     "Satu baris per konsesi (825, termasuk yang tanpa IPPKH): apakah memegang "
     "IPPKH aktif, berapa banyak, seluas apa. BATAS BAWAH — layer sumber hanya "
     "memuat IPPKH berstatus Aktif, bukan register sejarah.",
     _SUMBER, "Agregasi ippkh_konsesi_irisan yang punya irisan spasial > 0.",
     _SKRIP, "AKTIF"),
    ("konsesi_kawasan_hutan",
     "Luas tiap fungsi kawasan hutan (APL/HP/HPK/HL/HK/…) di dalam tiap konsesi. "
     "Penyebut status hukum lahan untuk analisis guna lahan.",
     _SUMBER, "Overlay_WIUP_vs_Kawasan_Hutan disambung lewat kode_wiup; luas "
     "cos(lintang).", _SKRIP, "AKTIF"),
    ("wiup_tanggal_pulih",
     "Tanggal berlaku/berakhir SK WIUP yang dipulihkan dari layer overlay — di "
     "wiup_geoportal kolom ini semuanya NULL akibat regresi re-scrape.",
     _SUMBER, "Diambil dari atribut tgl_berlak/tgl_akhir layer overlay; durasi = "
     "selisih tahun.", _SKRIP, "AKTIF"),
]

KOLOM = [
    ("konsesi_ippkh", "punya_ippkh", "1 bila ada IPPKH (jenis apa pun) beririsan.",
     "irisan >= 1 ha atau >= 1% luas IPPKH", _SUMBER),
    ("konsesi_ippkh", "punya_ippkh_tambang", "1 bila IPPKH-nya berjenis Tambang.",
     "jenis_ppkh LIKE 'Tambang%'", _SUMBER),
    ("konsesi_ippkh", "luas_irisan_ha", "Total luas IPPKH yang jatuh DI DALAM konsesi.",
     "SUM(luas irisan poligon)", _SUMBER),
    ("konsesi_ippkh", "luas_ippkh_sk_ha", "Total luas menurut SK IPPKH (bisa melampaui "
     "batas konsesi).", "SUM(luas_ppkh)", _SUMBER),
    ("konsesi_ippkh", "rasio_ippkh_thd_luas_sk",
     "Bagian konsesi yang tertutup IPPKH.", "luas_irisan_ha / luas_sk", _SUMBER),
    ("konsesi_ippkh", "cocok_nama", "1 bila nama pemegang IPPKH cocok nama badan usaha "
     "konsesi — PEMERIKSA, bukan penentu.", "normalize_name(a)==normalize_name(b)", _SUMBER),
    ("konsesi_ippkh", "tgl_ippkh_awal", "Tanggal SK IPPKH paling awal di konsesi ini. "
     "Dipakai sebagai bukti dokumenter kegiatan pra-izin.", "MIN(tgl_ppkh)", _SUMBER),
    ("konsesi_kawasan_hutan", "fungsi_kode", "Kode fungsi kawasan (kolom fungsikws).",
     "-", _SUMBER),
    ("konsesi_kawasan_hutan", "fungsi_nama", "Nama fungsi kawasan (kolom deskripsi).",
     "-", _SUMBER),
    ("wiup_tanggal_pulih", "durasi_sk_tahun", "Jangka SK dalam tahun — bahan uji "
     "UU 4/2009 Ps. 47 (pemberian pertama = 20 tahun).",
     "tahun(tgl_akhir) - tahun(tgl_berlaku)", _SUMBER),

    # ── sisa kolom (Konvensi #4/#5: cakupan dua arah, diikat verify_invariants) ──
    ("konsesi_ippkh", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER),
    ("konsesi_ippkh", "n_ippkh", "Banyak SK IPPKH yang beririsan dengan konsesi ini.",
     "COUNT irisan spasial > 0", _SUMBER),
    ("konsesi_ippkh", "n_ippkh_tambang", "Dari jumlah itu, yang berjenis Tambang.",
     "COUNT jenis_ppkh LIKE 'Tambang%'", _SUMBER),
    ("konsesi_ippkh", "tgl_ippkh_akhir", "Tanggal SK IPPKH paling akhir di konsesi ini.",
     "MAX(tgl_ppkh)", _SUMBER),
    ("konsesi_kawasan_hutan", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER),
    ("konsesi_kawasan_hutan", "luas_ha", "Luas irisan konsesi x fungsi kawasan ini.",
     "Luas poligon irisan, koreksi cos(lintang)", _SUMBER),
    ("wiup_tanggal_pulih", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER),
    ("wiup_tanggal_pulih", "tgl_berlaku", "Tanggal SK mulai berlaku — DIPULIHKAN; kolom "
     "ini NULL di wiup_geoportal akibat regresi re-scrape.", "-", _SUMBER),
    ("wiup_tanggal_pulih", "tgl_akhir", "Tanggal SK berakhir — idem, dipulihkan.",
     "-", _SUMBER),
    ("wiup_tanggal_pulih", "sk_iup", "Nomor SK menurut layer overlay — pembanding "
     "terhadap wiup_geoportal.sk_iup.", "-", _SUMBER),
    ("wiup_tanggal_pulih", "jenis_izin", "Jenis izin menurut layer overlay.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "kode_wiup", "Kode WIUP konsesi.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "id_ippkh", "Id poligon IPPKH (layer:objectid).", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "layer", "Asal layer: operasi atau eksplorasi. Layer "
     "eksplorasi TANPA kolom nama, jadi hanya cocok spasial.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "nama_ppkh", "Nama pemegang IPPKH (NULL di layer eksplorasi).",
     "-", _SUMBER),
    ("ippkh_konsesi_irisan", "no_ppkh", "Nomor SK IPPKH.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "tgl_ppkh", "Tanggal SK IPPKH.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "tgl_berakhir", "Tanggal IPPKH berakhir.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "jenis_ppkh", "Tambang / Non Tambang.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "status", "Status IPPKH. Di sumber ini SELALU 'Aktif' — "
     "itulah sebab angkanya batas bawah.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "luas_ppkh_sk_ha", "Luas menurut SK IPPKH.", "-", _SUMBER),
    ("ippkh_konsesi_irisan", "luas_ppkh_hitung_ha", "Luas poligon IPPKH hasil hitung "
     "sendiri — pembanding terhadap luas SK.",
     "Luas poligon, koreksi cos(lintang)", _SUMBER),
    ("ippkh_konsesi_irisan", "luas_irisan_ha", "Luas bagian IPPKH yang jatuh DI DALAM "
     "konsesi. Nol berarti hanya cocok nama, bukan spasial.",
     "Luas poligon irisan, koreksi cos(lintang)", _SUMBER),
    ("ippkh_konsesi_irisan", "pangsa_ippkh_di_konsesi",
     "Bagian poligon IPPKH yang berada di dalam konsesi.",
     "luas_irisan_ha / luas_ppkh_hitung_ha", _SUMBER),
    ("ippkh_konsesi_irisan", "cocok_nama", "1 bila nama pemegang cocok nama badan usaha "
     "konsesi. PEMERIKSA, bukan penentu.", "normalize_name(a)==normalize_name(b)", _SUMBER),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args(argv)

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    wiup = []
    for r in con.execute("SELECT kode_wiup, nama_usaha, luas_sk, geometry_geojson "
                         "FROM wiup_geoportal WHERE geometry_geojson IS NOT NULL"):
        g = shape(json.loads(r["geometry_geojson"]))
        if not g.is_valid:
            g = g.buffer(0)
        wiup.append({"kode_wiup": r["kode_wiup"], "nama_usaha": r["nama_usaha"],
                     "luas_sk": r["luas_sk"], "geom": g})
    print(f"{len(wiup)} konsesi bergeometri")

    ippkh = baca_ippkh()
    print(f"{len(ippkh)} poligon IPPKH "
          f"({sum(1 for p in ippkh if p['layer'] == 'operasi')} operasi, "
          f"{sum(1 for p in ippkh if p['layer'] == 'eksplorasi')} eksplorasi)")

    irisan = cocokkan_ippkh(ippkh, wiup)
    ringkas = ringkas_ippkh(irisan, wiup)
    print(f"{len(irisan)} pasangan (konsesi, IPPKH); "
          f"{sum(r[1] for r in ringkas)} konsesi ber-IPPKH, "
          f"{sum(r[2] for r in ringkas)} ber-IPPKH tambang")

    kawasan, tgl = bangun_kawasan({w["kode_wiup"] for w in wiup})
    print(f"{len(kawasan)} baris fungsi kawasan; "
          f"{len(tgl)} konsesi dapat tanggal SK pulih")

    con.executescript(META_DDL)
    con.executescript(DDL)
    con.executemany("INSERT INTO ippkh_konsesi_irisan VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", irisan)
    con.executemany("INSERT INTO konsesi_ippkh VALUES (?,?,?,?,?,?,?,?,?,?,?)", ringkas)
    con.executemany("INSERT INTO konsesi_kawasan_hutan VALUES (?,?,?,?)", kawasan)
    con.executemany("INSERT INTO wiup_tanggal_pulih VALUES (?,?,?,?,?,?)", tgl)
    con.executemany("INSERT OR REPLACE INTO analysis_meta "
                    "(nama_tabel,deskripsi,sumber,metode,script,status) "
                    "VALUES (?,?,?,?,?,?)", META)
    con.executemany("INSERT OR REPLACE INTO column_meta "
                    "(nama_tabel,nama_kolom,deskripsi,rumus,sumber) VALUES (?,?,?,?,?)",
                    KOLOM)
    con.commit()
    con.close()
    print("Selesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
