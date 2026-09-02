"""Penghitung keluaran non-DB (W4) — dipakai BERSAMA oleh 09_sajikan.py (menulis) dan
10_verifikasi.py (menghitung ulang lalu membandingkan), supaya "angka web = angka DB" bukan
sekadar janji: kedua skrip memanggil fungsi yang sama.

Semua angka dibaca dari DB; tidak ada yang ditulis tangan. Pembulatan mengikuti
scripts/gen_dashboard_stats.py lama: ha hero → 0 desimal, persen → 1 desimal.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .w4_skema import TAHUN_AKHIR, TAHUN_AWAL

JENDELA = f"{TAHUN_AWAL}-{TAHUN_AKHIR}"
LABEL_SANKEY = JENDELA
BUKAN_PROPERTI = {"geometri_geojson", "bbox_min_lon", "bbox_min_lat", "bbox_max_lon", "bbox_max_lat"}

# Tabel hulu yang wajib ada sebelum stats/geojson bisa dihitung (gagal keras bila absen).
TABEL_HULU_MINERBA = ["konsesi", "konsesi_registri", "hansen_ringkas", "hansen_tahunan", "izin_laju",
                      "izin_klasifikasi", "ippkh", "keyakinan_pra_izin", "keyakinan_ringkas",
                      "umur_izin_kurun", "transisi_konsesi"]
TABEL_HULU_LENGKAP = ["konsesi", "hansen_ringkas"]


def _satu(con: sqlite3.Connection, sql: str, params=()) -> Any:
    r = con.execute(sql, params).fetchone()
    return r[0] if r else None


def _angka(nilai: Any) -> Any:
    """'1189114.10' → 1189114.1 ; '2000' → 2000 ; teks lain (mis. '2001-2024') tetap teks."""
    if nilai is None or isinstance(nilai, (int, float)):
        return nilai
    s = str(nilai).strip()
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def hero(con: sqlite3.Connection) -> dict:
    """n konsesi, Σ hutan 2000, Σ hilang 2001–2024, persen — penyebut & pembilang Σ per konsesi."""
    n = _satu(con, "SELECT COUNT(*) FROM konsesi")
    hutan, hilang = con.execute(
        "SELECT COALESCE(SUM(hutan_2000_ha),0), COALESCE(SUM(hilang_2001_2024_ha),0) FROM hansen_ringkas").fetchone()
    return {
        "n_konsesi": n,
        "hutan_2000_ha": round(hutan),
        "hilang_2001_2024_ha": round(hilang),
        "pct_hutan_2000": round(100.0 * hilang / hutan, 1) if hutan else 0.0,
    }


def blok_minerba(con: sqlite3.Connection) -> dict:
    d = hero(con)
    d["n_provinsi"] = _satu(con, "SELECT COUNT(DISTINCT provinsi) FROM konsesi WHERE provinsi IS NOT NULL")
    d["n_kabupaten"] = _satu(con, "SELECT COUNT(DISTINCT kabupaten_norm) FROM konsesi WHERE kabupaten_norm IS NOT NULL")
    # Bentuk {komoditas: {n, hilang_ha}} — sama dengan `per_komoditas` di API /api/stats (CLAUDE/API-v3.md)
    # dan dengan tipe HimpunanStat.komoditas di webapp/src/lib/stats.ts, supaya satu bentuk saja yang dipakai.
    d["komoditas"] = {k: {"n": n, "hilang_ha": round(ha or 0.0, 2)} for k, n, ha in con.execute(
        "SELECT k.komoditas, COUNT(*), COALESCE(SUM(h.hilang_2001_2024_ha),0) FROM konsesi k "
        "LEFT JOIN hansen_ringkas h USING (kode_wiup) GROUP BY k.komoditas ORDER BY COUNT(*) DESC, k.komoditas")}
    return d


def blok_lengkap(con: sqlite3.Connection) -> dict:
    return hero(con)


def blok_keyakinan(con: sqlite3.Connection) -> dict:
    return {k: _angka(v) for k, v in con.execute("SELECT kunci, nilai FROM keyakinan_ringkas ORDER BY kunci")}


def blok_umur(con: sqlite3.Connection) -> dict:
    out: dict[str, list] = {}
    for ranc, kurun, n, laju in con.execute(
            "SELECT rancangan, kurun, n_konsesi, laju_bahaya_per_tahun FROM umur_izin_kurun "
            "ORDER BY rancangan, umur_awal"):
        out.setdefault(ranc, []).append({"kurun": kurun, "n_konsesi": n, "laju_bahaya_per_tahun": laju})
    return out


def blok_ippkh(con: sqlite3.Connection) -> dict:
    a, b = con.execute("SELECT COALESCE(SUM(punya_ippkh),0), COALESCE(SUM(punya_ippkh_tambang),0) FROM ippkh").fetchone()
    return {"n_punya": a, "n_tambang": b}


def blok_sankey(con: sqlite3.Connection) -> dict:
    ha, n = con.execute(
        "SELECT COALESCE(SUM(CASE WHEN kelas_awal <> kelas_akhir THEN ha END),0), COUNT(DISTINCT kode_wiup) "
        "FROM transisi_konsesi WHERE label = ?", (LABEL_SANKEY,)).fetchone()
    return {"total_berubah_ha": round(ha), "n_konsesi": n}


def blok_registri(con: sqlite3.Connection) -> dict:
    n = _satu(con, "SELECT COALESCE(SUM(cocok),0) FROM konsesi_registri")
    per = {(s or "tanpa_cocok"): k for s, k in con.execute(
        "SELECT strategi_cocok, COUNT(*) FROM konsesi_registri GROUP BY strategi_cocok ORDER BY strategi_cocok")}
    return {"n_cocok": n, "per_strategi": per}


def stats_minerba(con: sqlite3.Connection) -> dict:
    """Seluruh blok yang bersumber dari DB himpunan minerba (tanpa generated_at/jendela/lengkap)."""
    return {
        "minerba": blok_minerba(con),
        "keyakinan": blok_keyakinan(con),
        "umur": blok_umur(con),
        "ippkh": blok_ippkh(con),
        f"sankey_{TAHUN_AWAL}_{TAHUN_AKHIR}": blok_sankey(con),
        "registri": blok_registri(con),
    }


# ── GeoJSON QGIS ─────────────────────────────────────────────────────────────────────────────

def fitur_geojson(con: sqlite3.Connection) -> list[dict]:
    """Satu Feature per konsesi: properti = kolom v_konsesi (tanpa geometri/bbox) + hilang_YYYY_ha."""
    con.row_factory = sqlite3.Row
    tahunan: dict[str, dict[int, float]] = {}
    for kode, th, ha in con.execute("SELECT kode_wiup, tahun, hilang_ha FROM hansen_tahunan"):
        tahunan.setdefault(kode, {})[th] = ha or 0.0
    feats = []
    for r in con.execute("SELECT * FROM v_konsesi ORDER BY kode_wiup"):
        d = dict(r)
        p = {k: v for k, v in d.items() if k not in BUKAN_PROPERTI}
        yl = tahunan.get(d["kode_wiup"], {})
        for th in range(TAHUN_AWAL, TAHUN_AKHIR + 1):
            p[f"hilang_{th}_ha"] = round(yl.get(th, 0.0), 2)
        feats.append({"type": "Feature", "geometry": json.loads(d["geometri_geojson"]), "properties": p})
    con.row_factory = None
    return feats
