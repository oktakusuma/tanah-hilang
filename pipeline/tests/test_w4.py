"""Uji W4: 09_sajikan.py + 10_verifikasi.py.

(1) KOLOM (ekspektasi verifier) == kolom hasil DDL SKEMA — keduanya di lib/w4_skema.py.
(2) DB sintetis 3 konsesi yang memenuhi SEMUA aturan skema + meta → 09 jalan → verifier tanpa FAIL.
(3) Mutasi satu per satu → verifier FAIL pada pemeriksaan yang tepat.
(4) DB uji dari ARSIP (uji_arsip.siapkan_db_uji + tabel W3 yang belum ada di SALIN) → 09 menghasilkan
    825 fitur & hero 1.548.813 / 39,3; verifier paritas arsip PASS. Dilewati bila arsip tak ada.
Semua keluaran ke tmp_path / data/.bangun — JANGAN menimpa geojson/JSON asli.

    .venv/bin/python -m pytest pipeline/tests/test_w4.py -q
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

AKAR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AKAR))
from pipeline.lib import w4_skema as S  # noqa: E402
from pipeline.lib.db import buka, hash_geometri, tulis_bangun  # noqa: E402
from pipeline.lib.meta import INFRA, pastikan_meta, tulis_meta, tulis_sumber  # noqa: E402
from pipeline.lib import uji_arsip  # noqa: E402

PY = sys.executable
SKRIP_09 = AKAR / "pipeline/09_sajikan.py"
SKRIP_10 = AKAR / "pipeline/10_verifikasi.py"


def _verifikasi(**kw):
    """Panggil jalankan() 10_verifikasi.py dalam proses; kembalikan dict nama → status."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verifikasi10", SKRIP_10)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    lap = mod.jalankan(tulis=lambda *_: None, **kw)
    return {n: st for st, n, _ in lap.hasil}, {n: p for _, n, p in lap.hasil}


def _jalankan_09(db: Path, out: Path, db_lengkap: Path | None = None):
    cmd = [PY, str(SKRIP_09), "--db", str(db), "--geojson", str(out / "uji.geojson"), "--stats", str(out / "uji-stats.json")]
    if db_lengkap is not None:
        cmd += ["--db-lengkap", str(db_lengkap)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=AKAR)
    assert r.returncode == 0, r.stdout + r.stderr
    return out / "uji.geojson", out / "uji-stats.json"


# ── (1) KOLOM == DDL ─────────────────────────────────────────────────────────────────────────

def test_kolom_sama_dengan_ddl():
    con = sqlite3.connect(":memory:")
    S.buat_skema_kosong(con)
    for t in S.KOLOM:
        assert S.kolom_objek(con, t) == S.KOLOM[t], t
    assert set(S.objek_di_db(con)) == set(S.KOLOM)
    assert S.N_PASANGAN == 276


# ── (2) DB sintetis ──────────────────────────────────────────────────────────────────────────

GEOM = json.dumps({"type": "Polygon", "coordinates": [[[116, -1], [116.1, -1], [116.1, -0.9], [116, -1]]]})
KONSESI = [  # kode, tahun_izin, hutan_2000, hilang per tahun, vonis
    ("K1", 2010, 100.0, 1.0, "accelerated_post_iup"),
    ("K2", None, 0.0, 0.5, "tanpa_tahun_izin"),
    ("K3", 2025, 50.0, 2.0, "izin_setelah_jendela"),
]
TH = list(range(S.TAHUN_AWAL, S.TAHUN_AKHIR + 1))


def buat_db_sintetis(path: Path, himpunan: str = "minerba") -> Path:
    if path.exists():
        path.unlink()
    con = buka(path)
    for t, ddl in S.DDL.items():
        if t != "v_konsesi":            # v_konsesi dibuat oleh 09_sajikan.py (SKEMA §8)
            con.execute(ddl)
    ex = con.execute; exm = con.executemany
    exm("INSERT INTO konsesi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (k, f"PT {k}", f"SK-{k}", "BATUBARA" if k != "K3" else "EMAS", "IUP", "Operasi Produksi", 1000.0, 990.0, thn,
         f"{thn}-01-01" if thn else None, None, "geoportal" if thn else None, "KALIMANTAN TIMUR", "KUTAI KARTANEGARA",
         "kutai kartanegara", None, "CNC", GEOM, 116, -1, 116.1, -0.9) for k, thn, *_ in KONSESI])
    exm("INSERT INTO konsesi_registri VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("K1", 1, "T0_exact", "P1", "B1", "PT K1", "123", None, "Jl. A", None, "PT", None, None, None, None, None, "http://x"),
        ("K2", 0, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None),
        ("K3", 1, "T2_fuzzy_name", "P3", "B3", "PT K3", "456", None, None, None, "PT", None, None, None, None, None, None)])
    ex("INSERT INTO kepadatan_penduduk VALUES ('6402','KALIMANTAN TIMUR','KUTAI KARTANEGARA','kutai kartanegara',2020,30.5,'jiwa/km2','BPS')")
    exm("INSERT INTO mapbiomas_kelas VALUES (?,?,?,?)", [(3, "Hutan", "Hutan", "#1f8d49"), (27, "Tidak teramati", "Lainnya", "#ffffff"),
                                                       (30, "Lubang tambang", "Area tak bervegetasi", "#9c0027")])
    for k, thn, hutan, per, vonis in KONSESI:
        tot = per * len(TH)
        ex("INSERT INTO hansen_ringkas VALUES (?,?,?,?,?,?)", (k, hutan, tot, round(100 * tot / hutan, 2) if hutan else None, 2005, "00N_110E"))
        exm("INSERT INTO hansen_tahunan VALUES (?,?,?)", [(k, y, per) for y in TH])
        if thn is None:
            ex("INSERT INTO izin_laju VALUES (?,?,?,?,?,?,?,?,?,?)", (k, None, None, None, None, None, None, None, None, vonis))
        elif thn > S.TAHUN_AKHIR:
            ex("INSERT INTO izin_laju VALUES (?,?,?,?,?,?,?,?,?,?)", (k, thn, tot, len(TH), per, None, None, None, None, vonis))
        else:
            npra = thn - S.TAHUN_AWAL; npas = S.TAHUN_AKHIR - thn + 1
            ex("INSERT INTO izin_laju VALUES (?,?,?,?,?,?,?,?,?,?)", (k, thn, per * npra, npra, per, per * npas, npas, per, 1.0, vonis))
        ex("INSERT INTO izin_klasifikasi VALUES (?,?,?,?,?,?,?)", (k, "IZIN_PERTAMA", "INDIKASI", "uji", 10, 0, 0))
        ex("INSERT INTO ippkh VALUES (?,?,?,?,?,?,?,?,?,?,?)", (k, 1 if k != "K2" else 0, 1 if k == "K1" else 0, 1, 1, 10.0, 12.0, 0.01, "2012-01-01", None, 1))
        ex("INSERT INTO keyakinan_pra_izin VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (k, thn, "IZIN_PERTAMA", 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.4, 0.5, 0.5, tot, tot / 2, tot * 0.75))
        # MapBiomas: tiap tahun kelas 3=100, 30=20, 27=5; tahun 2024: 3=80, 30=40 (20 ha hutan→tambang)
        for y in TH:
            h3, h30 = (80.0, 40.0) if y == S.TAHUN_AKHIR else (100.0, 20.0)
            exm("INSERT INTO mapbiomas_tahunan VALUES (?,?,?,?,?)", [(k, y, 3, 1000, h3), (k, y, 30, 200, h30), (k, y, 27, 50, 5.0)])
    exm("INSERT INTO mapbiomas_gabungan VALUES (?,?,?)", [("Hutan", 3, "kelas 3 saja di uji")])
    exm("INSERT INTO transisi_kohort VALUES (?,?,?,?,?,?,?)", [
        ("kalender", "2001-2024", 2001, 2024, 3, 0, "uji"), ("umur", "umur+0_+10", 0, 10, 1, 2, "uji: hanya K1")])
    for k, *_ in KONSESI:
        exm("INSERT INTO transisi_konsesi VALUES (?,?,?,?,?,?,?,?)", [
            (k, "2001-2024", 2001, 2024, 3, 3, 800, 80.0), (k, "2001-2024", 2001, 2024, 3, 30, 200, 20.0),
            (k, "2001-2024", 2001, 2024, 30, 30, 200, 20.0)])
    exm("INSERT INTO transisi_konsesi VALUES (?,?,?,?,?,?,?,?)", [
        ("K1", "umur+0_+10", 2010, 2020, 3, 3, 1000, 100.0), ("K1", "umur+0_+10", 2010, 2020, 30, 30, 200, 20.0)])
    exm("INSERT INTO transisi_pasangan VALUES (?,?,?,?,?,?,?)",
        [(a, b, 3, 3, 3, 3000, 300.0) for a in TH for b in TH if a < b])
    ex("INSERT INTO kawasan_hutan VALUES ('K1','HL','Hutan Lindung',12.5)")
    ex("INSERT INTO ippkh_irisan VALUES ('K1','I1','operasi','PT K1','1/2012','2012-01-01',NULL,'tambang','aktif',12.0,11.9,10.0,0.01,1)")
    exm("INSERT INTO umur_izin_kurun VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (r, kr, a, b, 3, 3.0 * (b - a + 1), 150.0, 150.0 * (b - a + 1), 3.0, 0.02, 2970.0, 0.001, "uji")
        for r in ("A_seimbang", "B_semua") for kr, a, b in (("-8..-1", -8, -1), ("0..4", 0, 4), ("5..9", 5, 9))])
    exm("INSERT INTO umur_izin_tahunan VALUES (?,?,?,?,?,?,?)", [(r, u, 3, 1.0, 150.0, 0.0067, 0.001)
                                                            for r in ("A_seimbang", "B_semua") for u in range(-8, 10)])
    exm("INSERT INTO umur_izin_konsesi VALUES (?,?,?,?,?,?,?,?,?)", [("A_seimbang", "K1", "0..4", 5, 2010, 2014, 100.0, 5.0, 0.01)])
    exm("INSERT INTO keyakinan_model VALUES (?,?,?,?,?)", [("R", "intersep", -1.0, 0.37, 0.68), ("RS", "intersep", -1.1, 0.33, 0.74)])
    exm("INSERT INTO keyakinan_ringkas VALUES (?,?)", [
        ("hilang_batas_bawah_ha", "20.0"), ("hilang_bootstrap_lo_ha", "50.0"), ("hilang_harapan_ha", "63.0"),
        ("hilang_bootstrap_hi_ha", "70.0"), ("hilang_batas_atas_ha", "84.0"), ("n_bootstrap", "2000"), ("benih", "20260831"),
        ("jendela", "2001-2024"), ("ambang_lubang_ha", "5.0"), ("ambang_lubang_tahun", "2")])
    # meta 100% dua arah (kecuali v_konsesi — ditulis 09) + sumber + bangun
    pastikan_meta(con)
    for t in S.KOLOM:
        if t in INFRA or t == "v_konsesi":
            continue
        tulis_meta(con, t, deskripsi=f"uji {t}", sumber="uji", metode="uji", skrip="test_w4", lisensi="uji",
                   kolom=[(c, f"kolom {c}") for c in S.KOLOM[t]])
    for i in ("hansen", "mapbiomas", "geoportal_wiup"):
        tulis_sumber(con, i, f"sumber {i}", "CC BY 4.0")
    h = hash_geometri(con)
    for k, v in (("himpunan", himpunan), ("pipeline_versi", "uji"), ("git_commit", "uji"), ("konsesi.n", 3),
                 ("konsesi.hash_geometri", h), ("mapbiomas.hash_geometri", h)):
        tulis_bangun(con, k, v)
    con.commit(); con.close()
    return path


@pytest.fixture(scope="module")
def db_sintetis(tmp_path_factory):
    d = tmp_path_factory.mktemp("w4")
    db = buat_db_sintetis(d / "sintetis.db")
    db_l = buat_db_sintetis(d / "sintetis-lengkap.db", "lengkap")
    geojson, stats = _jalankan_09(db, d, db_l)
    return db, db_l, geojson, stats, d


def test_sintetis_09_keluaran(db_sintetis):
    db, _, geojson, stats, _ = db_sintetis
    g = json.loads(geojson.read_text())
    assert g["type"] == "FeatureCollection" and len(g["features"]) == 3
    p = g["features"][0]["properties"]
    assert {"kode_wiup", "nama_usaha", "tahun_izin", "hilang_2001_2024_ha", "hilang_2001_ha", "hilang_2024_ha"} <= set(p)
    assert not ({"geometri_geojson", "bbox_min_lon"} & set(p))
    assert p["hilang_2001_2024_ha"] == 24.0 and p["hilang_2010_ha"] == 1.0
    d = json.loads(stats.read_text())
    assert d["jendela"] == "2001-2024"
    assert d["minerba"] == {"n_konsesi": 3, "hutan_2000_ha": 150, "hilang_2001_2024_ha": 84, "pct_hutan_2000": 56.0,
                            "n_provinsi": 1, "n_kabupaten": 1,
                            "komoditas": {"BATUBARA": {"n": 2, "hilang_ha": 36.0},   # K1 24 + K2 12
                                          "EMAS": {"n": 1, "hilang_ha": 48.0}}}      # K3 2 ha × 24 tahun
    assert d["lengkap"] == {"n_konsesi": 3, "hutan_2000_ha": 150, "hilang_2001_2024_ha": 84, "pct_hutan_2000": 56.0}
    assert d["keyakinan"]["hilang_harapan_ha"] == 63.0 and d["keyakinan"]["jendela"] == "2001-2024"
    assert [x["kurun"] for x in d["umur"]["A_seimbang"]] == ["-8..-1", "0..4", "5..9"]
    assert d["ippkh"] == {"n_punya": 2, "n_tambang": 1}
    assert d["sankey_2001_2024"] == {"total_berubah_ha": 60, "n_konsesi": 3}
    assert d["registri"] == {"n_cocok": 2, "per_strategi": {"tanpa_cocok": 1, "T0_exact": 1, "T2_fuzzy_name": 1}}
    for k in ("periode", "atribusi", "lapisan", "kohort", "default", "full"):
        assert k not in d
    # 09 membuat v_konsesi + meta-nya (deskripsi diwariskan dari tabel sumber)
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM v_konsesi").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM column_meta WHERE nama_tabel='v_konsesi'").fetchone()[0] == len(S.KOLOM["v_konsesi"])
    con.close()


def test_sintetis_verifier_pass(db_sintetis):
    db, db_l, _, stats, _ = db_sintetis
    st, pesan = _verifikasi(db=db, himpunan="minerba", stats=stats, arsip=None, arsip_mapbiomas=None)
    assert "FAIL" not in st.values(), {n: pesan[n] for n, s in st.items() if s == "FAIL"}
    for n in ("skema", "meta-dua-arah", "bangun-kunci", "sumber", "rujukan-konsesi", "hansen-identitas", "hansen-pct",
              "izin-laju-identitas", "hash-geometri", "transisi-identitas", "transisi-pasangan", "umur-izin",
              "keyakinan-batas", "stats-web"):
        assert st[n] == "PASS", (n, pesan[n])
    # himpunan lengkap: blok `lengkap` di JSON yang sama = DB lengkap
    st_l, pesan_l = _verifikasi(db=db_l, himpunan="lengkap", stats=stats, arsip=Path("/tidak/ada.db"), arsip_mapbiomas=None)
    assert st_l["stats-web"] == "PASS", pesan_l["stats-web"]
    assert st_l["paritas-arsip"] == "FAIL"          # --arsip diberikan tapi tak ada → FAIL, bukan diam


def test_sintetis_cli_exit0(db_sintetis):
    db, _, _, stats, _ = db_sintetis
    r = subprocess.run([PY, str(SKRIP_10), "--db", str(db), "--himpunan", "minerba", "--stats", str(stats)],
                       capture_output=True, text=True, cwd=AKAR)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RINGKASAN:" in r.stdout and "0 FAIL" in r.stdout


# ── (3) mutasi → FAIL tepat sasaran ──────────────────────────────────────────────────────────

MUTASI = {
    "hansen-identitas": "UPDATE hansen_tahunan SET hilang_ha = hilang_ha + 5 WHERE kode_wiup='K1' AND tahun=2003",
    "hansen-pct": "UPDATE hansen_ringkas SET pct_hutan_2000 = 1.0 WHERE kode_wiup='K1'",
    "izin-laju-identitas": "UPDATE izin_laju SET hilang_pra_ha = hilang_pra_ha + 1 WHERE kode_wiup='K1'",
    "meta-dua-arah": "DELETE FROM column_meta WHERE nama_tabel='hansen_ringkas' AND nama_kolom='hutan_2000_ha'",
    "hash-geometri": "UPDATE bangun SET nilai='deadbeef' WHERE kunci='mapbiomas.hash_geometri'",
    "skema": "ALTER TABLE ippkh DROP COLUMN cocok_nama",
    "skema-objek-asing": "CREATE TABLE tabel_liar (x INTEGER)",
    "transisi-identitas": "UPDATE transisi_konsesi SET ha = ha + 1 WHERE kode_wiup='K2' AND label='2001-2024' AND kelas_awal=3 AND kelas_akhir=30",
    "transisi-pasangan": "DELETE FROM transisi_pasangan WHERE tahun_awal=2001 AND tahun_akhir=2002",
    "umur-izin": "UPDATE umur_izin_tahunan SET n_konsesi=0 WHERE rancangan='A_seimbang' AND umur_relatif=0",
    "keyakinan-batas": "UPDATE keyakinan_ringkas SET nilai='10.0' WHERE kunci='hilang_bootstrap_hi_ha'",
    "bangun-kunci": "DELETE FROM bangun WHERE kunci='git_commit'",
    "sumber": "DELETE FROM sumber WHERE id='mapbiomas'",
    "rujukan-konsesi": "UPDATE ippkh SET kode_wiup='K9' WHERE kode_wiup='K2'",
    "stats-web": "UPDATE hansen_ringkas SET hutan_2000_ha = hutan_2000_ha + 1000 WHERE kode_wiup='K1'",
}


@pytest.mark.parametrize("nama", list(MUTASI))
def test_mutasi_fail(db_sintetis, nama, tmp_path):
    db, _, _, stats, _ = db_sintetis
    salin = tmp_path / f"mut-{nama}.db"
    shutil.copy(db, salin)
    con = sqlite3.connect(salin); con.execute(MUTASI[nama]); con.commit(); con.close()
    st, pesan = _verifikasi(db=salin, himpunan="minerba", stats=stats, arsip=None, arsip_mapbiomas=None)
    sasaran = "skema" if nama.startswith("skema") else nama
    assert st[sasaran] == "FAIL", (sasaran, st, pesan.get(sasaran))


def test_himpunan_salah_fail(db_sintetis):
    db, _, _, stats, _ = db_sintetis
    st, _ = _verifikasi(db=db, himpunan="lengkap", stats=stats, arsip=Path("/tidak/ada.db"), arsip_mapbiomas=None)
    assert st["bangun-kunci"] == "FAIL"


def test_tabel_inti_absen_gagal_keras(tmp_path):
    db = buat_db_sintetis(tmp_path / "kurang.db")
    con = sqlite3.connect(db); con.execute("DROP TABLE keyakinan_model"); con.commit(); con.close()
    with pytest.raises(SystemExit) as e:
        _verifikasi(db=db, himpunan="minerba", stats=None, arsip=None, arsip_mapbiomas=None)
    assert e.value.code == 2
    r = subprocess.run([PY, str(SKRIP_09), "--db", str(db), "--geojson", str(tmp_path / "x.geojson"),
                        "--stats", str(tmp_path / "x.json")], capture_output=True, text=True, cwd=AKAR)
    assert r.returncode == 0                       # keyakinan_model bukan hulu 09
    con = sqlite3.connect(db); con.execute("DROP TABLE keyakinan_ringkas"); con.commit(); con.close()
    r = subprocess.run([PY, str(SKRIP_09), "--db", str(db), "--geojson", str(tmp_path / "x.geojson"),
                        "--stats", str(tmp_path / "x.json")], capture_output=True, text=True, cwd=AKAR)
    assert r.returncode == 2 and "keyakinan_ringkas" in r.stderr


# ── (4) DB uji dari arsip: 825 fitur, hero 1548813 / 39,3, paritas PASS ──────────────────────

ARSIP_K = uji_arsip._arsip(None)
ARSIP_M = uji_arsip.ARSIP_M if uji_arsip.ARSIP_M.exists() else AKAR / "data/arsip/mapbiomas.db"
BANGUN = AKAR / "data/.bangun"

TAMBAHAN_W3 = """
CREATE TABLE umur_izin_kurun AS SELECT rancangan,kurun,umur_awal,umur_akhir,n_konsesi,konsesi_tahun,hutan_awal_ha,hutan_tahun_ha,
  loss_ha AS hilang_ha,laju_bahaya_per_tahun,konsesi_luas_tahun_ha,laju_luas_per_tahun,catatan FROM a.umur_izin_kurun;
CREATE TABLE umur_izin_tahunan AS SELECT rancangan,umur_relatif,n_konsesi,loss_ha AS hilang_ha,hutan_ha,laju_bahaya_per_tahun,
  laju_luas_per_tahun FROM a.umur_izin_tahunan;
CREATE TABLE umur_izin_konsesi AS SELECT rancangan,kode_wiup,kurun,tahun_teramati,tahun_awal,tahun_akhir,hutan_awal_ha,
  loss_ha AS hilang_ha,laju_bahaya_per_tahun FROM a.umur_izin_konsesi;
CREATE TABLE keyakinan_pra_izin AS SELECT kode_wiup,iup_year AS tahun_izin,kelas,durasi_sk,e_lubang_pra_izin,f_ippkh_lebih_tua,
  a_kontrak_karya,b_durasi_pendek,c_masa_diwarisi,g_registri_beda,d_hansen_pra_dominan,n_sinyal,bukti_langsung,peluang_model_r,
  peluang_model_rs,peluang_akhir,loss_sejak_2001_ha AS hilang_sejak_2001_ha,loss_sejak_sk_ha AS hilang_sejak_sk_ha,
  loss_harapan_ha AS hilang_harapan_ha FROM a.keyakinan_pra_izin;
CREATE TABLE keyakinan_model AS SELECT * FROM a.keyakinan_model;
CREATE TABLE keyakinan_ringkas AS SELECT REPLACE(kunci,'loss_','hilang_') AS kunci, nilai FROM a.keyakinan_ringkas;
CREATE TABLE ippkh_irisan AS SELECT * FROM a.ippkh_konsesi_irisan;
"""


def siapkan_db_uji_arsip(path: Path) -> Path:
    """uji_arsip.siapkan_db_uji (semua SALIN) + tabel W3/W2 yang belum ada di SALIN — hanya untuk test W4."""
    con = uji_arsip.siapkan_db_uji(path, list(uji_arsip.SALIN))
    con.execute("ATTACH DATABASE ? AS a", (f"file:{ARSIP_K}?mode=ro",))
    con.executescript(TAMBAHAN_W3)
    con.execute("DETACH DATABASE a")
    con.execute(S.DDL["mapbiomas_gabungan"]); con.execute(S.DDL["v_mapbiomas_ringkas"]); con.execute(S.DDL["v_transisi_aliran"])
    tulis_bangun(con, "mapbiomas.hash_geometri", con.execute("SELECT nilai FROM bangun WHERE kunci='konsesi.hash_geometri'").fetchone()[0])
    # w1_util.pastikan_v_konsesi (dipanggil 09) menuntut column_meta konsesi terisi — meta minimal, hanya utk test
    from pipeline.lib.meta import pastikan_meta as _pm, tulis_meta as _tm
    _pm(con); _tm(con, "konsesi", deskripsi="uji", sumber="arsip", metode="salin", skrip="test_w4", lisensi="uji",
                  kolom=[(c, f"kolom {c}") for c in S.KOLOM["konsesi"]])
    tulis_bangun(con, "himpunan", "minerba")
    con.commit(); con.close()
    return path


@pytest.mark.skipif(not (ARSIP_K.exists() and ARSIP_M.exists()), reason="DB arsip tak ada")
def test_arsip_09_dan_paritas():
    BANGUN.mkdir(exist_ok=True)
    db = siapkan_db_uji_arsip(BANGUN / "w4-minerba.db")
    out = BANGUN / "w4-uji-keluaran"; out.mkdir(exist_ok=True)
    geojson, stats = _jalankan_09(db, out)
    g = json.loads(geojson.read_text())
    assert len(g["features"]) == 825
    p = g["features"][0]["properties"]
    assert {"kode_wiup", "nama_usaha", "tahun_izin", "hilang_2001_2024_ha", "hilang_2001_ha", "hilang_2024_ha"} <= set(p)
    assert "hilang_2025_ha" not in p and "geometri_geojson" not in p
    d = json.loads(stats.read_text())
    assert d["minerba"]["n_konsesi"] == 825
    assert d["minerba"]["hilang_2001_2024_ha"] == 1548813 and d["minerba"]["pct_hutan_2000"] == 39.3
    assert d["minerba"]["hutan_2000_ha"] == 3943141
    # bentuk komoditas = {nama: {n, hilang_ha}} (sama dgn per_komoditas di API v3); Σ n = jumlah konsesi
    assert d["minerba"]["komoditas"]["BATUBARA"]["n"] == 658
    assert sum(v["n"] for v in d["minerba"]["komoditas"].values()) == 825
    assert round(sum(v["hilang_ha"] for v in d["minerba"]["komoditas"].values()), 2) == 1548812.60
    assert d["ippkh"] == {"n_punya": 274, "n_tambang": 256}
    assert d["keyakinan"]["hilang_harapan_ha"] == 1189114.1
    assert [x["n_konsesi"] for x in d["umur"]["A_seimbang"]] == [260, 260, 260]
    assert d["lengkap"] is None

    st, pesan = _verifikasi(db=db, himpunan="minerba", stats=stats, arsip=ARSIP_K, arsip_mapbiomas=ARSIP_M)
    for n in ("hansen-identitas", "hansen-pct", "izin-laju-identitas", "hash-geometri", "transisi-identitas",
              "transisi-pasangan", "umur-izin", "keyakinan-batas", "stats-web", "paritas-hansen", "paritas-vonis",
              "paritas-klasifikasi", "paritas-registri", "paritas-ippkh", "paritas-keyakinan", "paritas-umur",
              "paritas-keyakinan-bootstrap", "paritas-sankey", "paritas-mapbiomas-tahunan"):
        assert st[n] == "PASS", (n, pesan[n])
    # DB uji arsip memang belum punya meta & sumber (milik W1–W3) — hanya itu yang boleh FAIL
    # DB uji arsip: tanpa meta/sumber (milik W1–W3) dan kohort umur-10_+0 masih 682/143 (arsip) ≠ v3 624/201
    assert {n for n, s in st.items() if s == "FAIL"} == {"meta-dua-arah", "sumber", "paritas-kohort"}
    assert st["paritas-vonis"] == "PASS"


@pytest.mark.skipif(not (ARSIP_K.exists() and ARSIP_M.exists()), reason="DB arsip tak ada")
@pytest.mark.parametrize("geser,harap", [("v3", "PASS"), (1500.0, "WARN"), (5000.0, "FAIL")])
def test_arsip_bootstrap_tiga_cabang(tmp_path, geser, harap):
    """Selang bootstrap v3 ≠ arsip (urutan iterasi): = jangkar v3 → PASS; < 2.000 ha dari arsip → WARN; lebih → FAIL."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verifikasi10b", SKRIP_10)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    db = siapkan_db_uji_arsip(tmp_path / "arsip-bootstrap.db")
    _jalankan_09(db, tmp_path)                      # membuat v_konsesi (tabel inti verifier)
    con = sqlite3.connect(db)
    if geser == "v3":
        lo, hi = mod.JANGKAR_V3_BOOTSTRAP
    else:
        lo = float(con.execute("SELECT nilai FROM keyakinan_ringkas WHERE kunci='hilang_bootstrap_lo_ha'").fetchone()[0]) - geser
        hi = float(con.execute("SELECT nilai FROM keyakinan_ringkas WHERE kunci='hilang_bootstrap_hi_ha'").fetchone()[0]) + geser
    con.execute("UPDATE keyakinan_ringkas SET nilai=? WHERE kunci='hilang_bootstrap_lo_ha'", (f"{lo:.2f}",))
    con.execute("UPDATE keyakinan_ringkas SET nilai=? WHERE kunci='hilang_bootstrap_hi_ha'", (f"{hi:.2f}",))
    con.commit(); con.close()
    st, pesan = _verifikasi(db=db, himpunan="minerba", stats=None, arsip=ARSIP_K, arsip_mapbiomas=ARSIP_M)
    assert st["paritas-keyakinan-bootstrap"] == harap, pesan["paritas-keyakinan-bootstrap"]
    assert st["paritas-keyakinan"] == "PASS"


@pytest.mark.skipif(not (ARSIP_K.exists() and ARSIP_M.exists()), reason="DB arsip tak ada")
@pytest.mark.parametrize("arah,harap", [(None, "PASS"), ("naik_jangkar", "PASS"), ("naik_lain", "WARN"),
                                        ("turun", "FAIL")])
def test_arsip_pra_izin_dominan(tmp_path, arah, harap):
    """Bendera pra_izin_dominan v3 (jendela pasca 2024) vs arsip (2025): mempersempit jendela hanya bisa
    membalik 0/NULL → 1. Sama dgn arsip atau tepat sejumlah jangkar v3 → PASS; jumlah lain (arah benar) →
    WARN; ada yang berbalik 1 → 0 → FAIL."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verifikasi10c", SKRIP_10)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    db = siapkan_db_uji_arsip(tmp_path / f"arsip-pradominan-{arah}.db")
    _jalankan_09(db, tmp_path)                      # membuat v_konsesi (tabel inti verifier)
    con = sqlite3.connect(db)
    if arah == "turun":
        kode = [r[0] for r in con.execute(
            "SELECT kode_wiup FROM izin_klasifikasi WHERE pra_izin_dominan=1 ORDER BY kode_wiup LIMIT 1")]
        con.executemany("UPDATE izin_klasifikasi SET pra_izin_dominan=0 WHERE kode_wiup=?", [(k,) for k in kode])
    elif arah is not None:
        n = mod.JANGKAR_V3_PRA_DOMINAN if arah == "naik_jangkar" else mod.JANGKAR_V3_PRA_DOMINAN + 1
        kode = [r[0] for r in con.execute(
            "SELECT kode_wiup FROM izin_klasifikasi WHERE COALESCE(pra_izin_dominan,0)=0 ORDER BY kode_wiup LIMIT ?", (n,))]
        assert len(kode) == n
        con.executemany("UPDATE izin_klasifikasi SET pra_izin_dominan=1 WHERE kode_wiup=?", [(k,) for k in kode])
    con.commit(); con.close()
    st, pesan = _verifikasi(db=db, himpunan="minerba", stats=None, arsip=ARSIP_K, arsip_mapbiomas=ARSIP_M)
    assert st["paritas-pra-izin-dominan"] == harap, pesan["paritas-pra-izin-dominan"]
    # AUC RS tak ikut bergeser di DB uji (keyakinan_model disalin apa adanya) → tak ada WARN tambahan
    assert "paritas-keyakinan-auc-rs" not in st


@pytest.mark.skipif(not (ARSIP_K.exists() and ARSIP_M.exists()), reason="DB arsip tak ada")
@pytest.mark.parametrize("geser,harap", [(0.005, "WARN"), (0.05, "FAIL")])
def test_arsip_auc_rs_bergeser(tmp_path, geser, harap):
    """AUC model RS memakai sinyal D (pra_izin_dominan) yang jendelanya beda dari arsip: geseran kecil
    + bendera D memang berbeda → WARN terpisah; geseran besar → FAIL di paritas-keyakinan. AUC model R
    (sumber peluang_akhir) tak pernah boleh bergeser."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verifikasi10d", SKRIP_10)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    db = siapkan_db_uji_arsip(tmp_path / f"arsip-aucrs-{geser}.db")
    _jalankan_09(db, tmp_path)
    con = sqlite3.connect(db)
    kode = [r[0] for r in con.execute(
        "SELECT kode_wiup FROM izin_klasifikasi WHERE COALESCE(pra_izin_dominan,0)=0 ORDER BY kode_wiup LIMIT ?",
        (mod.JANGKAR_V3_PRA_DOMINAN,))]
    con.executemany("UPDATE izin_klasifikasi SET pra_izin_dominan=1 WHERE kode_wiup=?", [(k,) for k in kode])
    con.execute("UPDATE keyakinan_model SET auc = auc - ? WHERE model='RS'", (geser,))
    con.commit(); con.close()
    st, pesan = _verifikasi(db=db, himpunan="minerba", stats=None, arsip=ARSIP_K, arsip_mapbiomas=ARSIP_M)
    if harap == "WARN":
        assert st["paritas-keyakinan"] == "PASS", pesan["paritas-keyakinan"]
        assert st["paritas-keyakinan-auc-rs"] == "WARN", pesan["paritas-keyakinan-auc-rs"]
    else:
        assert st["paritas-keyakinan"] == "FAIL" and "AUC RS" in pesan["paritas-keyakinan"]
        assert "paritas-keyakinan-auc-rs" not in st
