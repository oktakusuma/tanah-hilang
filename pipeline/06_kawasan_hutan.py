#!/usr/bin/env python3
"""Langkah 06 — kawasan hutan & IPPKH per konsesi (SKEMA.md §5).

Port dari `scripts/build_kawasan_hutan.py` (pipeline arsip) ke skema `tanah-hilang.db`:
logika pencocokan & luasnya SAMA, hanya sumber geometri konsesi kini tabel `konsesi` DB
target (bukan `wiup_geoportal`) dan bagian `wiup_tanggal_pulih` TIDAK ada di sini (itu milik
`01_identitas.py`, kolom `konsesi.tgl_berlaku/tgl_berakhir/asal_tanggal`).

PERTANYAAN YANG DIJAWAB: berapa konsesi yang benar-benar memegang IPPKH, dan fungsi kawasan
hutan apa saja yang ada di dalam tiap konsesi.

DUA JALUR PENCOCOKAN IPPKH → KONSESI, keduanya dilaporkan supaya saling memeriksa:
  spasial  irisan poligon IPPKH × poligon konsesi — INI YANG MENENTUKAN. Ambang: irisan
           >= 1 ha ATAU >= 1% luas IPPKH, supaya sentuhan tepi tak terhitung kepemilikan.
  nama     `nama_ppkh` vs `nama_usaha` lewat `scripts/match_harder.normalize_name`
           (satu-satunya penormal nama di proyek ini) — PEMERIKSA, bukan penentu.

KAVEAT WAJIB (diverifikasi 31 Agu 2026, jangan dihapus dari laporan mana pun):
  * Seluruh IPPKH di layer ini berstatus Aktif — ia POTRET izin yang berlaku, BUKAN register
    sejarah. Angka di sini BATAS BAWAH; tidak boleh dibaca "sisanya membuka hutan tanpa izin".
  * IPK (Izin Pemanfaatan Kayu) tidak ada di sumber ini sama sekali.
  * Layer eksplorasi tanpa kolom nama — hanya cocok spasial.
  * Irisan dengan Hutan Lindung / Kawasan Konservasi BUKAN bukti penambangan di sana.

LUAS: derajat² × cos(lintang pusat) memakai DEG_LAT_METERS yang sama dengan pipeline raster
(`scripts/_geo_common.py`) — demi konsistensi angka antar tabel, bukan presisi geodesi.

    .venv/bin/python pipeline/06_kawasan_hutan.py --db data/tanah-hilang.db --himpunan minerba
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib.db import AKAR, argparser, buka, gagal, tandai_selesai, wajib_ada, wajib_tabel  # noqa: E402
from pipeline.lib.meta import LISENSI, tulis_meta, tulis_sumber  # noqa: E402

SKRIP = "pipeline/06_kawasan_hutan.py"
GEO_DIR = AKAR / "data/geoportal"
MANIFEST = GEO_DIR / "MANIFEST.csv"
LAYER = ("ippkh_eksplorasi", "ippkh_operasi", "overlay_hutan")

# Ambang "IPPKH ini benar milik konsesi ini" — irisan harus berarti, bukan tepi bersinggungan.
AMBANG_HA = 1.0
AMBANG_PANGSA = 0.01

# Sama dengan scripts/_geo_common.DEG_LAT_METERS (tak diimpor langsung karena modul itu
# menarik rasterio). Diperiksa saat runtime bila modulnya bisa dimuat.
DEG_LAT_METERS = 111_320.0

SUMBER = ("Geoportal ESDM gis1 — Izin_Pinjam_Pakai_Kawasan_Hutan (layer 0 eksplorasi + 1 operasi) "
          "dan Overlay_WIUP_vs_Kawasan_Hutan (layer 0); data/geoportal/*.geojson + MANIFEST.csv "
          "(unduhan scripts/fetch_geoportal_hutan.py); geometri konsesi dari tabel konsesi DB ini")


def _muat_normalize_name():
    """Impor `normalize_name` dari scripts/match_harder.py — jangan bikin penormal kedua."""
    p = AKAR / "scripts/match_harder.py"
    wajib_ada(p, keterangan="penormal nama badan usaha")
    spec = importlib.util.spec_from_file_location("match_harder_arsip", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.normalize_name


def _cek_deg_lat():
    p = AKAR / "scripts/_geo_common.py"
    if not p.exists():
        return
    for baris in p.read_text(encoding="utf-8").splitlines():
        if baris.startswith("DEG_LAT_METERS"):
            nilai = float(baris.split("=")[1].split("#")[0].strip().replace("_", ""))
            if nilai != DEG_LAT_METERS:
                gagal(f"DEG_LAT_METERS berbeda dari scripts/_geo_common.py ({nilai} vs {DEG_LAT_METERS})")


def luas_ha(geom) -> float:
    """Luas poligon lon/lat → hektar, koreksi cos(lintang pusat)."""
    if geom.is_empty:
        return 0.0
    lat = geom.centroid.y
    return geom.area * DEG_LAT_METERS * (DEG_LAT_METERS * math.cos(math.radians(lat))) / 10_000.0


def tanggal(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def baca_manifest() -> dict[str, dict]:
    """MANIFEST.csv wajib ada dan geojson wajib cocok MD5-nya — ini jejak audit reproduksi."""
    wajib_ada(MANIFEST, *[GEO_DIR / f"{n}.geojson" for n in LAYER], keterangan="geoportal kehutanan")
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        baris = {r["berkas"]: r for r in csv.DictReader(f)}
    for n in LAYER:
        nama = f"{n}.geojson"
        if nama not in baris:
            gagal(f"{nama} tidak tercatat di {MANIFEST}")
        h = md5(GEO_DIR / nama)
        if h != baris[nama]["md5"].strip().lower():
            gagal(f"MD5 {nama} ({h}) ≠ MANIFEST ({baris[nama]['md5']}) — unduh ulang atau perbarui MANIFEST")
    return baris


def muat(nama: str) -> list[dict]:
    return json.loads((GEO_DIR / f"{nama}.geojson").read_text(encoding="utf-8"))["features"]


# ─────────────────────────── IPPKH ────────────────────────────────────────
def baca_ippkh() -> list[dict]:
    """Gabung layer operasi + eksplorasi jadi satu daftar berbentuk sama."""
    out = []
    for layer, sumber in (("ippkh_operasi", "operasi"), ("ippkh_eksplorasi", "eksplorasi")):
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
                # layer eksplorasi tak punya kolom nama/nomor — biarkan NULL, jangan ditebak.
                "nama_ppkh": a.get("nama_ppkh"),
                "no_ppkh": a.get("no_ppkh"),
                "tgl_ppkh": tanggal(a.get("tgl_ppkh")),
                "tgl_berakhir": tanggal(a.get("tgl_berakh")),
                "luas_ppkh_sk": a.get("luas_ppkh"),
                "jenis_ppkh": a.get("jenis_ppkh") or ("Tambang (eksplorasi)" if sumber == "eksplorasi" else None),
                "status": a.get("status"),
                "luas_hitung_ha": round(luas_ha(g), 2),
                "geom": g,
            })
    return out


def cocokkan_ippkh(ippkh, wiup, normalize_name):
    """Baris `ippkh_irisan`. Spasial menentukan; cocok nama tanpa irisan disimpan utk audit."""
    pohon = STRtree([w["geom"] for w in wiup])
    nama_wiup: dict[str, list[int]] = {}
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
            except Exception:  # noqa: BLE001 — geometri bermasalah
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
        for i in kandidat_nama:
            if not any(r[0] == wiup[i]["kode_wiup"] and r[1] == p["id"] for r in irisan):
                irisan.append((
                    wiup[i]["kode_wiup"], p["id"], p["layer"], p["nama_ppkh"], p["no_ppkh"],
                    p["tgl_ppkh"], p["tgl_berakhir"], p["jenis_ppkh"], p["status"],
                    p["luas_ppkh_sk"], p["luas_hitung_ha"], 0.0, 0.0, 1))
    return irisan


def ringkas_ippkh(irisan, wiup):
    """Satu baris `ippkh` per konsesi — termasuk yang TANPA IPPKH (nilai nol).
    Urutan kolom = SKEMA §5."""
    per: dict[str, list] = {}
    for r in irisan:
        per.setdefault(r[0], []).append(r)
    out = []
    for w in wiup:
        rs = per.get(w["kode_wiup"], [])
        sp = [r for r in rs if r[11] > 0]                      # ada irisan spasial
        tambang = [r for r in sp if (r[7] or "").startswith("Tambang")]
        tgl = sorted(r[5] for r in sp if r[5])
        luas_irisan = round(sum(r[11] for r in sp), 2)
        out.append((
            w["kode_wiup"], 1 if sp else 0, 1 if tambang else 0, len(sp), len(tambang),
            luas_irisan, round(sum(r[9] or 0 for r in sp), 2),
            round(sum(r[11] for r in sp) / w["luas_sk"], 4) if w["luas_sk"] else None,
            tgl[0] if tgl else None, tgl[-1] if tgl else None,
            1 if any(r[13] for r in rs) else 0,
        ))
    return out


# ──────────────────── Kawasan hutan (overlay) ─────────────────────────────
def bangun_kawasan(wiup_kode: set[str]):
    """Baris `kawasan_hutan`: luas tiap fungsi kawasan di dalam tiap konsesi (via kode_wiup layer)."""
    per: dict[tuple[str, int, str], float] = {}
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
    if asing:
        print(f"  {len(asing)} kode_wiup di layer overlay tidak ada di himpunan konsesi ini — dilewati.")
    # Kunci SKEMA = (kode_wiup, fungsi_nama). Bila satu nama fungsi punya >1 kode di konsesi yang
    # sama (belum pernah terjadi: 0 kasus per 31 Agu 2026), luasnya dijumlah dan kodenya digabung '+'.
    gabung: dict[tuple[str, str], tuple[list[str], float]] = {}
    for (k, fk, desk), ha in sorted(per.items()):
        kode_list, tot = gabung.get((k, desk), ([], 0.0))
        kode_list.append(str(fk))
        gabung[(k, desk)] = (kode_list, tot + ha)
    n_gabung = sum(1 for v in gabung.values() if len(v[0]) > 1)
    if n_gabung:
        print(f"  PERHATIAN: {n_gabung} pasangan (konsesi, fungsi) punya >1 fungsi_kode — digabung.")
    return [(k, "+".join(kl), desk, round(ha, 2)) for (k, desk), (kl, ha) in sorted(gabung.items())]


# ─────────────────────────── Tulis ────────────────────────────────────────
DDL = """
DROP TABLE IF EXISTS ippkh_irisan;
DROP TABLE IF EXISTS ippkh;
DROP TABLE IF EXISTS kawasan_hutan;
CREATE TABLE kawasan_hutan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), fungsi_kode TEXT, fungsi_nama TEXT NOT NULL,
  luas_ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, fungsi_nama));
CREATE TABLE ippkh (
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  punya_ippkh INTEGER NOT NULL, punya_ippkh_tambang INTEGER NOT NULL,
  n_ippkh INTEGER NOT NULL, n_ippkh_tambang INTEGER NOT NULL,
  luas_irisan_ha REAL, luas_ippkh_sk_ha REAL, rasio_ippkh_thd_luas_sk REAL,
  tgl_ippkh_awal TEXT, tgl_ippkh_akhir TEXT, cocok_nama INTEGER);
CREATE TABLE ippkh_irisan (
  kode_wiup TEXT NOT NULL, id_ippkh TEXT NOT NULL, layer TEXT, nama_ppkh TEXT, no_ppkh TEXT,
  tgl_ppkh TEXT, tgl_berakhir TEXT, jenis_ppkh TEXT, status TEXT,
  luas_ppkh_sk_ha REAL, luas_ppkh_hitung_ha REAL, luas_irisan_ha REAL, pangsa_ippkh_di_konsesi REAL,
  cocok_nama INTEGER,
  PRIMARY KEY (kode_wiup, id_ippkh));
"""

RUMUS_LUAS = "luas poligon (derajat²) × DEG_LAT_METERS² × cos(lintang pusat) / 10.000"


def tulis_meta_semua(con) -> None:
    lis = LISENSI["geoportal"]
    tulis_meta(con, "kawasan_hutan",
               deskripsi="Luas tiap FUNGSI KAWASAN HUTAN (APL/HP/HPT/HPK/HL/Kawasan Konservasi/Tubuh Air/…) "
                         "di dalam tiap konsesi, dari layer overlay Geoportal ESDM yang sudah membawa "
                         "kode_wiup (tanpa pencocokan spasial). Dipakai: tab 'IPPKH & kawasan hutan' halaman "
                         "guna lahan; menjawab 'konsesi ini berdiri di kawasan apa'. KAVEAT: irisan dengan "
                         "Hutan Lindung/Kawasan Konservasi BUKAN bukti penambangan di sana.",
               sumber=SUMBER,
               metode="Fitur overlay disaring kode_wiup ∈ konsesi; poligon tak valid di-buffer(0); luas per "
                      f"(kode_wiup, fungsikws, deskripsi) dijumlah ({RUMUS_LUAS}) lalu dibulatkan 2 desimal. "
                      "Reproduksi: unduh ulang scripts/fetch_geoportal_hutan.py (MD5 di MANIFEST.csv), lalu "
                      "jalankan skrip ini.",
               skrip=SKRIP, lisensi=lis, kolom=[
                   ("kode_wiup", "Kode WIUP konsesi (kunci ke tabel konsesi).", "-", SUMBER),
                   ("fungsi_kode", "Kode fungsi kawasan menurut layer overlay (fungsikws; mis. 100100 = Hutan "
                    "Lindung). Teks; bila satu nama fungsi punya >1 kode di konsesi yang sama, kode digabung '+'.",
                    "overlay_hutan.fungsikws", SUMBER),
                   ("fungsi_nama", "Nama fungsi kawasan (deskripsi resmi layer; '-' bila kosong).",
                    "overlay_hutan.deskripsi", SUMBER),
                   ("luas_ha", "Luas irisan konsesi × fungsi kawasan ini (ha).", RUMUS_LUAS, SUMBER),
               ])
    tulis_meta(con, "ippkh",
               deskripsi="Satu baris per konsesi (termasuk yang tanpa IPPKH): apakah memegang IPPKH menurut "
                         "irisan spasial, berapa banyak, luasnya, tanggal SK IPPKH terawal/terakhir, dan "
                         "apakah nama pemegangnya cocok nama badan usaha. Dipakai: panel detail konsesi, "
                         "sinyal F keyakinan pra-izin (tgl_ippkh_awal), tab IPPKH halaman guna lahan. "
                         "KAVEAT: layer sumber hanya memuat IPPKH berstatus Aktif → POTRET, bukan register "
                         "sejarah → semua angka BATAS BAWAH; IPK tidak tercakup.",
               sumber=SUMBER,
               metode=f"Irisan poligon IPPKH × poligon konsesi (shapely STRtree); dihitung 'punya' bila irisan "
                      f">= {AMBANG_HA} ha ATAU >= {AMBANG_PANGSA:.0%} luas IPPKH. Nama hanya pemeriksa "
                      "(scripts/match_harder.normalize_name). Agregat dari ippkh_irisan baris beririsan > 0.",
               skrip=SKRIP, lisensi=lis, kolom=[
                   ("kode_wiup", "Kode WIUP konsesi.", "-", SUMBER),
                   ("punya_ippkh", "1 bila ada >= 1 IPPKH yang beririsan spasial dengan konsesi.",
                    "COUNT(ippkh_irisan.luas_irisan_ha > 0) > 0", SUMBER),
                   ("punya_ippkh_tambang", "1 bila ada IPPKH berjenis 'Tambang…' yang beririsan.",
                    "jenis_ppkh LIKE 'Tambang%' AND luas_irisan_ha > 0", SUMBER),
                   ("n_ippkh", "Banyak poligon IPPKH yang beririsan spasial.", "COUNT(*) irisan > 0", SUMBER),
                   ("n_ippkh_tambang", "Banyak IPPKH jenis tambang yang beririsan.", "COUNT(*) tambang", SUMBER),
                   ("luas_irisan_ha", "Total luas IPPKH yang jatuh di dalam konsesi (ha).",
                    "SUM(ippkh_irisan.luas_irisan_ha)", SUMBER),
                   ("luas_ippkh_sk_ha", "Total luas menurut SK IPPKH yang beririsan (ha, seluruh poligon, "
                    "bukan hanya bagian di dalam konsesi).", "SUM(luas_ppkh_sk_ha)", SUMBER),
                   ("rasio_ippkh_thd_luas_sk", "Luas irisan IPPKH dibagi luas SK konsesi (NULL bila luas SK "
                    "kosong/0).", "luas_irisan_ha / konsesi.luas_sk_ha", SUMBER),
                   ("tgl_ippkh_awal", "Tanggal SK IPPKH TERAWAL yang beririsan (YYYY-MM-DD) — dipakai "
                    "sinyal F keyakinan pra-izin.", "MIN(tgl_ppkh)", SUMBER),
                   ("tgl_ippkh_akhir", "Tanggal SK IPPKH terakhir yang beririsan.", "MAX(tgl_ppkh)", SUMBER),
                   ("cocok_nama", "1 bila ada IPPKH (beririsan atau tidak) yang nama pemegangnya cocok "
                    "nama badan usaha konsesi. Pemeriksa, bukan penentu.",
                    "ANY(ippkh_irisan.cocok_nama)", SUMBER),
               ])
    tulis_meta(con, "ippkh_irisan",
               deskripsi="Setiap pasangan (konsesi, poligon IPPKH) yang cocok — jalur spasial dan/atau nama. "
                         "Baris beririsan-nol tapi cocok nama sengaja disimpan agar selisih kedua jalur bisa "
                         "diaudit. Dipakai: telusur balik tabel ippkh; audit ejaan nama antar registri.",
               sumber=SUMBER,
               metode=f"Irisan poligon (shapely STRtree); ambang >= {AMBANG_HA} ha atau >= {AMBANG_PANGSA:.0%} "
                      "luas IPPKH; nama dinormalkan match_harder.normalize_name lalu dibandingkan persis.",
               skrip=SKRIP, lisensi=lis, kolom=[
                   ("kode_wiup", "Kode WIUP konsesi.", "-", SUMBER),
                   ("id_ippkh", "Id poligon IPPKH: '<layer>:<objectid_1>'.", "-", SUMBER),
                   ("layer", "Asal layer: 'operasi' atau 'eksplorasi' (eksplorasi tanpa kolom nama → hanya "
                    "cocok spasial).", "-", SUMBER),
                   ("nama_ppkh", "Nama pemegang IPPKH (NULL di layer eksplorasi).", "-", SUMBER),
                   ("no_ppkh", "Nomor SK IPPKH.", "-", SUMBER),
                   ("tgl_ppkh", "Tanggal SK IPPKH (YYYY-MM-DD).", "epoch ms → tanggal UTC", SUMBER),
                   ("tgl_berakhir", "Tanggal IPPKH berakhir.", "epoch ms → tanggal UTC", SUMBER),
                   ("jenis_ppkh", "Jenis IPPKH: Tambang / Non Tambang / 'Tambang (eksplorasi)' utk layer "
                    "eksplorasi.", "-", SUMBER),
                   ("status", "Status IPPKH di sumber — SELALU 'Aktif' (itulah sebab angkanya batas bawah).",
                    "-", SUMBER),
                   ("luas_ppkh_sk_ha", "Luas menurut SK IPPKH (ha).", "-", SUMBER),
                   ("luas_ppkh_hitung_ha", "Luas poligon IPPKH hasil hitung sendiri — pembanding luas SK.",
                    RUMUS_LUAS, SUMBER),
                   ("luas_irisan_ha", "Luas bagian IPPKH yang jatuh DI DALAM konsesi (ha). 0 = hanya cocok "
                    "nama, bukan spasial.", RUMUS_LUAS + " atas poligon irisan", SUMBER),
                   ("pangsa_ippkh_di_konsesi", "Bagian poligon IPPKH yang berada di dalam konsesi (0..1).",
                    "luas_irisan_ha / luas_ppkh_hitung_ha", SUMBER),
                   ("cocok_nama", "1 bila nama pemegang cocok nama badan usaha konsesi setelah dinormalkan.",
                    "normalize_name(nama_ppkh) == normalize_name(nama_usaha)", SUMBER),
               ])


def main(argv=None) -> int:
    ap = argparser(__doc__)
    a = ap.parse_args(argv)
    t0 = time.time()
    _cek_deg_lat()
    normalize_name = _muat_normalize_name()
    manifest = baca_manifest()

    con = buka(a.db)
    wajib_tabel(con, "konsesi")
    wiup = []
    for kode, nama, luas_sk, geo in con.execute(
            "SELECT kode_wiup, nama_usaha, luas_sk_ha, geometri_geojson FROM konsesi ORDER BY kode_wiup"):
        g = shape(json.loads(geo))
        if not g.is_valid:
            g = g.buffer(0)
        wiup.append({"kode_wiup": kode, "nama_usaha": nama, "luas_sk": luas_sk, "geom": g})
    if not wiup:
        gagal("tabel konsesi kosong")
    print(f"{len(wiup)} konsesi bergeometri")

    ippkh = baca_ippkh()
    print(f"{len(ippkh)} poligon IPPKH ({sum(1 for p in ippkh if p['layer'] == 'operasi')} operasi, "
          f"{sum(1 for p in ippkh if p['layer'] == 'eksplorasi')} eksplorasi)")
    irisan = cocokkan_ippkh(ippkh, wiup, normalize_name)
    ringkas = ringkas_ippkh(irisan, wiup)
    n_punya, n_tambang = sum(r[1] for r in ringkas), sum(r[2] for r in ringkas)
    print(f"{len(irisan)} pasangan (konsesi, IPPKH); {n_punya} konsesi ber-IPPKH, {n_tambang} ber-IPPKH tambang")
    kawasan = bangun_kawasan({w["kode_wiup"] for w in wiup})
    print(f"{len(kawasan)} baris fungsi kawasan")

    con.executescript(DDL)
    con.executemany("INSERT INTO ippkh_irisan VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", irisan)
    con.executemany("INSERT INTO ippkh (kode_wiup, punya_ippkh, punya_ippkh_tambang, n_ippkh, n_ippkh_tambang, "
                    "luas_irisan_ha, luas_ippkh_sk_ha, rasio_ippkh_thd_luas_sk, tgl_ippkh_awal, tgl_ippkh_akhir, "
                    "cocok_nama) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ringkas)
    con.executemany("INSERT INTO kawasan_hutan VALUES (?,?,?,?)", kawasan)
    tulis_sumber(con, "geoportal_hutan", "Geoportal ESDM — IPPKH & Overlay WIUP vs Kawasan Hutan",
                 LISENSI["geoportal"], versi="gis1 MapServer (Izin_Pinjam_Pakai_Kawasan_Hutan 0+1; "
                 "Overlay_WIUP_vs_Kawasan_Hutan 0)",
                 url="https://geoportal.esdm.go.id/gis1/rest/services/",
                 tanggal_akses=max(r["tanggal_unduh"] for r in manifest.values()),
                 cakupan_tahun="potret izin aktif saat unduh (IPPKH bertanggal s/d 2023)",
                 sitasi="Kementerian ESDM, Geoportal ESDM (gis1), layer Izin Pinjam Pakai Kawasan Hutan dan "
                        "Overlay WIUP vs Kawasan Hutan, diakses " + max(r["tanggal_unduh"] for r in manifest.values()),
                 catatan="MD5 & jumlah fitur di data/geoportal/MANIFEST.csv: " + "; ".join(
                     f"{n}={manifest[n]['md5']} ({manifest[n]['n_fitur']} fitur)" for n in sorted(manifest)))
    tulis_meta_semua(con)
    tandai_selesai(con, "06_kawasan_hutan", himpunan=a.himpunan, n_punya_ippkh=n_punya,
                   n_punya_ippkh_tambang=n_tambang, n_kawasan_hutan=len(kawasan), n_ippkh_irisan=len(irisan))
    con.close()
    print(f"Selesai {SKRIP} ({time.time() - t0:.1f} s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
