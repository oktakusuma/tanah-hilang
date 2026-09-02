#!/usr/bin/env python3
"""Langkah 04 — komposisi guna lahan MapBiomas per konsesi per tahun (SKEMA.md §4).

MASALAH YANG DIJAWAB. Angka Hansen mengukur KAPAN tutupan pohon hilang di dalam konsesi,
tetapi tidak tahu lahan di dalam konsesi itu SEBENARNYA APA tiap tahun. Tabel di sini
menjawabnya: luas tiap kelas tutupan/penggunaan lahan MapBiomas Koleksi 4.1 di dalam tiap
konsesi, tiap tahun 2001–2024 (RQ b: kelas Lubang Tambang; RQ c: sawit = kelas 35 dan
kelas lain).

KELUARAN (semua milik skrip ini; di-drop & dibuat ulang → idempoten):
  mapbiomas_kelas     legenda resmi C4.1: kelas, nama, kelompok, hex (satu sumber utk web/QGIS)
  mapbiomas_gabungan  kategori buatan sendiri (Hutan / Pertanian non-sawit / Tubuh air) + keterangan
  mapbiomas_tahunan   kode_wiup × tahun × kelas → piksel, ha   (eks landuse_konsesi, tanpa fire)
  v_mapbiomas_ringkas view per konsesi × tahun: hutan/tambang/sawit/teramati/total
  bangun.mapbiomas.hash_geometri  sidik jari geometri konsesi yang dipakai (harus == konsesi.hash_geometri)

Logika di-port dari scripts/mapbiomas/landuse_konsesi.py (lihat pipeline/lib/w2_mapbiomas.py):
grid asli tanpa resampling, topeng dirasterisasi sekali per konsesi, luas cos(lintang).
Kelas 27 (awan) DISIMPAN apa adanya supaya pemakai hilir bisa memilih penyebut sendiri
(`teramati_ha` di view = tanpa 27). Nodata (0 = laut / luar Indonesia) tidak disimpan.

    python pipeline/04_mapbiomas.py --db data/tanah-hilang.db --himpunan minerba
Prasyarat: tabel `konsesi` (01_identitas.py) + raster data/external/mapbiomas/mapbiomas_c41_{2001..2024}.tif.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.lib import w2_mapbiomas as mb  # noqa: E402
from pipeline.lib.db import (argparser, buka, gagal, hash_geometri, tandai_selesai,  # noqa: E402
                             tulis_bangun, wajib_tabel)
from pipeline.lib.meta import LISENSI, tulis_meta, tulis_sumber  # noqa: E402

SKRIP = "pipeline/04_mapbiomas.py"

DDL = f"""
DROP VIEW  IF EXISTS v_mapbiomas_ringkas;
DROP TABLE IF EXISTS mapbiomas_tahunan;
DROP TABLE IF EXISTS mapbiomas_gabungan;
DROP TABLE IF EXISTS mapbiomas_kelas;
CREATE TABLE mapbiomas_kelas (
  kelas INTEGER PRIMARY KEY, nama TEXT NOT NULL, kelompok TEXT NOT NULL, hex TEXT NOT NULL
);
CREATE TABLE mapbiomas_gabungan (
  gabungan TEXT NOT NULL, kelas INTEGER NOT NULL REFERENCES mapbiomas_kelas(kelas), keterangan TEXT,
  PRIMARY KEY (gabungan, kelas)
);
CREATE TABLE mapbiomas_tahunan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup),
  tahun     INTEGER NOT NULL CHECK (tahun BETWEEN {mb.TAHUN_MIN} AND {mb.TAHUN_MAX}),
  kelas     INTEGER NOT NULL REFERENCES mapbiomas_kelas(kelas),
  piksel    INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, tahun, kelas)
);
"""
VIEW = f"""
CREATE VIEW v_mapbiomas_ringkas AS
  SELECT kode_wiup, tahun,
         SUM(CASE WHEN kelas IN {mb.KELAS_HUTAN} THEN ha ELSE 0 END)   AS hutan_ha,
         SUM(CASE WHEN kelas = {mb.KELAS_TAMBANG} THEN ha ELSE 0 END)   AS tambang_ha,
         SUM(CASE WHEN kelas = {mb.KELAS_SAWIT} THEN ha ELSE 0 END)     AS sawit_ha,
         SUM(CASE WHEN kelas <> {mb.KELAS_AWAN} THEN ha ELSE 0 END)     AS teramati_ha,
         SUM(ha) AS total_ha
  FROM mapbiomas_tahunan GROUP BY kode_wiup, tahun;
"""


def hitung_tahunan(berkas: dict[int, Path], konsesi) -> list[tuple]:
    """Baris (kode_wiup, tahun, kelas, piksel, ha) untuk semua konsesi × tahun."""
    baris: list[tuple] = []
    piksel_mask = {kode: int(mask.sum()) for kode, _, mask, _ in konsesi}
    tak_dikenal: set[int] = set()
    mulai = time.time()
    for t in mb.TAHUN:
        with rasterio.open(berkas[t]) as ds:
            for kode, win, mask, area in konsesi:
                vals = ds.read(1, window=win)[mask]
                areas = area[mask]
                px = np.bincount(vals, minlength=mb.NKELAS)
                ha = np.bincount(vals, weights=areas, minlength=mb.NKELAS)
                total_px = 0
                for c in np.nonzero(px)[0]:
                    c = int(c)
                    if c == mb.KELAS_NODATA:
                        continue                       # laut / luar Indonesia
                    if c not in mb.KODE_LEGENDA:
                        tak_dikenal.add(c)
                    baris.append((kode, t, c, int(px[c]), round(float(ha[c]), 4)))
                    total_px += int(px[c])
                # Invarian per tahun: piksel berkelas + nodata = piksel topeng.
                if total_px + int(px[mb.KELAS_NODATA]) != piksel_mask[kode]:
                    gagal(f"tabulasi bocor: {kode} tahun {t}")
        print(f"  {t}: {len(baris):,} baris kumulatif ({time.time() - mulai:,.0f} s)", flush=True)
    if tak_dikenal:
        gagal(f"kode kelas di luar legenda C4.1 muncul di raster: {sorted(tak_dikenal)} — "
              "perbarui LEGENDA di pipeline/lib/w2_mapbiomas.py setelah dicek ke legenda resmi")
    return baris


def tulis_meta_semua(con) -> None:
    lis = LISENSI["mapbiomas"]
    tulis_meta(con, "mapbiomas_kelas",
               deskripsi=("Legenda resmi MapBiomas Indonesia Koleksi 4.1 (nama Indonesia, kelompok "
                          "level-1, warna hex) — satu sumber untuk web, QGIS, dan tabel turunan. "
                          "Kelas 10 dan 27 ikut disimpan walau tak pernah muncul di konsesi Kalimantan."),
               sumber="Legenda resmi C4.1 (landy.mapbiomas.id/legendcode); hex = webapp/src/lib/mapbiomasLegend.ts",
               metode="Konstanta LEGENDA di pipeline/lib/w2_mapbiomas.py, ditulis ulang tiap run; "
                      "kode kelas raster yang tak ada di sini membuat skrip gagal keras.",
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("kelas", "Kode kelas MapBiomas (nilai piksel raster apa adanya).", "-", "legenda C4.1"),
                   ("nama", "Nama kelas resmi berbahasa Indonesia.", "-", "legenda C4.1"),
                   ("kelompok", "Kelompok level-1 legenda: Hutan / Tumbuhan Non-Hutan / Pertanian / "
                                "Non-Vegetasi / Tubuh Air / Citra Tertutup Awan.", "-", "legenda C4.1"),
                   ("hex", "Warna legenda (#rrggbb) — dipakai peta web & simbologi QGIS.", "-",
                    "webapp/src/lib/mapbiomasLegend.ts"),
               ])
    tulis_meta(con, "mapbiomas_gabungan",
               deskripsi=("Definisi kategori GABUNGAN buatan sendiri (BUKAN kelas resmi MapBiomas): "
                          "'Hutan' = 3+5+76, 'Pertanian non-sawit' = 9+21+40, 'Tubuh air' = 31+33. "
                          "Disimpan di DB supaya setiap pemakaian bisa diberi keterangan (kaveat wajib)."),
               sumber="Keputusan analisis tesis (docs/landasan-teori-dan-arah-tulisan.md §13)",
               metode="Konstanta GABUNGAN di pipeline/lib/w2_mapbiomas.py; cara pakai: "
                      "JOIN mapbiomas_tahunan.kelas = mapbiomas_gabungan.kelas lalu SUM(ha) GROUP BY gabungan.",
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("gabungan", "Nama kategori gabungan (Hutan / Pertanian non-sawit / Tubuh air).", "-", SKRIP),
                   ("kelas", "Kode kelas resmi yang menjadi anggota kategori ini.", "-", "mapbiomas_kelas"),
                   ("keterangan", "Kaveat yang WAJIB ikut tampil bersama angka kategori ini.", "-", SKRIP),
               ])
    tulis_meta(con, "mapbiomas_tahunan",
               deskripsi=("Komposisi kelas tutupan/penggunaan lahan MapBiomas per konsesi per tahun "
                          f"({mb.TAHUN_MIN}-{mb.TAHUN_MAX}): apa isi konsesi tiap tahun — termasuk Lubang "
                          "Tambang (30) dan Sawit (35). Pelengkap Hansen, BUKAN pengganti angka kehilangan "
                          "tutupan pohon. Nodata (0) tidak disimpan; kelas 27 (awan) disimpan apa adanya."),
               sumber=mb.SUMBER_RINGKAS,
               metode=("Tabulasi piksel (bincount) di grid asli EPSG:4326 TANPA resampling; topeng poligon "
                       "all_touched=False dirasterisasi sekali per konsesi; luas = jumlah luas piksel "
                       "terkoreksi lintang cos(lat) dengan DEG_LAT_METERS dari scripts/_geo_common.py "
                       "(identik pipeline Hansen). Geometri = tabel konsesi DB ini (sidik jari di "
                       "bangun.mapbiomas.hash_geometri). Cara reproduksi: "
                       "python pipeline/04_mapbiomas.py --db <db> --himpunan <minerba|lengkap>."),
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("kode_wiup", "Kode WIUP konsesi — kunci ke tabel konsesi.", "-", "konsesi"),
                   ("tahun", f"Tahun peta MapBiomas ({mb.TAHUN_MIN}-{mb.TAHUN_MAX}; peta berhenti 2024).", "-", mb.SUMBER_RINGKAS),
                   ("kelas", "Kode kelas MapBiomas (lihat mapbiomas_kelas).", "nilai piksel raster apa adanya", mb.SUMBER_RINGKAS),
                   ("piksel", "Jumlah piksel kelas ini di dalam topeng konsesi pada tahun itu.", "bincount(raster[topeng])", SKRIP),
                   ("ha", "Luas kelas ini (hektar) di dalam konsesi pada tahun itu. Kaveat: kelas 30 "
                          "(Lubang Tambang) dibaca sebagai BATAS BAWAH — akurasinya tak dipublikasikan MapBiomas.",
                    "SUM(luas piksel), luas piksel = px_x*px_y*(111.320 m/derajat)^2*cos(lintang)/10^4", SKRIP),
               ])
    tulis_meta(con, "v_mapbiomas_ringkas",
               deskripsi=("VIEW ringkas per konsesi × tahun: hutan (3+5+76) / lubang tambang (30) / "
                          "sawit (35) / teramati (tanpa awan 27) / total — pintu masuk tercepat sebelum "
                          "menyelam ke mapbiomas_tahunan. Kategori 'hutan' = gabungan buatan (lihat mapbiomas_gabungan)."),
               sumber="mapbiomas_tahunan (DB ini)",
               metode="SUM(CASE) per kelas, GROUP BY kode_wiup, tahun. teramati_ha = PENYEBUT yang "
                      "disarankan untuk pangsa, supaya pangsa tak bergerak hanya karena tutupan awan.",
               skrip=SKRIP, lisensi=lis,
               kolom=[
                   ("kode_wiup", "Kode WIUP konsesi.", "-", "mapbiomas_tahunan"),
                   ("tahun", "Tahun peta MapBiomas.", "-", "mapbiomas_tahunan"),
                   ("hutan_ha", "Luas hutan alam (Formasi Hutan + Mangrove + Hutan Rawa Gambut). BEDA definisi "
                                "dari 'tutupan pohon' Hansen.", "SUM(ha) utk kelas IN (3,5,76)", "mapbiomas_tahunan"),
                   ("tambang_ha", "Luas Lubang Tambang (kelas 30) — batas bawah.", "SUM(ha) utk kelas = 30", "mapbiomas_tahunan"),
                   ("sawit_ha", "Luas Sawit (kelas 35) — stok sawit di dalam konsesi.", "SUM(ha) utk kelas = 35", "mapbiomas_tahunan"),
                   ("teramati_ha", "Luas semua kelas KECUALI 27 (awan) — penyebut yang disarankan untuk pangsa.",
                    "SUM(ha) utk kelas <> 27", "mapbiomas_tahunan"),
                   ("total_ha", "Luas semua kelas berkode (termasuk awan; tanpa nodata) — mendekati luas poligon.",
                    "SUM(ha)", "mapbiomas_tahunan"),
               ])


def main() -> int:
    ap = argparser("04 — mapbiomas_tahunan + mapbiomas_kelas + mapbiomas_gabungan + v_mapbiomas_ringkas")
    a = ap.parse_args()
    con = buka(a.db)
    wajib_tabel(con, "konsesi")
    mb.cek_himpunan(con, a.himpunan)
    berkas = mb.wajib_raster()

    konsesi_semua = mb.baca_konsesi(con)
    sidik = hash_geometri(con)
    print(f"{len(konsesi_semua)} konsesi (himpunan {a.himpunan}), {len(mb.TAHUN)} tahun "
          f"{mb.TAHUN_MIN}-{mb.TAHUN_MAX}; hash geometri {sidik[:12]}…")

    ref = mb.buka_raster_tercek(berkas)
    konsesi, luar = mb.siapkan_konsesi(konsesi_semua, ref)
    print(f"{len(konsesi)} konsesi punya piksel; grid {ref.width}x{ref.height}", flush=True)
    ref.close()

    baris = hitung_tahunan(berkas, konsesi)

    # ── Tulis (satu transaksi: gagal di tengah → DB tak berubah) ──────────────────────
    con.executescript(DDL)
    con.executemany("INSERT INTO mapbiomas_kelas VALUES (?,?,?,?)", mb.LEGENDA)
    con.executemany("INSERT INTO mapbiomas_gabungan VALUES (?,?,?)", mb.GABUNGAN)
    con.executemany("INSERT INTO mapbiomas_tahunan VALUES (?,?,?,?,?)", baris)
    con.executescript(VIEW)
    tulis_meta_semua(con)
    tulis_sumber(con, "mapbiomas", "MapBiomas Indonesia — peta tahunan tutupan & penggunaan lahan",
                 "CC BY-SA (versi tak disebut penerbit)", versi="Koleksi 4.1", url=mb.URL_MAPBIOMAS,
                 tanggal_akses=mb.tanggal_akses().isoformat(), cakupan_tahun=f"{mb.TAHUN_MIN}-{mb.TAHUN_MAX}",
                 sitasi=mb.sitasi(),
                 catatan=("Koleksi mencakup 2000-2024; pipeline memakai 2001-2024 (jendela tesis). Tabel "
                          "turunan = Adapted Material (tabulasi di dalam poligon konsesi; tak ada nilai "
                          "kelas yang diubah). Akurasi kelas Lubang Tambang tak dipublikasikan → batas bawah."))
    # Sidik jari geometri: WAJIB sama dengan bangun.konsesi.hash_geometri (dicek 05 & 10).
    tulis_bangun(con, "mapbiomas.hash_geometri", sidik)
    tandai_selesai(con, "04_mapbiomas", n_konsesi=len(konsesi), n_tanpa_piksel=len(luar),
                   n_baris=len(baris), tahun=f"{mb.TAHUN_MIN}-{mb.TAHUN_MAX}", himpunan=a.himpunan)
    con.close()
    print(f"\nSelesai: {len(baris):,} baris mapbiomas_tahunan, {len(mb.LEGENDA)} kelas, "
          f"{len(mb.GABUNGAN)} baris gabungan → {a.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
