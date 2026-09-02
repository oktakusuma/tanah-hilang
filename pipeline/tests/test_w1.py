"""Uji workstream W1 (01_identitas, 02_hansen, 03_izin) — skema, jumlah baris, meta dua arah,
identitas Σ hansen_tahunan, dan PARITAS terhadap DB arsip.

    .venv/bin/python -m pytest pipeline/tests/test_w1.py -q

Fixture membangun ulang data/.bangun/w1-minerba.db & w1-lengkap.db dari nol (±35 s). Set
W1_TANPA_BANGUN=1 untuk memakai DB yang sudah ada. Uji paritas dilewati bila DB arsip tak ada.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.lib.db import AKAR, hash_geometri  # noqa: E402
from pipeline.lib.meta import cakupan_dua_arah  # noqa: E402

PY = sys.executable
BANGUN = AKAR / "data/.bangun"
SKEMA = (AKAR / "pipeline/SKEMA.md").read_text(encoding="utf-8")
TABEL_W1 = ("konsesi", "konsesi_registri", "kepadatan_penduduk", "hansen_ringkas", "hansen_tahunan",
            "izin_laju", "izin_klasifikasi")
N_KONSESI = {"minerba": 825, "lengkap": 1765}
# 4 konsesi minerba (32 lengkap) ber-tahun_izin 2026: arsip memasukkannya ke "tanpa tahun" karena
# jendela lamanya berhenti 2025; pipeline v3 mengikuti aturan SKEMA (tahun_izin > 2024 →
# izin_setelah_jendela). Jumlah gabungan kedua kategori batas tetap = arsip (59 + 17 = 76).
VONIS_INTI_MINERBA = {"accelerated_post_iup": 248, "decelerated_post_iup": 263, "stable": 222,
                      "no_loss_either": 14, "loss_only_after_iup": 2}
VONIS_BATAS_MINERBA = {"izin_setelah_jendela": 63, "tanpa_tahun_izin": 13}


def _arsip(nama: str) -> Path | None:
    for p in (AKAR / "data" / nama, AKAR / "data/arsip" / nama, AKAR / "data-full" / nama):
        if p.exists():
            return p
    return None


ARSIP = {"minerba": _arsip("kalimantan.db"),
         "lengkap": next((q for q in (AKAR / "data/arsip/kalimantan-lengkap.db",
                                      AKAR / "data-full/kalimantan.db") if q.exists()), None)}


def kolom_skema(tabel: str) -> list[str]:
    """Nama kolom tabel menurut blok CREATE TABLE di SKEMA.md (urutan dipertahankan)."""
    m = re.search(rf"CREATE TABLE {tabel} \((.*?)\n\);", SKEMA, re.S)
    assert m, f"{tabel} tak ada di SKEMA.md"
    kolom = []
    for baris in m.group(1).splitlines():
        baris = baris.split("--")[0].strip()
        if not baris or baris.upper().startswith(("PRIMARY KEY", "CHECK", "FOREIGN")):
            continue
        for bagian in baris.split(","):
            tok = bagian.strip().split()
            if tok and tok[0].upper() not in ("PRIMARY", "CHECK", "FOREIGN") and re.match(r"^[a-z_0-9]+$", tok[0]):
                kolom.append(tok[0])
    return kolom


def _bangun(himpunan: str) -> Path:
    db = BANGUN / f"w1-{himpunan}.db"
    if os.environ.get("W1_TANPA_BANGUN") and db.exists():
        return db
    BANGUN.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    for skrip in ("01_identitas.py", "02_hansen.py", "03_izin.py"):
        subprocess.run([PY, str(AKAR / "pipeline" / skrip), "--db", str(db), "--himpunan", himpunan],
                       check=True, cwd=AKAR, capture_output=True, text=True)
    return db


@pytest.fixture(scope="session", params=["minerba", "lengkap"])
def him(request):
    return request.param


@pytest.fixture(scope="session")
def db_path(him):
    return _bangun(him)


@pytest.fixture
def con(db_path):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    yield c
    c.close()


@pytest.fixture
def arsip(him, con):
    p = ARSIP.get(him)
    if p is None:
        pytest.skip(f"DB arsip {him} tidak ada")
    con.execute("ATTACH DATABASE ? AS a", (f"file:{p}?mode=ro",))
    return con


# ───────────────────────── skema, baris, meta, bangun ─────────────────────
def test_skema_kolom_sama_dengan_skema_md(con):
    for t in TABEL_W1:
        nyata = [r[1] for r in con.execute(f"PRAGMA table_xinfo('{t}')")]
        assert nyata == kolom_skema(t), t


def test_jumlah_baris(con, him):
    n = N_KONSESI[him]
    hitung = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
    assert hitung("konsesi") == n
    assert hitung("konsesi_registri") == n
    assert hitung("izin_klasifikasi") == n
    assert hitung("sumber") == 5            # mapbiomas ditulis 04, geoportal_hutan ditulis 06
    assert hitung("kepadatan_penduduk") == 560
    n_hansen = hitung("hansen_ringkas")
    assert n_hansen == (825 if him == "minerba" else 1764)   # 1 galian C tanpa baris batch CSV
    assert hitung("hansen_tahunan") == 24 * n_hansen
    assert hitung("izin_laju") == n_hansen
    assert con.execute("SELECT COUNT(DISTINCT kode_wiup) FROM hansen_tahunan").fetchone()[0] == n_hansen
    assert con.execute("SELECT MIN(tahun), MAX(tahun) FROM hansen_tahunan").fetchone() == (2001, 2024)


def test_meta_dua_arah_kosong(con):
    assert cakupan_dua_arah(con) == []
    assert con.execute("SELECT COUNT(*) FROM analysis_meta WHERE lisensi='' OR metode='' OR sumber=''").fetchone()[0] == 0
    assert {r[0] for r in con.execute("SELECT id FROM sumber")} == {
        "hansen", "geoportal_wiup", "minerbaone", "bps", "geoboundaries"}


def test_identitas_sigma_tahunan(con):
    salah = con.execute("""SELECT COUNT(*) FROM hansen_ringkas h JOIN
        (SELECT kode_wiup, SUM(hilang_ha) s FROM hansen_tahunan GROUP BY 1) t USING (kode_wiup)
        WHERE ABS(h.hilang_2001_2024_ha - t.s) > 0.01""").fetchone()[0]
    assert salah == 0
    # izin_laju pra + pasca = total bagi konsesi ber-tahun_izin di jendela
    salah = con.execute("""SELECT COUNT(*) FROM izin_laju l JOIN hansen_ringkas h USING (kode_wiup)
        WHERE l.hilang_pasca_ha IS NOT NULL AND ABS(l.hilang_pra_ha + l.hilang_pasca_ha - h.hilang_2001_2024_ha) > 0.02""").fetchone()[0]
    assert salah == 0
    assert con.execute("SELECT COUNT(*) FROM hansen_ringkas WHERE hutan_2000_ha > 0 AND "
                       "ABS(pct_hutan_2000 - 100.0*hilang_2001_2024_ha/hutan_2000_ha) > 0.01").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM hansen_ringkas WHERE (tahun_puncak IS NULL) <> (hilang_2001_2024_ha = 0)").fetchone()[0] == 0


def test_bangun_dan_hash(con, him):
    b = dict(con.execute("SELECT kunci, nilai FROM bangun"))
    assert b["himpunan"] == him
    assert int(b["konsesi.n"]) == N_KONSESI[him]
    assert b["konsesi.hash_geometri"] == hash_geometri(con)
    for k in ("01_identitas.selesai", "02_hansen.selesai", "03_izin.selesai", "pipeline_versi", "git_commit"):
        assert k in b


def test_vonis_dan_asal_tanggal_konsisten(con):
    assert con.execute("SELECT COUNT(*) FROM izin_laju WHERE vonis='tanpa_tahun_izin' AND tahun_izin >= 2001").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM izin_laju WHERE vonis='izin_setelah_jendela' AND (tahun_izin <= 2024 OR hilang_pasca_ha IS NOT NULL)").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM izin_laju WHERE vonis NOT IN ('tanpa_tahun_izin','izin_setelah_jendela') AND hilang_pasca_ha IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM konsesi WHERE (tgl_berlaku IS NULL) <> (asal_tanggal IS NULL)").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM konsesi_registri WHERE cocok <> (strategi_cocok IS NOT NULL)").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM izin_klasifikasi WHERE (kelas='TAK_DINILAI') <> (bukti IS NULL)").fetchone()[0] == 0


# ───────────────────────────── paritas arsip ──────────────────────────────
def test_paritas_hansen(arsip, him):
    c = arsip
    total, hutan = c.execute("SELECT SUM(hilang_2001_2024_ha), SUM(hutan_2000_ha) FROM hansen_ringkas").fetchone()
    a_total, a_hutan, a_n = c.execute("SELECT SUM(loss_2001_2024_ha), SUM(forest_2000_ha), COUNT(*) FROM a.wiup_loss").fetchone()
    assert abs(total - a_total) < 0.5
    assert abs(hutan - a_hutan) < 0.5
    assert c.execute("SELECT COUNT(*) FROM hansen_ringkas").fetchone()[0] == a_n
    if him == "minerba":
        assert abs(total - 1_548_812.60) < 0.5
    assert c.execute("SELECT COUNT(*) FROM konsesi").fetchone()[0] == c.execute("SELECT COUNT(*) FROM a.wiup_geoportal").fetchone()[0]
    # deret tahunan identik (arsip hanya menyimpan baris > 0)
    assert c.execute("""SELECT COUNT(*) FROM hansen_tahunan t LEFT JOIN a.wiup_loss_yearly y
        ON y.kode_wiup=t.kode_wiup AND y.year=t.tahun WHERE ABS(t.hilang_ha - COALESCE(y.loss_ha,0)) > 0.005""").fetchone()[0] == 0


def test_paritas_vonis(arsip, him):
    vonis = Counter(dict(arsip.execute("SELECT vonis, COUNT(*) FROM izin_laju GROUP BY 1")))
    a = Counter({(k or "tanpa_tahun_izin").replace("izin_setelah_jendela_2024", "izin_setelah_jendela"): n
                 for k, n in arsip.execute("SELECT verdict_jendela_2024, COUNT(*) FROM a.wiup_temporal GROUP BY 1")})
    inti = ("accelerated_post_iup", "decelerated_post_iup", "stable", "no_loss_either", "loss_only_after_iup")
    for k in inti:
        assert vonis[k] == a[k], k
    assert vonis["izin_setelah_jendela"] + vonis["tanpa_tahun_izin"] == a["izin_setelah_jendela"] + a["tanpa_tahun_izin"]
    if him == "minerba":
        assert {k: vonis[k] for k in inti} == VONIS_INTI_MINERBA
        assert {k: vonis[k] for k in VONIS_BATAS_MINERBA} == VONIS_BATAS_MINERBA
        assert a["izin_setelah_jendela"] == 59 and a["tanpa_tahun_izin"] == 17
    # nilai pra/pasca per konsesi identik dgn arsip (jendela 2024)
    assert arsip.execute("""SELECT COUNT(*) FROM izin_laju l JOIN a.wiup_temporal t USING (kode_wiup)
        WHERE (t.loss_2001_sampai_tahun_izin_ha IS NOT NULL AND ABS(l.hilang_pra_ha - t.loss_2001_sampai_tahun_izin_ha) > 0.01)
           OR (t.loss_tahun_izin_sampai_2024_ha IS NOT NULL AND ABS(l.hilang_pasca_ha - t.loss_tahun_izin_sampai_2024_ha) > 0.01)""").fetchone()[0] == 0


def test_paritas_registri(arsip):
    n_cocok = arsip.execute("SELECT SUM(cocok) FROM konsesi_registri").fetchone()[0]
    assert n_cocok == arsip.execute("SELECT COUNT(*) FROM a.wiup_match WHERE db_match='yes'").fetchone()[0]
    strat = dict(arsip.execute("SELECT strategi_cocok, COUNT(*) FROM konsesi_registri WHERE cocok=1 GROUP BY 1"))
    a_strat = dict(arsip.execute("SELECT match_strategy, COUNT(*) FROM a.wiup_match WHERE db_match='yes' GROUP BY 1"))
    assert strat == a_strat
    assert arsip.execute("""SELECT COUNT(*) FROM konsesi_registri r JOIN a.wiup_match m USING (kode_wiup)
        WHERE COALESCE(r.id_perizinan,'') <> COALESCE(m.id_perizinan,'')""").fetchone()[0] == 0
    assert arsip.execute("""SELECT COUNT(*) FROM konsesi k JOIN a.wiup_loss l USING (kode_wiup)
        WHERE ABS(k.luas_poligon_ha - l.polygon_area_ha) > 0.01""").fetchone()[0] == 0
    assert arsip.execute("""SELECT COUNT(*) FROM konsesi k JOIN a.wiup_geoportal g USING (kode_wiup)
        WHERE COALESCE(k.tahun_izin,-1) <> COALESCE(g.iup_year,-1) OR k.geometri_geojson <> g.geometry_geojson""").fetchone()[0] == 0


def test_paritas_klasifikasi_dan_tanggal(arsip, him):
    if him != "minerba":
        pytest.skip("arsip lengkap tak punya klasifikasi_izin/wiup_tanggal_pulih yang sebanding")
    z = dict(arsip.execute("SELECT kelas || '/' || COALESCE(bukti,'-'), COUNT(*) FROM izin_klasifikasi GROUP BY 1"))
    a = dict(arsip.execute("SELECT kelas || '/' || COALESCE(bukti,'-'), COUNT(*) FROM a.klasifikasi_izin GROUP BY 1"))
    assert z == a
    assert arsip.execute("""SELECT COUNT(*) FROM izin_klasifikasi z JOIN a.klasifikasi_izin k USING (kode_wiup)
        WHERE z.kelas <> k.kelas OR COALESCE(z.bukti,'') <> COALESCE(k.bukti,'') OR COALESCE(z.durasi_sk,-1) <> COALESCE(k.durasi_sk,-1)""").fetchone()[0] == 0
    # tgl_berlaku/tgl_berakhir identik arsip wiup_tanggal_pulih (795; keputusan W0 — tumpuan keyakinan pra-izin)
    assert arsip.execute("SELECT COUNT(*) FROM a.wiup_tanggal_pulih WHERE tgl_berlaku IS NOT NULL").fetchone()[0] == 795
    assert arsip.execute("SELECT COUNT(tgl_berlaku) FROM konsesi").fetchone()[0] == 795
    assert arsip.execute("SELECT COUNT(*) FROM konsesi WHERE asal_tanggal='ippkh_pulih'").fetchone()[0] == 795
    assert arsip.execute("""SELECT COUNT(*) FROM konsesi k LEFT JOIN a.wiup_tanggal_pulih p USING (kode_wiup)
        WHERE COALESCE(k.tgl_berlaku,'') <> COALESCE(p.tgl_berlaku,'') OR COALESCE(k.tgl_berakhir,'') <> COALESCE(p.tgl_akhir,'')""").fetchone()[0] == 0


def test_idempoten_jalan_ulang_01(db_path, him):
    """01 dijalankan ulang di atas DB yang sudah punya tabel hilir (FK) → tetap sukses, angka sama."""
    if him != "minerba":
        pytest.skip("cukup diuji pada minerba")
    sebelum = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    hash_lama = sebelum.execute("SELECT nilai FROM bangun WHERE kunci='konsesi.hash_geometri'").fetchone()[0]
    sebelum.close()
    subprocess.run([PY, str(AKAR / "pipeline/01_identitas.py"), "--db", str(db_path), "--himpunan", him],
                   check=True, cwd=AKAR, capture_output=True, text=True)
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert c.execute("SELECT COUNT(*) FROM konsesi").fetchone()[0] == 825
    assert c.execute("SELECT nilai FROM bangun WHERE kunci='konsesi.hash_geometri'").fetchone()[0] == hash_lama
    assert c.execute("SELECT COUNT(*) FROM hansen_ringkas").fetchone()[0] == 825   # tabel hilir tak tersentuh
    assert cakupan_dua_arah(c) == []
    c.close()
