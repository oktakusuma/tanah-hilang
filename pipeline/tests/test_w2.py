"""Uji W2 — 04_mapbiomas.py & 05_transisi.py.

Subset cepat (10 konsesi, ±5 detik): DB uji dibuat dari arsip lewat `uji_arsip`, kedua skrip
dijalankan sungguhan, lalu hasilnya dibandingkan PIKSEL DEMI PIKSEL dengan arsip
`data/mapbiomas.db` (geometri sama → wajib identik). Uji penuh (825) hanya jalan bila
`data/.bangun/w2-minerba.db` sudah dibangun (lihat laporan W2); kalau belum, di-skip.

    .venv/bin/python -m pytest pipeline/tests/test_w2.py -q
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

AKAR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AKAR))
from pipeline.lib import uji_arsip  # noqa: E402
from pipeline.lib import w2_mapbiomas as mb  # noqa: E402
from pipeline.lib.meta import cakupan_dua_arah  # noqa: E402

PY = sys.executable
SKRIP_04 = AKAR / "pipeline/04_mapbiomas.py"
SKRIP_05 = AKAR / "pipeline/05_transisi.py"
# Uji paritas penuh dijalankan terhadap DB PRODUKSI, bukan berkas kerja sementara.
# Sebelumnya menunjuk data/.bangun/w2-minerba.db — berkas hasil pengembangan yang hilang
# saat folder kerja dibersihkan, sehingga uji ini diam-diam ter-skip (temuan audit 3 Sep).
# data/tanah-hilang.db selalu ada, di-commit, dan justru artefak yang benar-benar dipakai.
DB_PENUH = AKAR / "data/tanah-hilang.db"
TS_LEGENDA = AKAR / "webapp/src/lib/mapbiomasLegend.ts"

# 10 konsesi: 2 tanpa tahun_izin, 1 besar (22.724 ha, izin 2005), izin 2009/2012/2012/2013/2016/2020/2025
# → kohort umur terisi (5 masuk / 5 keluar), konsesi terkecil hanya 19 piksel.
KODE_UJI = ["1362033032021001", "1361044332022002", "1300003032014011", "3363013032014111",
            "3362053032016194", "3362133032014071", "3364013032014046", "3364023032014207",
            "3364023032014141", "3362133032014117"]


def _arsip_m() -> Path:
    p = uji_arsip.ARSIP_M
    return p if p.exists() else AKAR / "data/arsip" / p.name


def _arsip_k() -> Path:
    return uji_arsip._arsip(None)


pytestmark = pytest.mark.skipif(
    not (_arsip_m().exists() and _arsip_k().exists() and all(mb.path_raster(t).exists() for t in mb.TAHUN)),
    reason="butuh arsip kalimantan.db + mapbiomas.db dan raster MapBiomas 2001-2024")


def _jalan(skrip: Path, db: Path, himpunan: str = "minerba") -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(skrip), "--db", str(db), "--himpunan", himpunan],
                          capture_output=True, text=True, cwd=str(AKAR))


def _buka(db: Path) -> sqlite3.Connection:
    # baca-saja: uji tak boleh mengotori DB yang di-commit
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("ATTACH DATABASE ? AS m", (f"file:{_arsip_m()}?mode=ro",))
    return con


@pytest.fixture(scope="module")
def db_uji(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("w2") / "w2-uji.db"
    con = uji_arsip.siapkan_db_uji(db, ["konsesi"], hanya_kode=KODE_UJI)
    # FK REFERENCES konsesi(kode_wiup) butuh indeks unik pada induk; uji_arsip (sejak 2 Sep 14:36)
    # sudah membuatnya sendiri — IF NOT EXISTS menjaga bila versi lama yang terpasang.
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_konsesi_kode ON konsesi(kode_wiup)")
    con.commit(); con.close()
    r4 = _jalan(SKRIP_04, db)
    assert r4.returncode == 0, r4.stderr[-2000:]
    r5 = _jalan(SKRIP_05, db)
    assert r5.returncode == 0, r5.stderr[-2000:]
    return db


# ── Legenda ──────────────────────────────────────────────────────────────────────────────

def test_legenda_sinkron_dengan_ts_dan_arsip():
    ts = TS_LEGENDA.read_text()
    hex_ts = {int(k): h.lower() for k, h in re.findall(r"kode:\s*(\d+),.*?hex:\s*'(#[0-9a-fA-F]{6})'", ts)}
    assert hex_ts, "MAPBIOMAS_KELAS tak terbaca dari mapbiomasLegend.ts"
    assert {k: h.lower() for k, _, _, h in mb.LEGENDA} == hex_ts
    con = sqlite3.connect(f"file:{_arsip_m()}?mode=ro", uri=True)
    arsip = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT DISTINCT class_code, class_name, kelompok FROM landuse_konsesi")}
    legenda = {k: (n, g) for k, n, g, _ in mb.LEGENDA}
    for k, v in arsip.items():
        assert legenda[k] == v, f"kelas {k}: {legenda.get(k)} != arsip {v}"
    assert {27, 33, 10} <= set(legenda)
    assert {(g, k) for g, k, _ in mb.GABUNGAN} == {("Hutan", 3), ("Hutan", 5), ("Hutan", 76),
                                                   ("Pertanian non-sawit", 9), ("Pertanian non-sawit", 21),
                                                   ("Pertanian non-sawit", 40), ("Tubuh air", 31), ("Tubuh air", 33)}


# ── Subset cepat: identik piksel demi piksel dengan arsip ────────────────────────────────

def test_tahunan_identik_arsip(db_uji):
    con = _buka(db_uji)
    q = ",".join("?" * len(KODE_UJI))
    n_baru = con.execute("SELECT COUNT(*) FROM mapbiomas_tahunan").fetchone()[0]
    n_arsip = con.execute(f"SELECT COUNT(*) FROM m.landuse_konsesi WHERE kode_wiup IN ({q}) "
                          "AND year BETWEEN 2001 AND 2024", KODE_UJI).fetchone()[0]
    assert n_baru == n_arsip > 0
    beda = con.execute("""SELECT COUNT(*) FROM (
        SELECT kode_wiup, tahun, kelas, piksel, ha FROM mapbiomas_tahunan
        EXCEPT SELECT kode_wiup, year, class_code, pixels, ha FROM m.landuse_konsesi)""").fetchone()[0]
    assert beda == 0
    assert con.execute("SELECT COUNT(*) FROM mapbiomas_tahunan WHERE tahun NOT BETWEEN 2001 AND 2024").fetchone()[0] == 0
    # view ringkas: teramati == total (tak ada awan di Kalimantan) & hutan = 3+5+76
    r = con.execute("SELECT SUM(total_ha - teramati_ha), SUM(hutan_ha) FROM v_mapbiomas_ringkas").fetchone()
    h = con.execute("SELECT SUM(ha) FROM mapbiomas_tahunan WHERE kelas IN (3,5,76)").fetchone()[0]
    assert abs(r[0]) < 1e-6 and abs(r[1] - h) < 1e-6
    for gab in ("Hutan", "Pertanian non-sawit", "Tubuh air"):
        ket = con.execute("SELECT keterangan FROM mapbiomas_gabungan WHERE gabungan=?", (gab,)).fetchone()[0]
        assert "BUKAN kelas resmi" in ket


def test_transisi_identik_arsip(db_uji):
    con = _buka(db_uji)
    q = ",".join("?" * len(KODE_UJI))
    kol = "kode_wiup,label,tahun_awal,tahun_akhir,kelas_awal,kelas_akhir"
    # Label kalender & umur+0_+10 identik penuh; umur-10_+0 identik utk tahun_awal >= 2001
    # (arsip memakai raster 2000 sebagai lantai — jendela tesis 2001 tidak).
    for label, syarat in (("2001-2024", ""), ("umur+0_+10", ""), ("umur-10_+0", " AND tahun_awal >= 2001")):
        beda1 = con.execute(f"""SELECT COUNT(*) FROM (
            SELECT {kol},piksel,ha FROM transisi_konsesi WHERE label=?
            EXCEPT SELECT {kol},pixels,ha FROM m.transisi_konsesi WHERE label=?)""", (label, label)).fetchone()[0]
        beda2 = con.execute(f"""SELECT COUNT(*) FROM (
            SELECT {kol},pixels,ha FROM m.transisi_konsesi WHERE label=? AND kode_wiup IN ({q}){syarat}
            EXCEPT SELECT {kol},piksel,ha FROM transisi_konsesi WHERE label=?)""",
                            (label, *KODE_UJI, label)).fetchone()[0]
        assert (beda1, beda2) == (0, 0), label
    assert con.execute("SELECT COUNT(*) FROM transisi_konsesi WHERE kelas_awal=27 OR kelas_akhir=27").fetchone()[0] == 0


def test_kohort_pasangan_dan_rekonsiliasi(db_uji):
    con = _buka(db_uji)
    kohort = {r[0]: r for r in con.execute("SELECT label, jenis, awal, akhir, n_konsesi, n_keluar FROM transisi_kohort")}
    assert set(kohort) == {"2001-2024", "umur-10_+0", "umur+0_+10"}
    assert kohort["2001-2024"][4:] == (10, 0)
    for lb in ("umur-10_+0", "umur+0_+10"):
        assert kohort[lb][4] + kohort[lb][5] == 10
        assert kohort[lb][4] == con.execute(
            "SELECT COUNT(DISTINCT kode_wiup) FROM transisi_konsesi WHERE label=?", (lb,)).fetchone()[0]
    assert con.execute("SELECT COUNT(DISTINCT tahun_awal*10000+tahun_akhir) FROM transisi_pasangan").fetchone()[0] == 276
    # Σ sisi transisi = Σ tahunan (tanpa 0/27) per konsesi, tiap label
    rows = con.execute("""
      WITH tr AS (SELECT label, kode_wiup, tahun_awal, tahun_akhir,
             SUM(CASE WHEN kelas_awal  NOT IN (0,27) THEN ha ELSE 0 END) AS a,
             SUM(CASE WHEN kelas_akhir NOT IN (0,27) THEN ha ELSE 0 END) AS b
           FROM transisi_konsesi GROUP BY label, kode_wiup),
      mt AS (SELECT kode_wiup, tahun, SUM(ha) AS ha FROM mapbiomas_tahunan WHERE kelas<>27 GROUP BY 1,2)
      SELECT MAX(ABS(tr.a - ma.ha)), MAX(ABS(tr.b - mk.ha)), COUNT(*)
      FROM tr JOIN mt ma ON ma.kode_wiup=tr.kode_wiup AND ma.tahun=tr.tahun_awal
              JOIN mt mk ON mk.kode_wiup=tr.kode_wiup AND mk.tahun=tr.tahun_akhir""").fetchone()
    assert rows[2] == 20 and rows[0] < 0.05 and rows[1] < 0.05
    # pasangan (2001,2024) = label 2001-2024
    a = con.execute("SELECT SUM(piksel), SUM(ha) FROM transisi_pasangan WHERE tahun_awal=2001 AND tahun_akhir=2024").fetchone()
    b = con.execute("SELECT SUM(piksel), SUM(ha) FROM transisi_konsesi WHERE label='2001-2024'").fetchone()
    assert a[0] == b[0] and abs(a[1] - b[1]) < 0.5
    # view aliran: nama ter-join dari legenda
    v = con.execute("SELECT COUNT(*) FROM v_transisi_aliran WHERE nama_awal IS NULL OR nama_akhir IS NULL").fetchone()[0]
    assert v == 0


def test_meta_hash_dan_bangun(db_uji):
    con = sqlite3.connect(str(db_uji))
    masalah = [m for m in cakupan_dua_arah(con) if not m.startswith("konsesi:")]   # konsesi = milik W1
    assert masalah == []
    b = dict(con.execute("SELECT kunci, nilai FROM bangun").fetchall())
    assert b["mapbiomas.hash_geometri"] == b["konsesi.hash_geometri"]
    assert "04_mapbiomas.selesai" in b and "05_transisi.selesai" in b
    assert con.execute("SELECT lisensi FROM analysis_meta WHERE nama_tabel='transisi_konsesi'").fetchone()[0].startswith("CC BY-SA")
    assert con.execute("SELECT COUNT(*) FROM sumber WHERE id='mapbiomas'").fetchone()[0] == 1
    for t in ("mapbiomas_kelas", "mapbiomas_gabungan", "mapbiomas_tahunan", "v_mapbiomas_ringkas",
              "transisi_kohort", "transisi_konsesi", "v_transisi_aliran", "transisi_pasangan"):
        d = con.execute("SELECT deskripsi, metode FROM analysis_meta WHERE nama_tabel=?", (t,)).fetchone()
        assert d and "rezim" not in (d[0] + d[1]).lower()


def test_idempoten(db_uji, tmp_path):
    db = tmp_path / "ulang.db"
    shutil.copy(db_uji, db)
    con = sqlite3.connect(str(db))
    sebelum = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("mapbiomas_tahunan", "transisi_konsesi", "transisi_pasangan", "analysis_meta", "column_meta")]
    con.close()
    assert _jalan(SKRIP_04, db).returncode == 0
    assert _jalan(SKRIP_05, db).returncode == 0
    con = sqlite3.connect(str(db))
    sesudah = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("mapbiomas_tahunan", "transisi_konsesi", "transisi_pasangan", "analysis_meta", "column_meta")]
    assert sebelum == sesudah


def test_gagal_keras(db_uji, tmp_path):
    kosong = tmp_path / "kosong.db"
    sqlite3.connect(str(kosong)).close()
    r = _jalan(SKRIP_04, kosong)
    assert r.returncode == 2 and "konsesi" in r.stderr
    r = _jalan(SKRIP_05, kosong)
    assert r.returncode == 2
    # hash geometri beda → 05 menolak
    beda = tmp_path / "beda.db"
    shutil.copy(db_uji, beda)
    con = sqlite3.connect(str(beda))
    con.execute("UPDATE bangun SET nilai='basi' WHERE kunci='mapbiomas.hash_geometri'"); con.commit(); con.close()
    r = _jalan(SKRIP_05, beda)
    assert r.returncode == 2 and "hash geometri" in r.stderr
    # --himpunan tak cocok dgn bangun.himpunan → tolak
    him = tmp_path / "him.db"
    shutil.copy(db_uji, him)
    con = sqlite3.connect(str(him))
    con.execute("INSERT OR REPLACE INTO bangun VALUES ('himpunan','lengkap','x')"); con.commit(); con.close()
    assert _jalan(SKRIP_04, him, "minerba").returncode == 2


# ── Penuh (825) — hanya bila DB penuh sudah dibangun ─────────────────────────────────────

def _db_penuh_siap() -> bool:
    if not DB_PENUH.exists():
        return False
    con = sqlite3.connect(f"file:{DB_PENUH}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM bangun WHERE kunci='05_transisi.selesai'").fetchone()[0] == 1
    except sqlite3.Error:
        return False


@pytest.mark.skipif(not _db_penuh_siap(), reason=f"DB {DB_PENUH.name} belum punya 05_transisi.selesai")
def test_paritas_penuh_minerba():
    con = _buka(DB_PENUH)
    assert con.execute("SELECT COUNT(DISTINCT kode_wiup) FROM mapbiomas_tahunan").fetchone()[0] == 825
    # Σ ha per tahun = arsip
    beda = con.execute("""SELECT MAX(ABS(a.ha - b.ha)) FROM
        (SELECT tahun, SUM(ha) ha FROM mapbiomas_tahunan GROUP BY tahun) a
        JOIN (SELECT year tahun, SUM(ha) ha FROM m.landuse_konsesi GROUP BY year) b USING (tahun)""").fetchone()[0]
    assert beda < 0.5
    assert con.execute("SELECT COUNT(*) FROM (SELECT kode_wiup,tahun,kelas,piksel,ha FROM mapbiomas_tahunan "
                       "EXCEPT SELECT kode_wiup,year,class_code,pixels,ha FROM m.landuse_konsesi)").fetchone()[0] == 0
    # transisi_pasangan: jumlah baris & 276 pasangan = arsip
    assert con.execute("SELECT COUNT(*) FROM transisi_pasangan").fetchone()[0] == \
        con.execute("SELECT COUNT(*) FROM m.transisi_pasangan").fetchone()[0]
    assert con.execute("SELECT COUNT(DISTINCT tahun_awal*10000+tahun_akhir) FROM transisi_pasangan").fetchone()[0] == 276
    assert con.execute("SELECT COUNT(*) FROM (SELECT tahun_awal,tahun_akhir,kelas_awal,kelas_akhir,n_konsesi,piksel,ha "
                       "FROM transisi_pasangan EXCEPT SELECT tahun_awal,tahun_akhir,kelas_awal,kelas_akhir,n_konsesi,pixels,ha "
                       "FROM m.transisi_pasangan)").fetchone()[0] == 0
    # Sankey 2001-2024: pita berubah & Σ dua sisi = arsip (angka arsip saat ini, bukan angka 31 Agu)
    baru = con.execute("SELECT SUM(ha) FROM transisi_konsesi WHERE label='2001-2024' AND kelas_awal<>kelas_akhir "
                       "AND kelas_awal<>27 AND kelas_akhir<>27").fetchone()[0]
    arsip = con.execute("SELECT SUM(ha) FROM m.transisi_konsesi WHERE label='2001-2024' AND kelas_awal<>kelas_akhir "
                        "AND kelas_awal<>27 AND kelas_akhir<>27").fetchone()[0]
    assert abs(baru - arsip) < 1
    sisi = con.execute("SELECT SUM(CASE WHEN kelas_awal NOT IN (0,27) THEN ha END), "
                       "SUM(CASE WHEN kelas_akhir NOT IN (0,27) THEN ha END) FROM transisi_konsesi WHERE label='2001-2024'").fetchone()
    thn = con.execute("SELECT SUM(CASE WHEN tahun=2001 THEN ha END), SUM(CASE WHEN tahun=2024 THEN ha END) "
                      "FROM mapbiomas_tahunan WHERE kelas<>27").fetchone()
    assert abs(sisi[0] - thn[0]) < 1 and abs(sisi[1] - thn[1]) < 1
    # kohort: kalender & umur+0_+10 = arsip; umur-10_+0 = arsip dgn lantai 2001 (bukan 2000)
    k = {r[0]: (r[1], r[2]) for r in con.execute("SELECT label, n_konsesi, n_keluar FROM transisi_kohort")}
    ka = {r[0]: (r[1], r[2]) for r in con.execute("SELECT label, n_konsesi, n_keluar FROM m.transisi_kohort")}
    assert k["2001-2024"] == ka["2001-2024"] and k["umur+0_+10"] == ka["umur+0_+10"]
    n_2001 = con.execute("SELECT COUNT(DISTINCT kode_wiup) FROM m.transisi_konsesi WHERE label='umur-10_+0' AND tahun_awal>=2001").fetchone()[0]
    assert k["umur-10_+0"] == (n_2001, 825 - n_2001)
