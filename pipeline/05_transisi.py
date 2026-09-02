#!/usr/bin/env python3
"""Langkah 05 — matriks transisi guna lahan (bahan diagram Sankey), SKEMA.md §4.

MASALAH YANG DIJAWAB. `mapbiomas_tahunan` menjawab "tahun ini isinya apa"; ia TIDAK bisa
menjawab "kelas ini berubah jadi apa" karena luas per kelas tak menyimpan asal-usul piksel.
Sankey butuh ALIRAN, jadi butuh tabulasi silang piksel antara dua tahun (RQ b & c).

CARA. Untuk tiap konsesi, 24 raster 2001–2024 dibaca SEKALI (jendela + topeng yang sama
dengan 04_mapbiomas), lalu tiap pasangan tahun ditabulasi silang dari larik di memori:
`bincount(kelas_A * 100 + kelas_B)`. Luas dikoreksi lintang persis seperti 04 sehingga
Σ transisi dapat direkonsiliasi dengan Σ mapbiomas_tahunan (diassert di sini).

KELUARAN (milik skrip ini; di-drop & dibuat ulang → idempoten):
  transisi_kohort    definisi kohort + n_konsesi & n_keluar (catatan kaki WAJIB)
  transisi_konsesi   per konsesi × label kohort × (kelas_awal, kelas_akhir) → piksel, ha
  v_transisi_aliran  agregat per label (nama kelas di-JOIN dari mapbiomas_kelas, tak disimpan ganda)
  transisi_pasangan  agregat SEMUA 276 pasangan tahun a<b dalam 2001–2024 — tampilan "tahun dinamis"

KOHORT. `2001-2024` (kalender, semua konsesi); `umur-10_+0` dan `umur+0_+10` (relatif tahun
izin, t0 = max(tahun_izin, 2001)); konsesi yang salah satu ujung jendelanya di luar
2001–2024 atau tanpa tahun_izin KELUAR dan dicatat di `n_keluar`.
CATATAN vs arsip: pipeline arsip memakai raster 2000 sebagai lantai kohort umur; di sini
lantainya 2001 (jendela tesis), sehingga n_konsesi `umur-10_+0` lebih kecil dari arsip.

KAVEAT WAJIB IKUT SETIAP SANKEY: (1) aliran ke/dari Lubang Tambang = BATAS BAWAH; (2) pita
antar langkah TIDAK boleh dijumlahkan — baca langsung dari pasangan tahunnya (Σ 23 langkah
tahunan ≈ 2,5× transisi langsung); (3) kelas 27 (awan) dikecualikan dari Sankey.
Baris dengan nodata (0) di satu sisi disimpan (tepi laut), tapi tak ikut view (tak ada di legenda).

    python pipeline/05_transisi.py --db data/tanah-hilang.db --himpunan minerba
Prasyarat: konsesi, mapbiomas_tahunan, mapbiomas_kelas + bangun.mapbiomas.hash_geometri == konsesi.hash_geometri.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib import w2_mapbiomas as mb  # noqa: E402
from pipeline.lib.db import argparser, buka, gagal, tandai_selesai, wajib_tabel  # noqa: E402
from pipeline.lib.meta import LISENSI, tulis_meta  # noqa: E402

SKRIP = "pipeline/05_transisi.py"
PENGALI = 100                      # kelas_awal*100 + kelas_akhir; kode tertinggi C4.1 = 76
NKOMBO = PENGALI * PENGALI
KALENDER = [(2001, 2024)]
UMUR = [(-10, 0), (0, 10)]
PASANGAN = [(a, b) for i, a in enumerate(mb.TAHUN) for b in mb.TAHUN[i + 1:]]   # 276
TOL_REKON_HA = 0.05                # ha per konsesi; baris dibulatkan 4 desimal → galat ≤ ~0,02
TAK_INFORMATIF = (mb.KELAS_NODATA, mb.KELAS_AWAN)

DDL = """
DROP VIEW  IF EXISTS v_transisi_aliran;
DROP TABLE IF EXISTS transisi_pasangan;
DROP TABLE IF EXISTS transisi_konsesi;
DROP TABLE IF EXISTS transisi_kohort;
CREATE TABLE transisi_kohort (
  jenis TEXT NOT NULL, label TEXT PRIMARY KEY, awal INTEGER NOT NULL, akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, n_keluar INTEGER NOT NULL, catatan TEXT
);
CREATE TABLE transisi_konsesi (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), label TEXT NOT NULL REFERENCES transisi_kohort(label),
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, label, kelas_awal, kelas_akhir)
);
CREATE INDEX idx_transisi_konsesi_label ON transisi_konsesi(label);
CREATE VIEW v_transisi_aliran AS
  SELECT t.label, t.kelas_awal, t.kelas_akhir, a.nama AS nama_awal, b.nama AS nama_akhir,
         COUNT(DISTINCT t.kode_wiup) AS n_konsesi, SUM(t.piksel) AS piksel, ROUND(SUM(t.ha),2) AS ha
  FROM transisi_konsesi t JOIN mapbiomas_kelas a ON a.kelas=t.kelas_awal JOIN mapbiomas_kelas b ON b.kelas=t.kelas_akhir
  GROUP BY t.label, t.kelas_awal, t.kelas_akhir;
CREATE TABLE transisi_pasangan (
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (tahun_awal, tahun_akhir, kelas_awal, kelas_akhir), CHECK (tahun_awal < tahun_akhir)
);
"""


def silang(va: np.ndarray, vb: np.ndarray, luas: np.ndarray):
    """(piksel, ha) per kombinasi kelas, panjang NKOMBO. va/vb = kelas piksel di dalam topeng."""
    kombo = va * PENGALI + vb
    return np.bincount(kombo, minlength=NKOMBO), np.bincount(kombo, weights=luas, minlength=NKOMBO)


def susun_kohort(kode_ada: list[str], t0: dict[str, int]):
    """→ (baris transisi_kohort, tugas: kode → [(label, ya, yb), ...])."""
    kohort: list[tuple] = []
    tugas: dict[str, list[tuple[str, int, int]]] = {k: [] for k in kode_ada}
    for ya, yb in KALENDER:
        label = f"{ya}-{yb}"
        for k in kode_ada:
            tugas[k].append((label, ya, yb))
        kohort.append(("kalender", label, ya, yb, len(kode_ada), 0,
                       f"Semua konsesi yang punya piksel di raster, jendela kalender {ya}-{yb}."))
    for oa, ob in UMUR:
        label = f"umur{oa:+d}_{ob:+d}"
        ikut = keluar = 0
        for k in kode_ada:
            t = t0.get(k)
            if t is None:
                keluar += 1
                continue
            ya, yb = t + oa, t + ob
            if not (mb.TAHUN_MIN <= ya and yb <= mb.TAHUN_MAX):
                keluar += 1
                continue
            tugas[k].append((label, ya, yb))
            ikut += 1
        kohort.append(("umur", label, oa, ob, ikut, keluar,
                       f"Jendela {ob - oa} tahun relatif tahun izin (t0 = max(tahun_izin, {mb.TAHUN_MIN})), "
                       f"offset {oa:+d} sampai {ob:+d}. {ikut} konsesi masuk, {keluar} keluar karena tanpa "
                       f"tahun izin atau salah satu ujung jendelanya di luar {mb.TAHUN_MIN}-{mb.TAHUN_MAX}. "
                       "n_keluar WAJIB ditampilkan bersama angka kohort ini."))
    return kohort, tugas


def hitung(berkas, konsesi, tugas):
    """Satu lintasan per konsesi: baca 24 tahun sekali, lalu semua tabulasi silang di memori.
    → (baris transisi_konsesi, baris transisi_pasangan)."""
    per_konsesi: list[tuple] = []
    agg: dict[tuple[int, int, int, int], list] = {}
    ds = {y: rasterio.open(p) for y, p in berkas.items()}
    mulai = time.time()
    try:
        for n, (kode, win, mask, area) in enumerate(konsesi, 1):
            nilai = {y: ds[y].read(1, window=win)[mask].astype(np.int32) for y in mb.TAHUN}
            luas = area[mask]
            if max(int(v.max(initial=0)) for v in nilai.values()) >= PENGALI:
                gagal("ada kode kelas >= 100 — PENGALI harus dinaikkan")
            for label, ya, yb in tugas.get(kode, ()):
                px, ha = silang(nilai[ya], nilai[yb], luas)
                for k in np.nonzero(px)[0]:
                    ka, kb = divmod(int(k), PENGALI)
                    if ka == mb.KELAS_NODATA and kb == mb.KELAS_NODATA:
                        continue                                  # laut ↔ laut
                    per_konsesi.append((kode, label, ya, yb, ka, kb, int(px[k]), round(float(ha[k]), 4)))
            for ya, yb in PASANGAN:
                px, ha = silang(nilai[ya], nilai[yb], luas)
                for k in np.nonzero(px)[0]:
                    ka, kb = divmod(int(k), PENGALI)
                    if ka == mb.KELAS_NODATA and kb == mb.KELAS_NODATA:
                        continue
                    s = agg.setdefault((ya, yb, ka, kb), [0, 0.0, 0])
                    s[0] += int(px[k]); s[1] += float(ha[k]); s[2] += 1
            if n % 50 == 0 or n == len(konsesi):
                print(f"  {n}/{len(konsesi)} konsesi, {len(per_konsesi):,} baris kohort, "
                      f"{len(agg):,} sel pasangan ({time.time() - mulai:,.0f} s)", flush=True)
    finally:
        for d in ds.values():
            d.close()
    pasangan = [(ya, yb, ka, kb, n_k, px, round(ha, 4))
                for (ya, yb, ka, kb), (px, ha, n_k) in sorted(agg.items())]
    return per_konsesi, pasangan


def rekonsiliasi(con, kohort, tugas) -> dict[str, float]:
    """Assert per label & per konsesi: Σ ha sisi awal transisi (tanpa 0/27) = Σ mapbiomas_tahunan
    tahun_awal tanpa 27; sisi akhir idem. Gagal keras bila ada selisih > TOL_REKON_HA."""
    hasil: dict[str, float] = {}
    q = """
      WITH tr AS (
        SELECT kode_wiup, tahun_awal, tahun_akhir,
               SUM(CASE WHEN kelas_awal  NOT IN (0,27) THEN ha ELSE 0 END) AS awal,
               SUM(CASE WHEN kelas_akhir NOT IN (0,27) THEN ha ELSE 0 END) AS akhir
        FROM transisi_konsesi WHERE label=? GROUP BY kode_wiup),
      mt AS (SELECT kode_wiup, tahun, SUM(ha) AS ha FROM mapbiomas_tahunan WHERE kelas<>27 GROUP BY kode_wiup, tahun)
      SELECT tr.kode_wiup, tr.awal, COALESCE(ma.ha,0), tr.akhir, COALESCE(mk.ha,0)
      FROM tr LEFT JOIN mt ma ON ma.kode_wiup=tr.kode_wiup AND ma.tahun=tr.tahun_awal
              LEFT JOIN mt mk ON mk.kode_wiup=tr.kode_wiup AND mk.tahun=tr.tahun_akhir"""
    # Konsesi yang NOL piksel MapBiomas (poligon di luar cakupan raster darat — mis. WIUP
    # PASIR LAUT di perairan) sah tak punya baris transisi: tak ada tutupan lahan yang teramati.
    # Rekonsiliasi karena itu dibandingkan terhadap anggota kohort yang PUNYA piksel.
    tanpa_piksel = {r[0] for r in con.execute(
        "SELECT k.kode_wiup FROM konsesi k WHERE NOT EXISTS "
        "(SELECT 1 FROM mapbiomas_tahunan m WHERE m.kode_wiup = k.kode_wiup)")}
    if tanpa_piksel:
        print(f"  catatan: {len(tanpa_piksel)} konsesi tanpa piksel MapBiomas "
              f"(poligon di luar cakupan raster darat) — dikecualikan dari rekonsiliasi")
    for _, label, *_ in kohort:
        harap = {k for k, tg in tugas.items() if any(lb == label for lb, _, _ in tg)} - tanpa_piksel
        rows = con.execute(q, (label,)).fetchall()
        ada = {r[0] for r in rows}
        if ada != harap:
            gagal(f"rekonsiliasi {label}: konsesi di transisi ({len(ada)}) != kohort berpiksel ({len(harap)}); "
                  f"selisih {sorted(ada ^ harap)[:5]}")
        maks = 0.0
        for kode, ta, ma, tk, mk in rows:
            d = max(abs(ta - ma), abs(tk - mk))
            maks = max(maks, d)
            if d > TOL_REKON_HA:
                gagal(f"rekonsiliasi {label} gagal di {kode}: transisi awal {ta:.4f} vs tahunan {ma:.4f}; "
                      f"akhir {tk:.4f} vs {mk:.4f}")
        tot = con.execute(
            "SELECT ROUND(SUM(CASE WHEN kelas_awal NOT IN (0,27) THEN ha END),2), "
            "ROUND(SUM(CASE WHEN kelas_akhir NOT IN (0,27) THEN ha END),2) "
            "FROM transisi_konsesi WHERE label=?", (label,)).fetchone()
        print(f"  rekonsiliasi {label}: {len(rows)} konsesi, selisih maks {maks:.4f} ha; "
              f"Σ awal {tot[0]:,.2f} ha, Σ akhir {tot[1]:,.2f} ha")
        hasil[label] = maks
    n_pas = con.execute("SELECT COUNT(DISTINCT tahun_awal*10000+tahun_akhir) FROM transisi_pasangan").fetchone()[0]
    if n_pas != len(PASANGAN):
        gagal(f"transisi_pasangan punya {n_pas} pasangan tahun, harus {len(PASANGAN)}")
    # Pasangan (2001,2024) agregat harus = Σ label 2001-2024 per konsesi.
    a = con.execute("SELECT SUM(ha), SUM(piksel) FROM transisi_pasangan WHERE tahun_awal=2001 AND tahun_akhir=2024").fetchone()
    b = con.execute("SELECT SUM(ha), SUM(piksel) FROM transisi_konsesi WHERE label='2001-2024'").fetchone()
    if a[1] != b[1] or abs((a[0] or 0) - (b[0] or 0)) > 0.5:
        gagal(f"transisi_pasangan (2001,2024) {a} != transisi_konsesi label 2001-2024 {b}")
    return hasil


def tulis_meta_semua(con) -> None:
    lis = LISENSI["mapbiomas"]
    sumber = (mb.SUMBER_RINGKAS + "; tabulasi silang piksel dua tahun di dalam topeng poligon konsesi")
    tulis_meta(con, "transisi_kohort",
               deskripsi=("Definisi tiap kohort Sankey + berapa konsesi MASUK dan KELUAR (tanpa tahun izin "
                          "atau ujung jendela di luar 2001-2024). Disimpan supaya angka kohort tak pernah "
                          "tampil tanpa catatan kakinya (n_keluar WAJIB ikut ditampilkan)."),
               sumber="konsesi.tahun_izin (DB ini) + jendela tesis 2001-2024",
               metode=("Kalender: semua konsesi berpiksel. Umur: t0 = max(tahun_izin, 2001); konsesi masuk "
                       "bila 2001 <= t0+awal dan t0+akhir <= 2024. Dicatat saat menyusun tugas per konsesi. "
                       "Reproduksi: python pipeline/05_transisi.py --db <db> --himpunan <minerba|lengkap>."),
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("jenis", "'kalender' (tahun tetap utk semua konsesi) atau 'umur' (relatif tahun izin).", "-", SKRIP),
                   ("label", "Nama kohort — kunci ke transisi_konsesi.label ('2001-2024', 'umur-10_+0', 'umur+0_+10').", "-", SKRIP),
                   ("awal", "Tahun awal (kalender) atau offset awal terhadap t0 (umur).", "-", SKRIP),
                   ("akhir", "Tahun akhir (kalender) atau offset akhir terhadap t0 (umur).", "-", SKRIP),
                   ("n_konsesi", "Konsesi yang MASUK kohort ini DAN punya piksel MapBiomas teramati (konsesi berpoligon di luar cakupan raster darat tak dihitung — lihat catatan).", "COUNT(DISTINCT kode_wiup) baris transisi_konsesi label ini", SKRIP),
                   ("n_keluar", "Konsesi yang KELUAR (tanpa tahun_izin atau ujung jendela di luar 2001-2024). Wajib dilaporkan.",
                    "n konsesi berpiksel - n_konsesi", SKRIP),
                   ("catatan", "Penjelasan kohort + catatan kaki yang harus ikut tampil bersama angkanya.", "-", SKRIP),
               ])
    tulis_meta(con, "transisi_konsesi",
               deskripsi=("Matriks transisi guna lahan per konsesi per kohort — BAHAN DIAGRAM SANKEY. Menjawab "
                          "'kelas ini berubah jadi apa' (RQ b: jalur menuju Lubang Tambang; RQ c: jalur lain). "
                          "Nama kelas TIDAK disimpan — JOIN ke mapbiomas_kelas (lihat v_transisi_aliran)."),
               sumber=sumber,
               metode=("Untuk tiap konsesi & pasangan tahun kohort: bincount(kelas_awal*100 + kelas_akhir) pada "
                       "piksel di dalam topeng; luas cos(lintang). Grid, topeng, rumus luas SAMA dgn "
                       "04_mapbiomas sehingga Σ transisi = Σ mapbiomas_tahunan kedua sisi (diassert di skrip). "
                       "Pasangan nodata<->nodata dibuang; nodata di satu sisi disimpan (tepi laut)."),
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("kode_wiup", "Kode WIUP konsesi — kunci ke tabel konsesi.", "-", "konsesi"),
                   ("label", "Nama kohort — kunci ke transisi_kohort.label.", "-", "transisi_kohort"),
                   ("tahun_awal", "Tahun kalender sisi asal. Untuk kohort umur BERBEDA antar konsesi (t0 + offset).", "-", SKRIP),
                   ("tahun_akhir", "Tahun kalender sisi tujuan.", "-", SKRIP),
                   ("kelas_awal", "Kode kelas MapBiomas di tahun_awal (0 = nodata/laut).", "-", "mapbiomas_kelas"),
                   ("kelas_akhir", "Kode kelas MapBiomas di tahun_akhir (0 = nodata/laut).", "-", "mapbiomas_kelas"),
                   ("piksel", "Cacah piksel yang menempuh transisi ini.", "bincount(kelas_awal*100 + kelas_akhir)", SKRIP),
                   ("ha", "Luas transisi (hektar). AWAS: aliran ke/dari Lubang Tambang = BATAS BAWAH.",
                    "Σ luas piksel, koreksi cos(lintang) (rumus di mapbiomas_tahunan.ha)", SKRIP),
               ])
    tulis_meta(con, "v_transisi_aliran",
               deskripsi=("VIEW agregat lintas konsesi per (label, kelas asal, kelas tujuan) dengan nama kelas — "
                          "bentuk yang langsung dipakai diagram Sankey di web. Baris dengan nodata (0) tak ikut "
                          "karena 0 bukan kelas legenda."),
               sumber="transisi_konsesi JOIN mapbiomas_kelas (DB ini)",
               metode="GROUP BY label, kelas_awal, kelas_akhir; n_konsesi = COUNT(DISTINCT kode_wiup). "
                      "Untuk Sankey: saring kelas 27 dan (opsional) kelas_awal = kelas_akhir.",
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("label", "Nama kohort.", "-", "transisi_kohort"),
                   ("kelas_awal", "Kode kelas asal.", "-", "mapbiomas_kelas"),
                   ("kelas_akhir", "Kode kelas tujuan.", "-", "mapbiomas_kelas"),
                   ("nama_awal", "Nama kelas asal (legenda resmi).", "JOIN mapbiomas_kelas", "mapbiomas_kelas"),
                   ("nama_akhir", "Nama kelas tujuan (legenda resmi).", "JOIN mapbiomas_kelas", "mapbiomas_kelas"),
                   ("n_konsesi", "Banyak konsesi yang punya aliran ini.", "COUNT(DISTINCT kode_wiup)", "transisi_konsesi"),
                   ("piksel", "Total piksel lintas konsesi.", "SUM(piksel)", "transisi_konsesi"),
                   ("ha", "Total luas lintas konsesi (hektar).", "ROUND(SUM(ha), 2)", "transisi_konsesi"),
               ])
    tulis_meta(con, "transisi_pasangan",
               deskripsi=("SEMUA pasangan tahun (a<b) dalam 2001-2024 (276 pasangan) — AGREGAT lintas konsesi, "
                          "tanpa kode_wiup. Menghidupi tampilan 'tahun dinamis': tiap langkah dibaca LANGSUNG "
                          "dari pasangannya. JANGAN menjumlahkan pita antar langkah — piksel berubah-lalu-kembali "
                          "akan terhitung berkali-kali."),
               sumber=sumber,
               metode=("Per konsesi: 24 raster dibaca sekali, 276 tabulasi silang dihitung dari larik di memori, "
                       "lalu dijumlahkan lintas konsesi. Pasangan nodata<->nodata dibuang."),
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("tahun_awal", "Tahun sisi asal.", "-", SKRIP),
                   ("tahun_akhir", "Tahun sisi tujuan (selalu > tahun_awal).", "-", SKRIP),
                   ("kelas_awal", "Kode kelas MapBiomas di tahun_awal (0 = nodata).", "-", "mapbiomas_kelas"),
                   ("kelas_akhir", "Kode kelas MapBiomas di tahun_akhir (0 = nodata).", "-", "mapbiomas_kelas"),
                   ("n_konsesi", "Banyak konsesi yang punya aliran ini pada pasangan tahun itu.", "COUNT konsesi", SKRIP),
                   ("piksel", "Cacah piksel lintas konsesi.", "Σ bincount per konsesi", SKRIP),
                   ("ha", "Luas lintas konsesi (hektar), dihitung LANGSUNG utk pasangan itu — bebas hitung ganda.",
                    "Σ luas piksel, koreksi cos(lintang)", SKRIP),
               ])


def main() -> int:
    ap = argparser("05 — transisi_kohort, transisi_konsesi, v_transisi_aliran, transisi_pasangan")
    a = ap.parse_args()
    con = buka(a.db)
    wajib_tabel(con, "konsesi", "mapbiomas_tahunan", "mapbiomas_kelas")
    mb.cek_himpunan(con, a.himpunan)
    mb.hash_geometri_cocok(con)
    berkas = mb.wajib_raster()

    konsesi_semua = mb.baca_konsesi(con)
    t0 = {k: max(t, mb.TAHUN_MIN) for k, _, t in konsesi_semua if t is not None}
    print(f"{len(konsesi_semua)} konsesi (himpunan {a.himpunan}); {len(t0)} punya tahun_izin")

    ref = mb.buka_raster_tercek(berkas)
    konsesi, luar = mb.siapkan_konsesi(konsesi_semua, ref)
    ref.close()
    kode_ada = [k for k, *_ in konsesi]
    kohort, tugas = susun_kohort(kode_ada, t0)
    for _, label, _, _, n, keluar, _ in kohort:
        print(f"  {label}: {n} masuk, {keluar} keluar")

    per_konsesi, pasangan = hitung(berkas, konsesi, tugas)

    # Koreksi n_konsesi kohort ke konsesi yang BENAR-BENAR berkontribusi baris. Konsesi yang
    # lolos saringan bbox tapi nol piksel saat topeng diterapkan (mis. WIUP PASIR LAUT yang
    # poligonnya di perairan, di luar cakupan raster darat) tak punya tutupan lahan teramati —
    # memasukkannya ke n_konsesi membuat angka kohort menjanjikan data yang tak ada.
    berkontribusi: dict[str, set[str]] = {}
    for baris in per_konsesi:
        berkontribusi.setdefault(baris[1], set()).add(baris[0])
    kohort_terkoreksi = []
    for jenis, label, aw, ak, n, keluar, catatan in kohort:
        n_nyata = len(berkontribusi.get(label, ()))
        if n_nyata != n:
            catatan += (f" {n - n_nyata} konsesi anggota kohort tak punya piksel MapBiomas "
                        "(poligon di luar cakupan raster darat) sehingga tak berkontribusi baris; "
                        "n_konsesi mencatat yang teramati saja.")
        kohort_terkoreksi.append((jenis, label, aw, ak, n_nyata, keluar, catatan))
    kohort = kohort_terkoreksi

    con.executescript(DDL)
    con.executemany("INSERT INTO transisi_kohort VALUES (?,?,?,?,?,?,?)", kohort)
    con.executemany("INSERT INTO transisi_konsesi VALUES (?,?,?,?,?,?,?,?)", per_konsesi)
    con.executemany("INSERT INTO transisi_pasangan VALUES (?,?,?,?,?,?,?)", pasangan)
    try:
        rekon = rekonsiliasi(con, kohort, tugas)
    except SystemExit:
        con.rollback()
        raise
    tulis_meta_semua(con)
    tandai_selesai(con, "05_transisi", n_konsesi=len(konsesi), n_tanpa_piksel=len(luar),
                   n_baris_konsesi=len(per_konsesi), n_baris_pasangan=len(pasangan),
                   n_pasangan_tahun=len(PASANGAN), himpunan=a.himpunan,
                   rekonsiliasi_selisih_maks_ha=f"{max(rekon.values()):.4f}")
    con.close()
    print(f"\nSelesai: {len(per_konsesi):,} baris transisi_konsesi, {len(kohort)} kohort, "
          f"{len(pasangan):,} baris transisi_pasangan ({len(PASANGAN)} pasangan) → {a.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
