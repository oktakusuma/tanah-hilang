#!/usr/bin/env python3
"""Langkah 01 — identitas konsesi (SKEMA.md §0 `sumber`, §1 `konsesi`, `konsesi_registri`,
`kepadatan_penduduk`; §8 `v_konsesi` bila semua tabel sudah ada). Baris `sumber` yang ditulis: hansen,
geoportal_wiup, minerbaone, bps, geoboundaries — `mapbiomas` milik 04_mapbiomas.py, `geoportal_hutan` milik 06.

    python pipeline/01_identitas.py --db data/tanah-hilang.db --himpunan minerba

Logika di-PORT (bukan ditulis ulang) dari pipeline arsip:
  * scripts/build_combined_db.py  — muat geojson WIUP_Publish, normalisasi komoditas/kabupaten,
    tahun_izin dari tgl_berlaku, bbox, pencocokan SK-persis (`_match_pairs`: lookup verbatim,
    duplikat nomor_izin → baris terakhir menang, sk NULL → ''), kepadatan BPS (unpivot).
  * scripts/match_harder.py       — T1 SK dinormalkan → T3 digit saja → T2 nama fuzzy (≥0,88),
    urutan & ambang sama persis; hasil langsung diterapkan (tanpa --apply).
  * scripts/build_kawasan_hutan.py (bagian `wiup_tanggal_pulih`) — tgl_berlaku/tgl_berakhir
    dipulihkan dari layer Overlay_WIUP_vs_Kawasan_Hutan (identik arsip; keputusan W0 2 Sep 2026 —
    keyakinan pra-izin bergantung padanya). tahun_izin tetap dari tgl_berlaku WIUP_Publish (= iup_year).
  * luas_poligon_ha = `polygon_area_ha` batch CSV Hansen (identik arsip wiup_loss); konsesi yang
    tak punya baris CSV (poligon lebih kecil dari satu piksel raster) memakai luas geometri
    (derajat² × cos lintang, konvensi _geo_common) dan dicatat di `bangun`.

Idempoten: DROP + CREATE tabel miliknya saja. Gagal keras bila input absen.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib import db as L  # noqa: E402
from pipeline.lib.himpunan import termasuk  # noqa: E402
from pipeline.lib.meta import LISENSI, pastikan_meta, tulis_meta, tulis_sumber  # noqa: E402
from pipeline.lib.w1_util import (cocok_nama_fuzzy, hanya_digit, kabupaten_norm, ke_angka,  # noqa: E402
                                  normalisasi_sk, pastikan_v_konsesi, tahun_dari, tanggal_iso,
                                  teks_upper)

SKRIP = "pipeline/01_identitas.py"
GEOJSON = L.AKAR / "data/wiup/kalimantan_unique.geojson"
REGISTRI = L.AKAR / "data/minerba-kalimantan.db"
KEPADATAN = L.AKAR / "data/kepadatan_penduduk.csv"
BATCH = L.AKAR / "data/analysis/batch_KALIMANTAN_t30_wide.csv"
OVERLAY = L.AKAR / "data/geoportal/overlay_hutan.geojson"
URL_MINERBAONE = "https://minerbaone.esdm.go.id/publik/badan-usaha/detail/{}"
DEG_LAT_METERS = 111_320.0  # sama dgn scripts/_geo_common.DEG_LAT_METERS

DDL = """
CREATE TABLE konsesi (
  kode_wiup        TEXT PRIMARY KEY,
  nama_usaha       TEXT NOT NULL,
  sk_iup           TEXT,
  komoditas        TEXT NOT NULL,
  jenis_izin       TEXT,
  kegiatan         TEXT,
  luas_sk_ha       REAL,
  luas_poligon_ha  REAL NOT NULL,
  tahun_izin       INTEGER,
  tgl_berlaku      TEXT,
  tgl_berakhir     TEXT,
  asal_tanggal     TEXT,
  provinsi         TEXT, kabupaten TEXT, kabupaten_norm TEXT,
  lokasi           TEXT,
  cnc              TEXT,
  geometri_geojson TEXT NOT NULL,
  bbox_min_lon REAL, bbox_min_lat REAL, bbox_max_lon REAL, bbox_max_lat REAL
);
CREATE TABLE konsesi_registri (
  kode_wiup          TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  cocok              INTEGER NOT NULL,
  strategi_cocok     TEXT,
  id_perizinan       TEXT, id_badan_usaha TEXT,
  nama_badan_usaha   TEXT, nib TEXT, npwp TEXT, alamat TEXT, kode_pos TEXT, jenis_badan_usaha TEXT,
  tanggal_berlaku    TEXT, tanggal_berakhir TEXT, tanggal_penetapan TEXT,
  tahap_kegiatan     TEXT, status_cnc TEXT,
  url_minerbaone     TEXT
);
CREATE TABLE kepadatan_penduduk (
  kode_kabkot TEXT, provinsi TEXT, kabupaten TEXT, kabupaten_norm TEXT,
  tahun INTEGER, kepadatan REAL, satuan TEXT, sumber TEXT,
  PRIMARY KEY (kode_kabkot, tahun)
);
"""


# ───────────────────────────── geometri ──────────────────────────────────
def bbox_geom(g: dict):
    c = g["coordinates"]
    if g["type"] == "Polygon":
        pts = [pt for r in c for pt in r]
    elif g["type"] == "MultiPolygon":
        pts = [pt for poly in c for r in poly for pt in r]
    else:
        L.gagal(f"tipe geometri tak didukung: {g['type']}")
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _luas_cincin_deg2(cincin) -> float:
    """Rumus shoelace (derajat²), tanda diabaikan."""
    s = 0.0
    n = len(cincin)
    for i in range(n):
        x1, y1 = cincin[i][:2]
        x2, y2 = cincin[(i + 1) % n][:2]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def luas_geometri_ha(g: dict) -> float:
    """Luas poligon lon/lat → ha, koreksi cos(lintang pusat bbox) — konvensi
    build_kawasan_hutan.luas_ha / _geo_common. Hanya cadangan bila batch CSV tak punya baris."""
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    deg2 = 0.0
    for poly in polys:
        deg2 += _luas_cincin_deg2(poly[0]) - sum(_luas_cincin_deg2(r) for r in poly[1:])
    _, miny, _, maxy = bbox_geom(g)
    lat = (miny + maxy) / 2
    return deg2 * DEG_LAT_METERS * DEG_LAT_METERS * math.cos(math.radians(lat)) / 10_000.0


# ─────────────────────────────── muat ────────────────────────────────────
def muat_konsesi(himpunan: str) -> list[dict]:
    gj = json.loads(GEOJSON.read_text(encoding="utf-8"))
    baris, n_semua = [], 0
    for f in gj["features"]:
        p = f["properties"]; g = f["geometry"]
        kode = p.get("kode_wiup")
        if not kode or not g:
            continue
        n_semua += 1
        komoditas = teks_upper(p.get("komoditas"))
        if not termasuk(himpunan, komoditas):
            continue
        # tahun_izin = tahun tgl_berlaku Geoportal WIUP_Publish (identik arsip iup_year). Pasangan
        # tanggal harian TIDAK diambil dari sini melainkan dari layer overlay (lihat pulihkan_tanggal),
        # supaya identik arsip wiup_tanggal_pulih yang menjadi tumpuan keyakinan pra-izin (sinyal B/G).
        tgl_b = tanggal_iso(p.get("tgl_berlaku") if p.get("tgl_berlaku") not in (None, "") else p.get("tgl_berlak"))
        minx, miny, maxx, maxy = bbox_geom(g)
        baris.append({
            "kode_wiup": kode, "nama_usaha": p.get("nama_usaha") or "", "sk_iup": p.get("sk_iup"),
            "komoditas": komoditas or "", "jenis_izin": p.get("jenis_izin"), "kegiatan": p.get("kegiatan"),
            "luas_sk_ha": ke_angka(p.get("luas_sk")), "luas_poligon_ha": None,
            "tahun_izin": tahun_dari(tgl_b),
            "tgl_berlaku": None, "tgl_berakhir": None, "asal_tanggal": None,
            "provinsi": p.get("nama_prov"), "kabupaten": p.get("nama_kab"),
            "kabupaten_norm": kabupaten_norm(p.get("nama_kab")),
            "lokasi": p.get("lokasi"), "cnc": p.get("cnc"),
            "geometri_geojson": json.dumps(g, separators=(",", ":")),
            "bbox_min_lon": minx, "bbox_min_lat": miny, "bbox_max_lon": maxx, "bbox_max_lat": maxy,
            "_geom": g,
        })
    print(f"  geojson: {n_semua} WIUP, himpunan {himpunan} → {len(baris)} konsesi")
    return baris


def pulihkan_tanggal(baris: list[dict]) -> int:
    """tgl_berlaku/tgl_berakhir dari layer Overlay_WIUP_vs_Kawasan_Hutan (epoch ms → UTC) — PORT
    build_kawasan_hutan.bangun_kawasan (arsip wiup_tanggal_pulih): fitur PERTAMA per kode_wiup yang
    dipakai, pasangan tanggal diambil apa adanya (boleh salah satunya NULL). Keputusan W0 (2 Sep 2026):
    kolom ini harus identik arsip karena keyakinan pra-izin bergantung padanya; tgl_berlaku ISO milik
    WIUP_Publish TIDAK dipakai di sini (hanya untuk tahun_izin) — keduanya kerap selisih satu hari."""
    per = {b["kode_wiup"]: b for b in baris}
    n = 0
    for f in json.loads(OVERLAY.read_text(encoding="utf-8"))["features"]:
        a = f.get("properties") or {}
        b = per.get(a.get("kode_wiup"))
        if b is None or b["asal_tanggal"] is not None:
            continue
        b["tgl_berlaku"], b["tgl_berakhir"] = tanggal_iso(a.get("tgl_berlak")), tanggal_iso(a.get("tgl_akhir"))
        b["asal_tanggal"] = "ippkh_pulih"
        n += 1
    return n


def isi_luas_poligon(baris: list[dict]) -> list[str]:
    luas = {}
    with open(BATCH, newline="") as f:
        for r in csv.DictReader(f):
            luas[r["kode_wiup"]] = ke_angka(r.get("polygon_area_ha"))
    tanpa = []
    for b in baris:
        v = luas.get(b["kode_wiup"])
        if v is None:
            v = round(luas_geometri_ha(b["_geom"]), 2)
            tanpa.append(b["kode_wiup"])
        b["luas_poligon_ha"] = v
    return tanpa


def cocokkan_registri(baris: list[dict]) -> tuple[list[dict], dict]:
    """T0 SK-persis (build_combined_db._match_pairs) lalu T1/T3/T2 (match_harder) pada sisa."""
    reg = sqlite3.connect(f"file:{REGISTRI}?mode=ro", uri=True)
    reg.row_factory = sqlite3.Row
    # T0: lookup VERBATIM; duplikat nomor_izin → baris terakhir menang (assignment dict).
    persis = {}
    for r in reg.execute("SELECT nomor_izin, id_perizinan, id_badan_usaha FROM perizinan "
                         "WHERE nomor_izin IS NOT NULL AND nomor_izin != ''"):
        persis[r[0]] = (r[1], r[2])
    # T1–T3: lookup ala match_harder (setdefault → baris pertama menang; urutan rowid perizinan).
    by_norm, by_digit, by_nama = {}, {}, {}
    for r in reg.execute("""SELECT p.id_perizinan, p.id_badan_usaha, p.nomor_izin, b.nama_badan_usaha
                            FROM perizinan p LEFT JOIN badan_usaha b ON b.id_badan_usaha = p.id_badan_usaha
                            WHERE p.nomor_izin IS NOT NULL AND p.nomor_izin != ''"""):
        by_norm.setdefault(normalisasi_sk(r["nomor_izin"]), (r["id_perizinan"], r["id_badan_usaha"]))
        d = hanya_digit(r["nomor_izin"])
        if len(d) >= 8:
            by_digit.setdefault(d, (r["id_perizinan"], r["id_badan_usaha"]))
        if r["nama_badan_usaha"]:
            from pipeline.lib.w1_util import normalisasi_nama
            by_nama.setdefault(normalisasi_nama(r["nama_badan_usaha"]), []).append(
                (r["id_perizinan"], r["id_badan_usaha"]))

    hasil, hitung = [], {"T0_exact": 0, "T1_norm_sk": 0, "T3_digits": 0, "T2_fuzzy_name": 0, None: 0}
    for b in sorted(baris, key=lambda x: x["kode_wiup"]):
        sk = b["sk_iup"] if b["sk_iup"] is not None else ""
        strategi, ids = None, None
        if sk in persis:
            strategi, ids = "T0_exact", persis[sk]
        else:
            ns = normalisasi_sk(b["sk_iup"])
            d = hanya_digit(b["sk_iup"])
            if ns and ns in by_norm:
                strategi, ids = "T1_norm_sk", by_norm[ns]
            elif len(d) >= 8 and d in by_digit:
                strategi, ids = "T3_digits", by_digit[d]
            else:
                hit = cocok_nama_fuzzy(b["nama_usaha"], by_nama)
                if hit:
                    strategi, ids = "T2_fuzzy_name", hit[1][0]
        hitung[strategi] += 1
        baris_r = {"kode_wiup": b["kode_wiup"], "cocok": int(strategi is not None), "strategi_cocok": strategi,
                   "id_perizinan": None, "id_badan_usaha": None, "nama_badan_usaha": None, "nib": None,
                   "npwp": None, "alamat": None, "kode_pos": None, "jenis_badan_usaha": None,
                   "tanggal_berlaku": None, "tanggal_berakhir": None, "tanggal_penetapan": None,
                   "tahap_kegiatan": None, "status_cnc": None, "url_minerbaone": None}
        if ids:
            id_pz, id_bu = ids
            baris_r.update(id_perizinan=id_pz, id_badan_usaha=id_bu,
                           url_minerbaone=URL_MINERBAONE.format(id_bu) if id_bu else None)
            p = reg.execute("SELECT tanggal_berlaku, tanggal_berakhir, tanggal_penetapan, nama_tahap_kegiatan, "
                            "status_cnc FROM perizinan WHERE id_perizinan=?", (id_pz,)).fetchone()
            if p:
                baris_r.update(tanggal_berlaku=p[0], tanggal_berakhir=p[1], tanggal_penetapan=p[2],
                               tahap_kegiatan=p[3], status_cnc=p[4])
            u = reg.execute("SELECT nama_badan_usaha, nib, npwp_badan_usaha, alamat, kode_pos, jenis_badan_usaha "
                            "FROM badan_usaha WHERE id_badan_usaha=?", (id_bu,)).fetchone() if id_bu else None
            if u:
                baris_r.update(nama_badan_usaha=u[0], nib=u[1], npwp=u[2], alamat=u[3], kode_pos=u[4],
                               jenis_badan_usaha=u[5])
        hasil.append(baris_r)
    reg.close()
    return hasil, hitung


def muat_kepadatan() -> list[tuple]:
    out = []
    with open(KEPADATAN, newline="") as f:
        for r in csv.DictReader(f):
            for th in range(2015, 2025):
                out.append((r["kode_kabkot"], r["provinsi"], r["kabupaten"], r["kab_normalized"], th,
                            ke_angka(r.get(f"d{th}")), r.get("satuan"), r.get("sumber")))
    return out


# ─────────────────────────────── sumber ──────────────────────────────────
def tulis_semua_sumber(con: sqlite3.Connection) -> None:
    tulis_sumber(con, "hansen", "Hansen Global Forest Change (UMD/Google/USGS/NASA)", "CC BY 4.0",
                 versi="GFC-2025 v1.13",
                 url="https://storage.googleapis.com/earthenginepartners-hansen/GFC-2025-v1.13/download.html",
                 tanggal_akses="2026-05-18", cakupan_tahun="2001-2024 (dipakai; raster tersedia s.d. 2025) + tutupan pohon 2000",
                 sitasi="Hansen, M.C. et al. (2013). High-Resolution Global Maps of 21st-Century Forest Cover Change. "
                        "Science 342: 850-853. Data v1.13 (GFC-2025) dari Hansen Earth Engine Partners bucket.",
                 catatan="Ambang kanopi 30% (standar publikasi). Kehilangan = hilangnya tutupan pohon apa pun, "
                         "bukan semata deforestasi hutan alam. Tahun 2025 sengaja tidak dibawa (jendela tesis 2001–2024).")
    # id 'mapbiomas' DITULIS oleh 04_mapbiomas.py (W2); 'geoportal_hutan' oleh 06_kawasan_hutan.py (W3).
    tulis_sumber(con, "geoportal_wiup", "Geoportal ESDM — layer WIUP_Publish (poligon WIUP)",
                 "Data publik pemerintah (Geoportal ESDM, tanpa token)",
                 versi="WIUP_Publish (ArcGIS REST, f=geojson)", url="https://geoportal.esdm.go.id/minerba/",
                 tanggal_akses="2026-07-11", cakupan_tahun=None,
                 sitasi="Kementerian ESDM, Geoportal ESDM — layer WIUP_Publish, diakses 11 Jul 2026.",
                 catatan="tgl_berlaku = tanggal SK yang berlaku SAAT INI (bukan izin pertama). Geometri berubah "
                         "antar scrape — jangan scrape ulang tanpa membangun ulang tabel MapBiomas (hash geometri dijaga).")
    tulis_sumber(con, "minerbaone", "MinerbaOne — Kementerian ESDM (registri badan usaha & perizinan)",
                 "Data publik pemerintah (MinerbaOne, API publik)",
                 versi="data/minerba-kalimantan.db (perizinan 8.461, badan_usaha 7.572)",
                 url="https://minerbaone.esdm.go.id/", tanggal_akses="2026-07-11", cakupan_tahun=None,
                 sitasi="Kementerian ESDM, MinerbaOne — tabel badan usaha & perizinan via API publik, diakses 11 Jul 2026.",
                 catatan="Registri merekam masa berlaku SEKARANG, bukan sejarah izin; dicocokkan ke WIUP lewat nomor SK.")
    tulis_sumber(con, "bps", "BPS — Kepadatan Penduduk per Kabupaten/Kota", "Data publik BPS",
                 versi="Kepadatan Penduduk per Kabupaten/Kota 2015–2024", url="https://www.bps.go.id/",
                 tanggal_akses=None, cakupan_tahun="2015-2024",
                 sitasi="Badan Pusat Statistik. Kepadatan Penduduk menurut Kabupaten/Kota, 2015–2024.",
                 catatan="Tanggal akses tak tercatat di repo; berkas data/kepadatan_penduduk.csv bertanggal 10 Jul 2026.")
    tulis_sumber(con, "geoboundaries", "geoBoundaries — batas administrasi (ADM1/ADM2 Indonesia)", "CC BY 4.0",
                 versi="geoBoundaries 2020", url="https://www.geoboundaries.org/", tanggal_akses=None, cakupan_tahun=None,
                 sitasi="Runfola, D. et al. (2020). geoBoundaries: A global database of political administrative "
                        "boundaries. PLoS ONE 15(4): e0231866.",
                 catatan="Dipakai hanya untuk peta (webapp/public/kalimantan-kabupaten.geojson, -provinsi.geojson); "
                         "tidak ada tabel turunan di DB ini.")


# ─────────────────────────────── meta ────────────────────────────────────
def tulis_semua_meta(con: sqlite3.Connection, himpunan: str, n_tanpa_csv: int) -> None:
    g = "Geoportal ESDM WIUP_Publish (data/wiup/kalimantan_unique.geojson)"
    o = "Geoportal ESDM gis1 Overlay_WIUP_vs_Kawasan_Hutan (data/geoportal/overlay_hutan.geojson)"
    tulis_meta(con, "konsesi",
               deskripsi=f"Identitas & poligon tiap konsesi (WIUP) himpunan '{himpunan}': "
                         f"{'batubara + mineral logam' if himpunan == 'minerba' else 'semua WIUP Kalimantan termasuk galian C'}. "
                         "Satu baris per kode_wiup; kunci seluruh tabel lain.",
               sumber=f"{g}; tanggal pulih: data/geoportal/overlay_hutan.geojson; luas poligon: "
                      "data/analysis/batch_KALIMANTAN_t30_wide.csv (polygon_area_ha)",
               metode="Muat geojson, saring komoditas via lib/himpunan.termasuk, komoditas & kabupaten dinormalkan "
                      "UPPER, tahun_izin = 4 digit pertama tgl_berlaku WIUP_Publish; tgl_berlaku/tgl_berakhir harian "
                      "dipulihkan dari layer overlay kawasan hutan (epoch ms → UTC; fitur pertama per kode_wiup, "
                      "port build_kawasan_hutan). luas_poligon_ha = jumlah luas piksel Hansen di dalam poligon "
                      f"(batch CSV); {n_tanpa_csv} konsesi tanpa baris CSV memakai luas geometri (derajat² × cos lintang).",
               skrip=SKRIP, lisensi=LISENSI["geoportal"], kolom=[
        ("kode_wiup", "Kode WIUP resmi ESDM — kunci utama.", "-", g),
        ("nama_usaha", "Nama badan usaha pemegang menurut Geoportal.", "-", g),
        ("sk_iup", "Nomor SK izin yang berlaku saat ini.", "-", g),
        ("komoditas", "Komoditas apa adanya dari Geoportal, dinormalkan UPPER (BATUBARA, EMAS, …).", "UPPER(TRIM(komoditas))", g),
        ("jenis_izin", "Jenis izin: IUP / IUPK / PKP2B / KK / IPR / WIUP.", "-", g),
        ("kegiatan", "Tahap kegiatan menurut Geoportal (Eksplorasi / Operasi Produksi / sanksi …).", "-", g),
        ("luas_sk_ha", "Luas menurut SK, ha (eks luas_sk).", "-", g),
        ("luas_poligon_ha", "Luas geometri poligon, ha — jumlah luas piksel Hansen (koreksi lintang per baris) "
                            "di dalam poligon; identik arsip wiup_loss.polygon_area_ha.",
         "Σ luas piksel (mask poligon) dari batch_analyze; cadangan: luas derajat² × 111320² × cos(lat) / 10000",
         "data/analysis/batch_KALIMANTAN_t30_wide.csv"),
        ("tahun_izin", "Tahun SK yang berlaku SAAT INI (bukan izin pertama) — eks iup_year; dari tgl_berlaku "
                       "WIUP_Publish (ISO). Bisa selisih 1 tahun dgn tahun tgl_berlaku (sumber overlay) pada SK "
                       "bertanggal pergantian tahun.", "CAST(substr(WIUP_Publish.tgl_berlaku,1,4) AS INTEGER)", g),
        ("tgl_berlaku", "Tanggal SK mulai berlaku 'YYYY-MM-DD' — DIPULIHKAN dari layer Overlay_WIUP_vs_Kawasan_Hutan "
                        "(identik arsip wiup_tanggal_pulih; tumpuan keyakinan pra-izin). Kerap selisih 1 hari dgn "
                        "tgl_berlaku WIUP_Publish (artefak zona waktu).", "overlay tgl_berlak (epoch ms → tanggal UTC)", o),
        ("tgl_berakhir", "Tanggal SK berakhir 'YYYY-MM-DD' — sumber sama dengan tgl_berlaku.", "overlay tgl_akhir (epoch ms → tanggal UTC)", o),
        ("asal_tanggal", "Asal pasangan tanggal: 'ippkh_pulih' (layer overlay) | NULL (kode_wiup tak ada di layer overlay). "
                         "Nilai 'geoportal' dicadangkan bila kelak WIUP_Publish dipakai kembali.", "-", o),
        ("provinsi", "Nama provinsi (eks nama_prov).", "-", g),
        ("kabupaten", "Nama kabupaten/kota apa adanya (eks nama_kab).", "-", g),
        ("kabupaten_norm", "Kabupaten dinormalkan (tanpa awalan KAB./KOTA, UPPER) — kunci join ke kepadatan_penduduk.", "normalize_kab(nama_kab)", g),
        ("lokasi", "Keterangan lokasi bebas dari Geoportal.", "-", g),
        ("cnc", "Status Clean and Clear menurut Geoportal.", "-", g),
        ("geometri_geojson", "Geometri poligon (GeoJSON, EPSG:4326, padat tanpa spasi).", "json.dumps(geometry, separators=(',',':'))", g),
        ("bbox_min_lon", "Bujur minimum kotak pembatas.", "min(lon)", g),
        ("bbox_min_lat", "Lintang minimum kotak pembatas.", "min(lat)", g),
        ("bbox_max_lon", "Bujur maksimum kotak pembatas.", "max(lon)", g),
        ("bbox_max_lat", "Lintang maksimum kotak pembatas.", "max(lat)", g),
    ])
    m = "data/minerba-kalimantan.db (perizinan, badan_usaha)"
    tulis_meta(con, "konsesi_registri",
               deskripsi="Hasil pencocokan tiap konsesi ke registri MinerbaOne; satu baris per konsesi, "
                         "kolom registri NULL bila tak cocok. Profil perusahaan (NIB, alamat) & tanggal izin registri.",
               sumber=f"konsesi.sk_iup/nama_usaha × {m}",
               metode="T0 SK persis verbatim (duplikat nomor_izin → baris terakhir), lalu pada sisa: T1 SK dinormalkan "
                      "(spasi/strip/pemisah), T3 hanya digit (≥8 digit), T2 nama badan usaha fuzzy "
                      "(SequenceMatcher ≥0,88; berisiko positif-palsu). Port build_combined_db._match_pairs + match_harder.",
               skrip=SKRIP, lisensi=LISENSI["minerbaone"], kolom=[
        ("kode_wiup", "Kode WIUP (kunci, FK konsesi).", "-", "konsesi"),
        ("cocok", "1 bila tercocokkan ke MinerbaOne, 0 bila tidak (eks db_match yes/no).", "strategi_cocok IS NOT NULL", m),
        ("strategi_cocok", "Strategi yang berhasil: T0_exact | T1_norm_sk | T2_fuzzy_name | T3_digits | NULL.", "urutan T0→T1→T3→T2", m),
        ("id_perizinan", "ID perizinan MinerbaOne.", "-", m),
        ("id_badan_usaha", "ID badan usaha MinerbaOne.", "-", m),
        ("nama_badan_usaha", "Nama badan usaha menurut MinerbaOne.", "-", m),
        ("nib", "Nomor Induk Berusaha.", "-", m),
        ("npwp", "NPWP badan usaha (eks npwp_badan_usaha).", "-", m),
        ("alamat", "Alamat badan usaha.", "-", m),
        ("kode_pos", "Kode pos badan usaha.", "-", m),
        ("jenis_badan_usaha", "Bentuk badan usaha (PT/CV/…).", "-", m),
        ("tanggal_berlaku", "Tanggal berlaku izin menurut registri (masa berlaku SEKARANG).", "-", m),
        ("tanggal_berakhir", "Tanggal berakhir izin menurut registri.", "-", m),
        ("tanggal_penetapan", "Tanggal penetapan izin menurut registri.", "-", m),
        ("tahap_kegiatan", "Tahap kegiatan menurut registri (eks nama_tahap_kegiatan).", "-", m),
        ("status_cnc", "Status Clean and Clear menurut registri.", "-", m),
        ("url_minerbaone", "Tautan profil badan usaha di MinerbaOne.", "https://minerbaone.esdm.go.id/publik/badan-usaha/detail/{id_badan_usaha}", m),
    ])
    b = "data/kepadatan_penduduk.csv (BPS)"
    tulis_meta(con, "kepadatan_penduduk",
               deskripsi="Kepadatan penduduk BPS per kabupaten/kota Kalimantan 2015–2024, bentuk panjang "
                         "(satu baris per kabupaten × tahun). Disalin apa adanya dari CSV.",
               sumber=b, metode="Unpivot kolom d2015..d2024 CSV BPS menjadi (tahun, kepadatan).",
               skrip=SKRIP, lisensi=LISENSI["bps"], kolom=[
        ("kode_kabkot", "Kode BPS kabupaten/kota (4 digit).", "-", b),
        ("provinsi", "Nama provinsi.", "-", b),
        ("kabupaten", "Nama kabupaten/kota.", "-", b),
        ("kabupaten_norm", "Kabupaten dinormalkan — kunci join ke konsesi.kabupaten_norm.", "-", b),
        ("tahun", "Tahun data (2015–2024).", "-", b),
        ("kepadatan", "Kepadatan penduduk.", "-", b),
        ("satuan", "Satuan kepadatan (jiwa/km2).", "-", b),
        ("sumber", "Teks sumber BPS apa adanya.", "-", b),
    ])


# ─────────────────────────────── main ────────────────────────────────────
def main() -> int:
    ap = L.argparser("01 identitas: sumber, konsesi, konsesi_registri, kepadatan_penduduk")
    a = ap.parse_args()
    t0 = time.time()
    L.wajib_ada(GEOJSON, REGISTRI, KEPADATAN, BATCH, OVERLAY, keterangan="input 01_identitas")

    baris = muat_konsesi(a.himpunan)
    if not baris:
        L.gagal("tidak ada konsesi lolos saringan himpunan")
    n_pulih = pulihkan_tanggal(baris)
    tanpa_csv = isi_luas_poligon(baris)
    print(f"  tanggal SK: {n_pulih} dipulihkan dari layer overlay, "
          f"{sum(1 for b in baris if b['tgl_berlaku'] is None)} NULL; tahun_izin terisi "
          f"{sum(1 for b in baris if b['tahun_izin'] is not None)}")
    if tanpa_csv:
        print(f"  {len(tanpa_csv)} konsesi tanpa baris batch CSV → luas geometri: {', '.join(tanpa_csv)}")
    registri, hitung = cocokkan_registri(baris)
    print("  registri: " + ", ".join(f"{k or 'tak cocok'}={v}" for k, v in hitung.items()))
    kepadatan = muat_kepadatan()

    con = L.buka(a.db)
    pastikan_meta(con)
    # DROP konsesi harus lolos walau tabel hilir (hansen_*, dst.) masih merujuknya — FK dimatikan
    # sementara; bangun.sh dari nol menghapus DB, jadi ini hanya untuk jalan ulang parsial.
    con.execute("PRAGMA foreign_keys = OFF")
    con.executescript("DROP VIEW IF EXISTS v_konsesi; DROP TABLE IF EXISTS konsesi_registri; "
                      "DROP TABLE IF EXISTS kepadatan_penduduk; DROP TABLE IF EXISTS konsesi;")
    con.executescript(DDL)
    con.executemany("""INSERT INTO konsesi VALUES (:kode_wiup,:nama_usaha,:sk_iup,:komoditas,:jenis_izin,:kegiatan,
        :luas_sk_ha,:luas_poligon_ha,:tahun_izin,:tgl_berlaku,:tgl_berakhir,:asal_tanggal,:provinsi,:kabupaten,
        :kabupaten_norm,:lokasi,:cnc,:geometri_geojson,:bbox_min_lon,:bbox_min_lat,:bbox_max_lon,:bbox_max_lat)""", baris)
    con.executemany("""INSERT INTO konsesi_registri VALUES (:kode_wiup,:cocok,:strategi_cocok,:id_perizinan,:id_badan_usaha,
        :nama_badan_usaha,:nib,:npwp,:alamat,:kode_pos,:jenis_badan_usaha,:tanggal_berlaku,:tanggal_berakhir,
        :tanggal_penetapan,:tahap_kegiatan,:status_cnc,:url_minerbaone)""", registri)
    con.executemany("INSERT INTO kepadatan_penduduk VALUES (?,?,?,?,?,?,?,?)", kepadatan)
    con.execute("CREATE INDEX IF NOT EXISTS idx_konsesi_komoditas ON konsesi(komoditas)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_konsesi_provinsi ON konsesi(provinsi)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_konsesi_kabupaten ON konsesi(kabupaten_norm)")
    con.commit()
    con.execute("PRAGMA foreign_keys = ON")

    tulis_semua_sumber(con)
    tulis_semua_meta(con, a.himpunan, len(tanpa_csv))
    L.tulis_bangun(con, "himpunan", a.himpunan)
    L.tulis_bangun(con, "konsesi.n", len(baris))
    L.tulis_bangun(con, "konsesi.hash_geometri", L.hash_geometri(con))
    L.tulis_bangun(con, "konsesi.tanpa_batch_csv", ",".join(tanpa_csv) or "-")
    pastikan_v_konsesi(con, SKRIP)
    L.tandai_selesai(con, "01_identitas", n_konsesi=len(baris), n_cocok=sum(r["cocok"] for r in registri),
                     n_tanggal_pulih=n_pulih, n_kepadatan=len(kepadatan))
    con.close()
    print(f"01_identitas selesai: {len(baris)} konsesi, {sum(r['cocok'] for r in registri)} cocok registri, "
          f"{len(kepadatan)} baris kepadatan → {a.db} ({time.time()-t0:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
