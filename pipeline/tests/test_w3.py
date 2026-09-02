"""Uji W3 — 06_kawasan_hutan, 07_umur_izin, 08_keyakinan: jalan di DB uji berskema baru
(salinan arsip lewat lib/uji_arsip) lalu paritas terhadap DB arsip data/kalimantan.db.

    .venv/bin/python -m pytest pipeline/tests/test_w3.py -v

Skip otomatis bila DB arsip / mapbiomas.db / geojson geoportal tidak ada.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.lib.db import AKAR  # noqa: E402
from pipeline.lib.uji_arsip import ARSIP_K, ARSIP_M, _arsip, siapkan_db_uji  # noqa: E402

PY = sys.executable
DB_UJI = AKAR / "data/.bangun/w3-minerba.db"
TABEL_HULU = ["konsesi", "konsesi_registri", "hansen_ringkas", "hansen_tahunan", "izin_laju",
              "izin_klasifikasi", "mapbiomas_kelas", "mapbiomas_tahunan"]
GEOPORTAL = [AKAR / f"data/geoportal/{n}" for n in
             ("MANIFEST.csv", "ippkh_eksplorasi.geojson", "ippkh_operasi.geojson", "overlay_hutan.geojson")]


def _arsip_m() -> Path:
    return ARSIP_M if ARSIP_M.exists() else AKAR / "data/arsip" / ARSIP_M.name


def _jalankan(skrip: str, db: Path) -> None:
    r = subprocess.run([PY, str(AKAR / "pipeline" / skrip), "--db", str(db), "--himpunan", "minerba"],
                       capture_output=True, text=True, cwd=AKAR)
    assert r.returncode == 0, f"{skrip} gagal (kode {r.returncode}):\n{r.stdout}\n{r.stderr}"


@pytest.fixture(scope="module")
def db() -> sqlite3.Connection:
    if not _arsip(None).exists() or not _arsip_m().exists():
        pytest.skip("DB arsip (kalimantan.db / mapbiomas.db) tidak ada")
    if any(not p.exists() for p in GEOPORTAL):
        pytest.skip("geojson geoportal / MANIFEST.csv tidak ada")
    con = siapkan_db_uji(DB_UJI, TABEL_HULU)
    # DB uji hasil CREATE TABLE AS tidak punya PRIMARY KEY; REFERENCES konsesi(kode_wiup)
    # di tabel W3 butuh indeks unik pada induknya (di DB produksi ini PK dari 01_identitas).
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_konsesi_kode ON konsesi(kode_wiup)")
    con.commit()
    con.close()
    for s in ("06_kawasan_hutan.py", "07_umur_izin.py", "08_keyakinan.py"):
        _jalankan(s, DB_UJI)
    con = sqlite3.connect(str(DB_UJI))
    con.execute("ATTACH DATABASE ? AS a", (f"file:{_arsip(None)}?mode=ro",))
    yield con
    con.close()


def _q(con, sql, *p):
    return con.execute(sql, p).fetchall()


# ───────────────────────────── 06 kawasan hutan & IPPKH ─────────────────────────────
def test_ippkh_jumlah(db):
    assert _q(db, "SELECT SUM(punya_ippkh), SUM(punya_ippkh_tambang), COUNT(*) FROM ippkh")[0] == (274, 256, 825)


def test_kawasan_hutan_jangkar(db):
    baris = dict((n, (round(l), k)) for n, l, k in _q(
        db, "SELECT fungsi_nama, SUM(luas_ha), COUNT(DISTINCT kode_wiup) FROM kawasan_hutan GROUP BY 1"))
    assert baris["Hutan Lindung"] == (112056, 49)
    assert baris["Kawasan Konservasi"] == (27423, 33)
    assert _q(db, "SELECT COUNT(*) FROM kawasan_hutan")[0][0] == 1623
    assert _q(db, "SELECT COUNT(*) FROM ippkh_irisan")[0][0] == 568


def test_kawasan_hutan_identik_arsip(db):
    baru = _q(db, "SELECT kode_wiup, fungsi_kode, fungsi_nama, luas_ha FROM kawasan_hutan ORDER BY 1,3")
    lama = _q(db, "SELECT kode_wiup, CAST(fungsi_kode AS TEXT), fungsi_nama, luas_ha "
                  "FROM a.konsesi_kawasan_hutan ORDER BY 1,3")
    assert baru == lama


def test_ippkh_identik_arsip(db):
    kol = ("kode_wiup, punya_ippkh, punya_ippkh_tambang, n_ippkh, n_ippkh_tambang, luas_irisan_ha, "
           "luas_ippkh_sk_ha, rasio_ippkh_thd_luas_sk, tgl_ippkh_awal, tgl_ippkh_akhir, cocok_nama")
    assert _q(db, f"SELECT {kol} FROM ippkh ORDER BY 1") == _q(db, f"SELECT {kol} FROM a.konsesi_ippkh ORDER BY 1")
    assert _q(db, "SELECT * FROM ippkh_irisan ORDER BY 1,2") == _q(db, "SELECT * FROM a.ippkh_konsesi_irisan ORDER BY 1,2")


# ───────────────────────────────── 07 umur izin ─────────────────────────────────────
def test_umur_a_seimbang(db):
    r = _q(db, "SELECT kurun, n_konsesi, laju_bahaya_per_tahun FROM umur_izin_kurun "
               "WHERE rancangan='A_seimbang' ORDER BY umur_awal")
    assert [x[0] for x in r] == ["-8..-1", "0..4", "5..9"]
    assert all(x[1] == 260 for x in r)
    # laju tersimpan sebagai pecahan; ×100 = %/th
    for (_, _, laju), harap in zip(r, (1.782, 2.392, 2.494)):
        assert abs(laju * 100 - harap) < 0.001


def _bandingkan(db, tabel_baru, tabel_lama, kunci, tol=0.01):
    kb = [r[1] for r in db.execute(f"PRAGMA table_info('{tabel_baru}')")]
    kl = [k.replace("hilang_ha", "loss_ha") for k in kb]
    baru = _q(db, f"SELECT {', '.join(kb)} FROM {tabel_baru} ORDER BY {kunci}")
    lama = _q(db, f"SELECT {', '.join(kl)} FROM a.{tabel_lama} ORDER BY {kunci}")
    assert len(baru) == len(lama)
    for x, y in zip(baru, lama):
        for u, v in zip(x, y):
            if isinstance(u, float) and isinstance(v, float):
                assert abs(u - v) <= tol, (tabel_baru, x, y)
            else:
                assert u == v, (tabel_baru, x, y)


def test_umur_identik_arsip(db):
    _bandingkan(db, "umur_izin_kurun", "umur_izin_kurun", "rancangan, kurun")
    _bandingkan(db, "umur_izin_tahunan", "umur_izin_tahunan", "rancangan, umur_relatif")
    _bandingkan(db, "umur_izin_konsesi", "umur_izin_konsesi", "rancangan, kode_wiup, kurun")
    assert _q(db, "SELECT COUNT(*) FROM umur_izin_tahunan WHERE n_konsesi <= 0")[0][0] == 0


# ─────────────────────────────── 08 keyakinan pra-izin ──────────────────────────────
def _ringkas(db, skema=""):
    return {k: v for k, v in _q(db, f"SELECT kunci, nilai FROM {skema}keyakinan_ringkas")}


def test_keyakinan_ringkas_jangkar(db):
    r = _ringkas(db)
    assert abs(float(r["hilang_harapan_ha"]) - 1189114.10) < 0.5
    assert abs(float(r["hilang_batas_bawah_ha"]) - 547714.03) < 0.5
    assert abs(float(r["hilang_batas_atas_ha"]) - 1546928.38) < 0.5
    lo, hi = float(r["hilang_bootstrap_lo_ha"]), float(r["hilang_bootstrap_hi_ha"])
    # Selang bootstrap deterministik (benih 20260831) dengan konsesi TERURUT kode_wiup.
    # Arsip (1.144.798,52 / 1.250.548,75) memakai urutan rowid wiup_geoportal yang tidak
    # terurut; dengan urutan itu skrip ini menghasilkan angka arsip persis (diverifikasi
    # 2 Sep 2026). Selisihnya murni permutasi tarikan Monte Carlo, bukan metode.
    assert abs(lo - 1144662.06) < 0.5 and abs(hi - 1252064.79) < 0.5
    assert abs(lo - 1144798.52) < 2000 and abs(hi - 1250548.75) < 2000
    assert float(r["hilang_batas_bawah_ha"]) <= lo <= float(r["hilang_harapan_ha"]) <= hi <= float(r["hilang_batas_atas_ha"])
    assert r["n_bootstrap"] == "2000" and r["benih"] == "20260831" and r["jendela"] == "2001-2024"


def test_keyakinan_model_auc(db):
    auc = dict(_q(db, "SELECT DISTINCT model, auc FROM keyakinan_model"))
    assert abs(auc["R"] - 0.683) < 0.001 and abs(auc["RS"] - 0.736) < 0.001
    baru = _q(db, "SELECT model, penebak, koefisien, rasio_odds, auc FROM keyakinan_model ORDER BY 1,2")
    lama = _q(db, "SELECT model, penebak, koefisien, rasio_odds, auc FROM a.keyakinan_model ORDER BY 1,2")
    assert len(baru) == len(lama) == 11
    for x, y in zip(baru, lama):
        assert x[:2] == y[:2] and all(abs(u - v) < 1e-9 for u, v in zip(x[2:], y[2:]))


def test_keyakinan_pra_izin_identik_arsip(db):
    assert _q(db, "SELECT COUNT(*) FROM keyakinan_pra_izin")[0][0] == 818
    baru = _q(db, "SELECT kode_wiup, peluang_akhir, hilang_harapan_ha, e_lubang_pra_izin, f_ippkh_lebih_tua, "
                  "g_registri_beda, durasi_sk FROM keyakinan_pra_izin ORDER BY 1")
    lama = _q(db, "SELECT kode_wiup, peluang_akhir, loss_harapan_ha, e_lubang_pra_izin, f_ippkh_lebih_tua, "
                  "g_registri_beda, durasi_sk FROM a.keyakinan_pra_izin ORDER BY 1")
    assert len(baru) == len(lama)
    for x, y in zip(baru, lama):
        assert x[0] == y[0] and abs(x[1] - y[1]) < 1e-6 and abs(x[2] - y[2]) < 0.01 and x[3:] == y[3:], (x, y)


# ───────────────────────────────── meta & bangun ────────────────────────────────────
def test_meta_dan_bangun(db):
    from pipeline.lib.meta import cakupan_dua_arah
    milik = {"kawasan_hutan", "ippkh", "ippkh_irisan", "umur_izin_kurun", "umur_izin_tahunan",
             "umur_izin_konsesi", "keyakinan_pra_izin", "keyakinan_model", "keyakinan_ringkas"}
    masalah = [m for m in cakupan_dua_arah(db) if m.split(":")[0] in milik]
    assert masalah == [], masalah
    lisensi = dict(_q(db, "SELECT nama_tabel, lisensi FROM analysis_meta"))
    assert "Geoportal" in lisensi["ippkh"] and "Hansen" in lisensi["umur_izin_kurun"]
    assert "CC BY-SA" in lisensi["keyakinan_pra_izin"]
    selesai = {k for (k,) in _q(db, "SELECT kunci FROM bangun WHERE kunci LIKE '%.selesai'")}
    assert {"06_kawasan_hutan.selesai", "07_umur_izin.selesai", "08_keyakinan.selesai"} <= selesai
    assert _q(db, "SELECT COUNT(*) FROM sumber WHERE id='geoportal_hutan'")[0][0] == 1
