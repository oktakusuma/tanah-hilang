# Tanah yang Hilang — Reproduksi Data (mentah → jadi)

Paket ini berisi **data sumber publik mentah + skrip pipeline** untuk membangun ulang
basis data akhir tesis *Tanah yang Hilang* **dari nol** — analisis spasial guna lahan &
deforestasi di dalam konsesi tambang Kalimantan.

Hasil akhirnya **dua** berkas SQLite dengan skema identik:

| Berkas | Isi | Jumlah konsesi |
|---|---|---|
| `data/tanah-hilang.db` | **himpunan minerba** — batubara + mineral logam. Inilah yang dibaca web app & dipakai tesis | **825** |
| `data/tanah-hilang-lengkap.db` | **himpunan lengkap** — semua WIUP, termasuk galian C / mineral bukan logam & batuan | **1.765** |

**Jendela analisis = 2001–2024** dan tertanam di skema: tidak ada kolom 2025, 2009,
maupun sawit Descals. Nama tabel & kolom berbahasa Indonesia. Kontrak skemanya —
tabel per tabel, kolom per kolom, lengkap dengan angka jangkar yang wajib direproduksi —
ada di **`pipeline/SKEMA.md`**; baca itu bila ingin memastikan hasil Anda sama.

Tiga sumber angka: **Hansen GFC v1.13** (kehilangan tutupan pohon), **MapBiomas
Indonesia C4.1** (ragam guna lahan, termasuk lubang tambang & sawit), dan **poligon WIUP
Geoportal ESDM** (+ registri MinerbaOne untuk identitas perusahaan).

> **Catatan:** paket ini berisi **data mentah hasil scrape yang sudah jadi** + **skrip
> pengolahan**. Skrip *scraper*-nya (penarik data MinerbaOne & Geoportal WIUP) tidak
> disertakan — tetapi endpoint API-nya didokumentasikan di **Lampiran A** bila Anda ingin
> menarik ulang sendiri.
>
> Paket ini juga **tidak** menyertakan `stata/` (panel penelitian tesis, belum
> dipublikasikan) maupun basis data jadi. Semua langkah di bawah berjalan tanpa keduanya.

---

## 1. Struktur folder

```
Tanah Hilang/
├── data/                                 # INPUT mentah (yang kecil sudah disertakan)
│   ├── minerba-kalimantan.db             #   MinerbaOne: 7.572 badan usaha + 8.461 izin
│   ├── kepadatan_penduduk.csv            #   BPS: kepadatan 56 kab/kota, 2015–2024
│   │                                     #   (bentuk lebar d2015..d2024; di-unpivot saat ingest)
│   ├── wiup/
│   │   ├── kalimantan_raw.geojson        #   snapshot WIUP dari Geoportal (1.765)
│   │   └── kalimantan_unique.geojson     #   input kanonik pipeline — lihat ralat di bawah
│   ├── geoportal/
│   │   └── MANIFEST.csv                  #   jumlah fitur + MD5 + tanggal + kueri untuk
│   │                                     #   ketiga geojson kehutanan yang harus diunduh
│   └── boundaries/
│       └── kalimantan-kabupaten.geojson  #   batas kabupaten (geoBoundaries)
├── pipeline/                             # PIPELINE v3 — inilah yang dijalankan
│   ├── bangun-bundel.sh                  #   PEMBUNGKUS untuk bundel ini (pakai ini)
│   ├── bangun.sh                         #   orkestrator 10 langkah (identik repo pengembangan)
│   ├── tests/                            #   pytest pipeline (sebagian auto-skip tanpa DB arsip)
│   ├── 00_prasyarat.py … 10_verifikasi.py
│   ├── lib/                              #   pustaka bersama (db, meta, himpunan, w1…w4)
│   └── SKEMA.md                          #   kontrak skema + angka jangkar
├── scripts/                              # skrip penunjang & pipeline ARSIP v2 (§7)
│   ├── mapbiomas/                        #   unduhan raster MapBiomas + tile + skrip arsip
│   └── gee/                              #   komposit citra sezaman 2009–2018 (opsional)
└── README.md                             # berkas ini
```

> **Ralat label `kalimantan_unique.geojson`:** berkas ini **byte-identik** dengan
> `kalimantan_raw.geojson` (`cmp data/wiup/kalimantan_raw.geojson
> data/wiup/kalimantan_unique.geojson`). Langkah "dedup `kode_wiup`" ternyata **no-op** —
> snapshot mentah sudah unik per `kode_wiup`. Nama berkas dipertahankan karena pipeline
> membacanya sebagai input kanonik; label lama "WIUP unik (dedup)" memberi kesan ada
> penyaringan yang sebenarnya tidak terjadi.

**Tidak** disertakan karena besar atau karena murni turunan — semuanya bisa diunduh /
dihasilkan ulang (lihat §2):

- raster Hansen (~1,4 GB), raster MapBiomas C4.1 (~5,25 GB), geojson kehutanan
  Geoportal (~51 MB), `data/analysis/batch_*.csv` (hasil `scripts/batch_analyze.py`),
  dan tentu saja `data/tanah-hilang*.db` itu sendiri.

---

## 2. Prasyarat

### 2a. Lingkungan Python

- **Python 3.14** — lingkungan kanonik yang menghasilkan angka tesis (freeze `pip`
  2026-08-06); 3.11+ kemungkinan besar tetap jalan, tetapi presisi byte-per-byte hanya
  terdokumentasi terhadap 3.14.
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python -c "import rasterio, numpy, shapely; print('env OK')"
  ```
  Pipeline v3 hanya butuh **rasterio + numpy + shapely** (unduhan memakai `urllib`
  pustaka standar). `scipy`/`matplotlib` di `requirements.txt` hanya untuk skrip di luar
  pipeline; lihat komentar di berkas itu.

  > **Catatan drift:** versi pustaka geospasial lain (rasterio/GEOS) tetap bisa
  > menjalankan pipeline ini, tetapi hasilnya bisa bergeser sedikit di keputusan piksel
  > tepi poligon — pada snapshot yang di-commit efeknya di bawah ±2 ha, tidak mengubah
  > kesimpulan mana pun.

Jalankan semua perintah **dari akar folder ini**. Skrip pipeline tidak memakai path
hard-coded: semua input dicari relatif ke akar repo lewat `pipeline/lib/db.AKAR`, jadi
cwd sebenarnya bebas — tetapi contoh-contoh di bawah mengasumsikan akar.

### 2b. Data yang harus diunduh sendiri

| Input | Ukuran | Cara mendapatkan |
|---|---|---|
| `data/raster/Hansen_GFC-2025-v1.13_{lossyear,treecover2000}_{00N,10N}_{100E,110E}.tif` | ~1,4 GB | `python scripts/download_hansen.py --kalimantan-all` (bisa di-resume) |
| `data/external/mapbiomas/mapbiomas_c41_{2001..2024}.tif` | ~5,25 GB | `python scripts/mapbiomas/fetch_mapbiomas_lulc.py` (ukuran + MD5 diverifikasi terhadap `scripts/mapbiomas/manifest_c41_2000_2024.csv` yang disertakan) |
| `data/geoportal/{ippkh_eksplorasi,ippkh_operasi,overlay_hutan}.geojson` | ~51 MB | `python scripts/fetch_geoportal_hutan.py` — cocokkan hasilnya dengan `data/geoportal/MANIFEST.csv` (fitur + MD5 + kueri) yang disertakan |
| `data/analysis/batch_KALIMANTAN_t30_wide.csv` | ~0,5 MB | `python scripts/batch_analyze.py --province KALIMANTAN --threshold 30` — **butuh raster Hansen**, dan inilah langkah paling lama (overlay 1.765 poligon × raster) |

`overlay_hutan.geojson` dibutuhkan **sejak langkah 01** (tanggal berlaku/berakhir izin
dipulihkan dari layer itu), bukan hanya oleh langkah 06.

Catatan `batch_analyze.py`: dijalankan langsung dari `data/wiup/kalimantan_unique.geojson`
**mentah, untuk SEMUA 1.765 WIUP** — bukan dari himpunan yang sudah disaring. Ini
disengaja: tiap konsesi diukur berdiri sendiri dari poligonnya sendiri, sehingga hasil
yang sama bisa dipakai kedua himpunan tanpa memindai raster dua kali. Metodenya:
rasterisasi poligon → filter kanopi ≥30% (baseline hutan 2000) → decode `lossyear` →
koreksi luas piksel per lintang (~0,0774 ha).

### 2c. Periksa kelengkapan sebelum mulai

```bash
python pipeline/00_prasyarat.py            # ringkas, exit 2 bila ada yang kurang
python pipeline/00_prasyarat.py --json     # keluaran mesin (daftar berkas yang kurang)
python pipeline/00_prasyarat.py --tanpa-md5   # lewati verifikasi MD5 raster MapBiomas (lambat)
```

Pipeline v3 **gagal keras** bila prasyarat inti absen — tidak ada lagi langkah yang
diam-diam dilewati lalu `exit 0` seperti pipeline lama.

---

## 3. Menjalankan pipeline

```bash
bash pipeline/bangun-bundel.sh             # ← PAKAI INI di bundel: 01-10 + statistik ke data/
bash pipeline/bangun.sh                    # kedua himpunan, dari nol (DB lama dihapus)
bash pipeline/bangun.sh --himpunan minerba # satu himpunan saja
bash pipeline/bangun.sh --dari 04          # lanjut dari langkah 04 (DB sudah ada)
bash pipeline/bangun.sh --sampai 08        # berhenti setelah langkah 08
bash pipeline/bangun.sh --tanpa-md5        # lewati verifikasi MD5 raster MapBiomas
```

`bangun.sh` memakai `.venv/bin/python` bila ada, selain itu `python3` (bisa ditimpa
dengan variabel lingkungan `PYTHON`).

Langkah **01–08** dijalankan **per himpunan**; **09** sekali (JSON memuat kedua himpunan,
geojson dari minerba); **10** per himpunan.

| # | Skrip | INPUT | OUTPUT (tabel / berkas) |
|---|---|---|---|
| 00 | `00_prasyarat.py` | raster Hansen, raster MapBiomas + manifest MD5, geojson Geoportal + `MANIFEST.csv`, `wiup/kalimantan_unique.geojson`, `minerba-kalimantan.db`, `kepadatan_penduduk.csv`, batch CSV | tidak menulis apa pun — **gagal keras** + daftar yang kurang |
| 01 | `01_identitas.py` | geojson WIUP (1.765; disaring `lib/himpunan.py`), `minerba-kalimantan.db` (`perizinan`, `badan_usaha`), `kepadatan_penduduk.csv`, `geoportal/overlay_hutan.geojson` | `sumber`, `bangun` (hash geometri, n konsesi), **`konsesi`**, **`konsesi_registri`** (SK-persis + pencocokan T1–T4), `kepadatan_penduduk` |
| 02 | `02_hansen.py` | `analysis/batch_KALIMANTAN_t30_wide.csv`, `konsesi` | **`hansen_ringkas`**, **`hansen_tahunan`** (2001–2024), **`izin_laju`** (laju pra/pasca tahun izin + vonis) |
| 03 | `03_izin.py` | `konsesi`, `konsesi_registri`, `izin_laju` | **`izin_klasifikasi`** (IZIN_PERTAMA / PERPANJANGAN / TAK_DINILAI × KUAT / INDIKASI) |
| 04 | `04_mapbiomas.py` | raster MapBiomas 2001–2024, geometri **dari tabel `konsesi` DB target** | **`mapbiomas_kelas`**, **`mapbiomas_gabungan`**, **`mapbiomas_tahunan`**, view `v_mapbiomas_ringkas`; menulis `bangun.mapbiomas.hash_geometri` |
| 05 | `05_transisi.py` | raster MapBiomas, `konsesi.tahun_izin` | **`transisi_kohort`**, **`transisi_konsesi`**, **`transisi_pasangan`** (276 pasangan tahun), view `v_transisi_aliran` — bahan diagram Sankey |
| 06 | `06_kawasan_hutan.py` | `geoportal/*.geojson` + `MANIFEST.csv`, `konsesi` | **`kawasan_hutan`**, **`ippkh`**, **`ippkh_irisan`** |
| 07 | `07_umur_izin.py` | `konsesi.tahun_izin`, `hansen_ringkas`, `hansen_tahunan` | **`umur_izin_kurun`**, **`umur_izin_tahunan`**, **`umur_izin_konsesi`** |
| 08 | `08_keyakinan.py` | `konsesi`, `izin_klasifikasi`, `ippkh`, `hansen_tahunan`, `mapbiomas_tahunan` kelas 30, `konsesi_registri` | **`keyakinan_pra_izin`**, **`keyakinan_model`**, **`keyakinan_ringkas`** |
| 09 | `09_sajikan.py` | kedua DB | `data/wiup/kalimantan_with_loss.geojson` (untuk QGIS) + `dashboard-stats.json`; memastikan view `v_konsesi` ada |
| 10 | `10_verifikasi.py` | DB + JSON | tidak menulis; **exit ≠ 0 bila FAIL** |

**Urutan wajib:** 01 sebelum semua; 02 sebelum 03/07/08; 04 sebelum 05 & 08; 03 & 06
sebelum 08; 09 setelah 01–08; 10 terakhir. **Jangan menarik ulang geometri WIUP tanpa
membangun ulang langkah 04–05** — dijaga invarian `bangun.mapbiomas.hash_geometri ==
bangun.konsesi.hash_geometri` (tabel MapBiomas pernah tertinggal satu generasi geometri
tanpa ketahuan).

Perkiraan durasi dari nol, di luar unduhan: `batch_analyze.py` ± jam, MapBiomas 04+05
± 1–2 jam per himpunan, sisanya menit.

### Dua catatan kecil untuk pemakai bundel ini

1. **`dashboard-stats.json`.** Secara bawaan langkah 09 menulisnya ke
   `webapp/src/generated/dashboard-stats.json` (lokasi di repo web app). Bundel ini tidak
   memuat web app, jadi folder itu akan dibuat kosong. Bila ingin rapi, jalankan langkah
   09 sendiri dengan path eksplisit:
   ```bash
   python pipeline/09_sajikan.py --db data/tanah-hilang.db \
          --db-lengkap data/tanah-hilang-lengkap.db \
          --geojson data/wiup/kalimantan_with_loss.geojson \
          --stats data/dashboard-stats.json
   ```
2. **Paritas arsip.** `bangun.sh` memanggil langkah 10 dengan
   `--arsip data/arsip/kalimantan.db --arsip-mapbiomas data/arsip/mapbiomas.db`. Basis
   data arsip itu **tidak** disertakan di bundel (hasil pipeline lama, bukan data mentah),
   jadi pemeriksaan paritas ke-12 otomatis dilewati. Sebelas kelompok pemeriksaan lainnya
   tetap berjalan penuh:
   ```bash
   python pipeline/10_verifikasi.py --db data/tanah-hilang.db --himpunan minerba \
          --stats data/dashboard-stats.json
   ```
   Yang diperiksa: skema = `SKEMA.md`, `analysis_meta`/`column_meta` 100% dua arah,
   Σ `hansen_tahunan` = `hilang_2001_2024_ha`, konsistensi `pct_hutan_2000`, identitas
   pra/pasca `izin_laju`, hash geometri MapBiomas = konsesi, Σ transisi = Σ tahunan
   (kedua sisi, tiap label), 276 pasangan tahun, `n_konsesi` > 0 di tiap titik umur izin,
   batas `keyakinan_ringkas`, dan `dashboard-stats.json` = DB.

### Angka jangkar (himpunan minerba) — patokan bahwa hasil Anda benar

| Angka | Nilai | Sumber |
|---|---|---|
| Konsesi | 825 | `konsesi` |
| Σ kehilangan tutupan pohon 2001–2024 | **1.548.812,60 ha** | `hansen_ringkas` |
| Hutan 2000 (kanopi ≥30%) | 3.943.141 ha → **39,3%** hilang | `hansen_ringkas` |
| Sankey 2001→2024, total pita berubah kelas | 1.270.225,42 ha | `transisi_konsesi` |
| Lubang tambang (kelas 30) 2024 | 157.287,46 ha | `mapbiomas_tahunan` |
| Kehilangan yang diatribusikan ke masa izin (harapan) | 1.189.114,10 ha | `keyakinan_ringkas` |
| Konsesi ber-IPPKH aktif / IPPKH tambang | 274 / 256 | `ippkh` |

Daftar lengkap angka jangkar per bagian ada di `pipeline/SKEMA.md`.

---

## 4. Dua himpunan (kenapa?)

Layer Geoportal `WIUP_Publish` memuat **semua** WIUP, termasuk **galian C / batuan**
(pasir kuarsa, andesit, batu gamping, tanah urug) yang sering **bukan di kawasan hutan**.
Untuk analisis deforestasi tambang fokusnya mineral & batubara, jadi pipeline membangun
dua berkas: `tanah-hilang.db` (825, dipakai tesis) dan `tanah-hilang-lengkap.db` (1.765,
angka konteks). Daftar komoditas yang dipertahankan ada di
`scripts/filter_minerba.py::MINERBA_COMMODITIES` dan diimpor apa adanya oleh
`pipeline/lib/himpunan.py` supaya tidak ada dua daftar yang bisa berbeda: batubara +
bauksit, emas, bijih besi, besi, zirkon, timah, mangan, antimoni, intan (13 nilai
termasuk varian DMP).

**Kaveat himpunan lengkap:** 8 konsesi galian C berkomoditas pasir laut / pasir kuarsa
punya **nol piksel MapBiomas** karena poligonnya di perairan, di luar cakupan raster
darat. Itu bukan galat; rekonsiliasi langkah 05 memang hanya membandingkan anggota kohort
yang punya piksel.

---

## 5. Hasil akhir — isi `tanah-hilang.db`

Setiap tabel membawa **lisensinya sendiri** di kolom `analysis_meta.lisensi`, dan setiap
sumber data tercatat di tabel `sumber` (nama, versi, lisensi, URL, tanggal akses, teks
sitasi). Itulah sebabnya turunan Hansen (CC BY 4.0) dan turunan MapBiomas (**CC BY-SA
4.0**, ShareAlike) boleh berada di satu berkas: satu file SQLite hanyalah kumpulan tabel.

| Tabel / view | Isi | Lisensi turunan |
|---|---|---|
| `konsesi` | identitas + geometri konsesi (kode WIUP, nama usaha, SK, komoditas, luas, `tahun_izin`, tanggal berlaku/berakhir, provinsi/kabupaten) | data publik pemerintah (Geoportal ESDM) |
| `konsesi_registri` | hasil pencocokan ke registri MinerbaOne (strategi T0–T3, NIB, NPWP, alamat, URL) | data publik pemerintah |
| `kepadatan_penduduk` | BPS 56 kab/kota × 2015–2024, bentuk long | BPS |
| `hansen_ringkas` | per konsesi: hutan 2000, `hilang_2001_2024_ha`, `pct_hutan_2000`, tahun puncak, tile | CC BY 4.0 (Hansen/GFC) |
| `hansen_tahunan` | per konsesi × tahun 2001–2024: `hilang_ha` | CC BY 4.0 |
| `izin_laju` | laju kehilangan sebelum vs sesudah `tahun_izin` + vonis (`accelerated_post_iup`, `decelerated_post_iup`, `loss_only_after_iup`, `stable`, `no_loss_either`, `izin_setelah_jendela`, `tanpa_tahun_izin`) | CC BY 4.0 |
| `izin_klasifikasi` | apakah `tahun_izin` tampak izin PERTAMA atau PERPANJANGAN, + kekuatan bukti | turunan campuran |
| `mapbiomas_kelas` / `mapbiomas_gabungan` | legenda resmi C4.1 + kategori gabungan buatan sendiri (mis. "Pertanian non-sawit" = kelas 9+21+40) yang **wajib diberi keterangan** saat dipakai | CC BY-SA 4.0 |
| `mapbiomas_tahunan` (+ view `v_mapbiomas_ringkas`) | komposisi kelas guna lahan per konsesi × tahun × kelas (piksel & ha) | **CC BY-SA 4.0** |
| `transisi_kohort` / `transisi_konsesi` / `transisi_pasangan` (+ view `v_transisi_aliran`) | aliran guna lahan kelas asal → kelas tujuan di dalam konsesi (bahan Sankey); `transisi_pasangan` memuat 276 pasangan tahun dan **tidak boleh dijumlahkan antar langkah** | **CC BY-SA 4.0** |
| `kawasan_hutan` / `ippkh` / `ippkh_irisan` | fungsi kawasan hutan di dalam konsesi; potret IPPKH **aktif** (batas bawah, bukan register sejarah) + audit irisan spasial vs kecocokan nama | data publik pemerintah |
| `umur_izin_kurun` / `umur_izin_tahunan` / `umur_izin_konsesi` | laju kehilangan menurut **umur izin** (bukan tahun kalender), dua rancangan kohort: `A_seimbang` & `B_semua` | CC BY 4.0 |
| `keyakinan_pra_izin` / `keyakinan_model` / `keyakinan_ringkas` | peluang terkalibrasi bahwa sebuah konsesi **sudah beroperasi sebelum `tahun_izin`**, plus atribusi kehilangan berikut selang bootstrap-nya | turunan campuran |
| view `v_konsesi` | gabungan semua di atas per konsesi — satu sumber untuk daftar & panel detail | — |
| `sumber`, `bangun`, `analysis_meta`, `column_meta` | provenansi: sumber & lisensi, jejak build (tanggal, versi pipeline, commit git, **sidik jari geometri konsesi**), deskripsi + rumus tiap tabel dan tiap kolom | — |

Melacak asal-usul angka apa pun:

```bash
sqlite3 data/tanah-hilang.db "SELECT nama_tabel, lisensi, sumber, metode, skrip FROM analysis_meta"
sqlite3 data/tanah-hilang.db "SELECT nama_kolom, deskripsi, rumus FROM column_meta WHERE nama_tabel='izin_laju'"
sqlite3 data/tanah-hilang.db "SELECT * FROM sumber"
sqlite3 data/tanah-hilang.db "SELECT kunci, nilai FROM bangun"
```

Cakupan `analysis_meta` + `column_meta` dituntut **100% dua arah** terhadap
`PRAGMA table_info` oleh `pipeline/10_verifikasi.py` — tiap kolom punya keterangan, dan
tiap keterangan punya kolom.

---

## 6. Opsional — di luar pipeline angka

### 6a. Tile MapBiomas untuk peta web

```bash
# dari raster yang sudah diunduh (§2b)
python scripts/mapbiomas/gen_mapbiomas_tiles.py \
       --src data/external/mapbiomas --pattern 'mapbiomas_c41_*.tif' --tahun 2001-2024
# atau langsung dari COG publik, tanpa mengunduh raster penuh
python scripts/mapbiomas/gen_mapbiomas_tiles.py --remote --tahun 2001-2024 --zmax 10
```
→ `data/tiles/mapbiomas/{tahun}/{z}/{x}/{y}.png`, kanal R = kode kelas, warna dipetakan di
sisi klien. ⚠ Tile **data** wajib **lossless**: konversi WebP lossy merusak kode kelas
di kanal R. Ini latar visual, bukan sumber angka.

### 6b. Citra dasar sezaman 2009–2018 (`scripts/gee/`)

Komposit citra satelit sezaman untuk latar halaman peta — menjawab "seperti apa lokasi
ini saat izinnya terbit". **Bukan** sumber angka apa pun.

> **Prasyarat:** akun **Google Earth Engine** sendiri (tier nonkomersial gratis cukup) +
> kunci *service account* di `.secrets/gee-sa.json`. Bundel ini tidak menyertakan kunci
> apa pun. **Lisensi sumber:** Landsat Collection 2 (USGS/NASA) domain publik; Copernicus
> Sentinel-2 (ESA) terbuka. Atribusi wajib: *USGS/NASA Landsat 5/7/8 Collection 2 ·
> Copernicus Sentinel-2 (ESA) · Cloud Score+ · Google Earth Engine*.

```bash
pip install earthengine-api          # tidak ada di requirements.txt

python scripts/gee/komposit_landsat.py  --tahun 2011 --bbox 117.30,0.25,117.80,0.75
python scripts/gee/komposit_sentinel.py --tahun 2017 --bbox 117.30,0.25,117.80,0.75
python scripts/gee/gen_landsat_tiles.py --sumber landsat  --tahun 2009-2015
python scripts/gee/gen_landsat_tiles.py --sumber sentinel --tahun 2016-2018
```

| Tahun | Sumber | Resolusi |
|---|---|---|
| 2009–2015 | Landsat 5/7/8 Collection 2 L2 | 30 m |
| 2016–2018 | Sentinel-2 L1C + Cloud Score+ | 10 m |

Catatan metodologis yang penting dan mahal dipelajari:

- **Tiap tahun adalah komposit ±1 tahun**, bukan potret satu tahun — "2011" berarti
  gabungan 2010–2012, dan label di peta menyebutnya terang-terangan. Kalimantan terlalu
  berawan: diuji di Sangatta, 2011 sendirian menyisakan 8% piksel bolong, jendela
  2010–2012 nol.
- **Persentil 35, bukan median** — awan selalu lebih terang dari permukaan, jadi mengambil
  nilai di bawah median otomatis membuang piksel berawan tipis yang lolos masker. Median
  menyisakan bercak awan; p25 terlalu gelap.
- **2016–2018 terpaksa memakai Sentinel-2 L1C** (puncak atmosfer) karena L2A praktis tak
  ada di Kalimantan pada tahun-tahun itu. Kabutnya ditangani dengan menaikkan titik hitam
  ke 0,05 — itu **peregangan tampilan, BUKAN koreksi atmosfer**; jangan dipakai sebagai
  masukan hitungan.
- **Penyeragaman warna antar-sensor sengaja tidak dilakukan** (koefisien Roy dkk. 2016
  diturunkan untuk Collection 1 dan tidak disarankan untuk Collection 2). Nada warna
  karenanya bergeser sedikit di 2013 dan 2016.
- **Laut ditutup dengan band `datamask` Hansen v1.13** supaya batas darat–laut konsisten
  dengan sumber angka. *(Yang gagal dan jangan diulang: JRC Global Surface Water —
  cakupannya tak sampai laut lepas.)*
- **Resolusi berubah sepanjang seri** (30 m → 10 m → citra masa kini 0,3–0,5 m). Jangan
  menyimpulkan "bukaan makin jelas" dari citra yang memang makin tajam.

### 6c. Replikasi peta di QGIS

1. Jalankan pipeline sampai selesai; raster Hansen sudah ada di `data/raster/`.
2. Langkah 09 menghasilkan `data/wiup/kalimantan_with_loss.geojson` — 825 konsesi dengan
   properti `kode_wiup`, `nama_usaha`, `tahun_izin`, `hilang_2001_2024_ha`, dan
   `hilang_2001_ha` … `hilang_2024_ha`. Itulah layer poligonnya.
3. Di QGIS: gabungkan 4 TIF `lossyear` jadi satu VRT (*Raster → Miscellaneous → Build
   Virtual Raster*), clip ke poligon konsesi (*GDAL → Clip raster by mask layer*, NoData
   `255`), lalu warnai per tahun. Basemap padanan web: XYZ
   `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`;
   batas kabupaten: `data/boundaries/kalimantan-kabupaten.geojson`.

`scripts/qgis_loss_slider.py` membuat layer slider tahunan otomatis, tetapi berstatus
**ARSIP**: kosakata & jendelanya masih 2009–2025 dan kolomnya `loss_*`. Sesuaikan sendiri
ke jendela 2001–2024 bila dipakai. Skrip itu hanya jalan di dalam QGIS (butuh `qgis.core`).

---

## 7. ARSIP — pipeline v2 di `scripts/`

Isi `scripts/` **bukan** satu kesatuan pipeline. Tiap berkas diberi **header status** di
baris pertama, jadi periksa itu sebelum memakainya:

| Status | Artinya |
|---|---|
| **PRASYARAT AKTIF** | menghasilkan input yang dibutuhkan pipeline v3, tetapi dijalankan manual di luar `bangun.sh` (`download_hansen.py`, `batch_analyze.py`, `fetch_geoportal_hutan.py`, `mapbiomas/fetch_mapbiomas_lulc.py`) |
| **PUSTAKA AKTIF** | diimpor langsung oleh pipeline v3 (`_geo_common.py`, `filter_minerba.py`, `match_harder.py`) — jangan diubah tanpa membangun ulang DB |
| **ARSIP** | pipeline generasi sebelumnya (v2). Masih bisa dijalankan, tetapi hasilnya **bukan** angka tesis sekarang |
| **USANG** | percobaan generasi awal yang sudah digantikan |
| **DI LUAR PIPELINE** | figur naskah, tile, batas administrasi |

**Apa itu pipeline v2?** Generasi sebelum pivot: basis datanya `data/kalimantan.db` +
`data/mapbiomas.db` (+ `data-full/kalimantan.db`), jendelanya **2001–2025**, nama
kolomnya Inggris (`iup_year`, `loss_*`), dan kerangka analisisnya membandingkan **tiga
periode kewenangan izin** (kabupaten 2009–2014 → provinsi 2015–2019 → pusat 2020–2025)
lewat keluarga tabel `backtrack_*` / `periode_*` / `atribusi_*`, dengan lapisan pemeriksa
sawit dari peta Descals dkk. 2024.

Fokus tesis bergeser pada 1–2 September 2026: pokok bahasannya kini **guna lahan di dalam
konsesi** (apa isinya, berubah jadi apa, dan bukaan tambang mencaplok lahan apa) dengan
jendela 2001–2024. Konsekuensinya:

- **Descals dicabut** dari analisis. "Sawit" sekarang berarti kelas 35 MapBiomas.
  `attribution_sawit.py`, `fetch_descals.py`, `gen_descals_tiles.py` = ARSIP.
- Keluarga `backtrack_*` / `periode_*` / `atribusi_izin*` **dibekukan sebagai pembanding**
  (`build_laju_izin.py`, `build_periode_tables.py`, `build_atribusi_izin.py` = ARSIP).
  Penerus konsepnya adalah `keyakinan_pra_izin` (langkah 08).
- Logika inti **dipindah, bukan ditulis ulang** — supaya angkanya bisa dibuktikan identik.
  Peta padanannya:

| Skrip v2 (ARSIP) | Penerus di pipeline v3 |
|---|---|
| `build_combined_db.py` | `01_identitas.py` (+ `02_hansen.py` untuk bagian pengukuran) |
| `match_harder.py` | logika T1–T4 → `lib/w1_util.py`; `normalize_name` masih **diimpor** oleh `06_kawasan_hutan.py` |
| `temporal_iup.py` | `02_hansen.py` → `izin_laju` (ambang vonis 1,5 / 0,67 sama persis, jendela 2024) |
| `klasifikasi_perpanjangan.py` | `03_izin.py` → `izin_klasifikasi` |
| `mapbiomas/landuse_konsesi.py` | `lib/w2_mapbiomas.py` + `04_mapbiomas.py` → `mapbiomas_tahunan` |
| `mapbiomas/transisi_konsesi.py` | `05_transisi.py` → `transisi_*` |
| `build_kawasan_hutan.py` | `06_kawasan_hutan.py` (bagian tanggal pulih pindah ke `01_identitas.py`) |
| `build_umur_izin.py` | `07_umur_izin.py` |
| `build_keyakinan_pra_izin.py` | `08_keyakinan.py` |
| `gen_dashboard_stats.py`, `sync_geojson_from_db.py` | `09_sajikan.py` |
| `verify_invariants.py`, `check_db_journal.py` | `10_verifikasi.py` |
| `enrich_with_db.py` | tidak ada penerus — keluarannya memang tak pernah dibaca siapa pun |

Skrip v2 **sengaja tidak dihapus**: ia bahan audit untuk membuktikan angka v3 = angka v2
di titik-titik yang memang harus sama. Tetapi orkestratornya (`rescrape/process.sh` di
repo pengembangan) tidak disertakan di bundel ini, jadi menjalankan ulang pipeline v2
utuh dari sini butuh perakitan manual.

---

## 8. Catatan sumber & keterbatasan

- **Geoportal ESDM** — layer `WIUP_Publish` (semua WIUP; berbeda dari layer lama
  `Join_WIUP_vs_IPPKH` yang hanya memuat WIUP beririsan kawasan hutan). Tanggal
  (`tgl_berlaku`/`tgl_akhir`) = SK **terkini**, bukan izin pertama — inilah alasan adanya
  `izin_klasifikasi` dan `keyakinan_pra_izin`.
- **Hansen GFC v1.13** (CC BY 4.0): "tree cover loss" ≠ deforestasi permanen (termasuk
  kebakaran & rotasi tanaman); ambang kanopi 30% adalah pilihan model, bukan definisi
  hutan resmi.
- **MapBiomas Indonesia Koleksi 4.1** (**CC BY-SA 4.0**): sitasi wajib — *MapBiomas
  Indonesia – Collection 4.1 time-series maps of land-use and land-cover, accessed on
  [tanggal] via https://landy.mapbiomas.id*. Tiga kaveat yang wajib ikut dibawa:
  1. **Akurasi kelas Lubang Tambang tidak dipublikasikan** MapBiomas (validasi resmi hanya
     Koleksi 2, 6 kelas) — baca luas tambang sebagai **batas bawah**.
  2. Hansen dan MapBiomas **sering tak sepakat pada bukaan kecil** (sepakat 96,3% pada
     gugus >100 ha, jauh lebih rendah pada gugus kecil); selisihnya dibaca sebagai
     **indikasi degradasi**, bukan galat salah satu pihak.
  3. **Kategori gabungan buatan sendiri** (mis. "Pertanian non-sawit" = kelas 9+21+40)
     wajib diberi keterangan karena bukan kelas resmi MapBiomas — daftarnya ada di tabel
     `mapbiomas_gabungan`.
  Angka MapBiomas ≠ angka Hansen **by design** (definisi hutan berbeda; neto vs bruto):
  posisinya lapisan yang saling melengkapi, bukan saling menggantikan.
- **IPPKH Geoportal** = potret izin **aktif** saat pengunduhan (lihat tanggal di
  `data/geoportal/MANIFEST.csv`), bukan register sejarah — jumlah konsesi ber-IPPKH adalah
  batas bawah.
- **MinerbaOne** (`minerba-kalimantan.db`): data sekunder dari API publik ESDM. Sebagian
  WIUP tak ter-cross-link (SK kosong / format berbeda); kolom `konsesi_registri.cocok` dan
  `strategi_cocok` menyebut berapa dan lewat cara apa.
- **BPS / geoBoundaries**: kepadatan penduduk per kab/kota 2015–2024; batas administrasi
  dari geoBoundaries.
- **Descals dkk. 2024** (CC BY 4.0): dipakai pipeline v2, **tidak lagi** di v3. Petanya
  berhenti 2021, sehingga 2022 ke atas tak pernah terperiksa terhadap sawit.

Lisensi data turunan mengikuti sumbernya, per tabel — lihat `analysis_meta.lisensi`.
Turunan MapBiomas bersifat **ShareAlike**.

---

## Lampiran A — Sumber data untuk scrape ulang (opsional)

Skrip scraper WIUP/MinerbaOne tidak disertakan, tetapi datanya berasal dari **API publik**
berikut (tanpa autentikasi). Cukup untuk menarik ulang bila diperlukan.

### A.1 MinerbaOne (perusahaan & izin)

Base: `https://minerbaone.esdm.go.id/api/common/v2/publik`
Header: `Accept: application/json`, `Referer: https://minerbaone.esdm.go.id/publik/badan-usaha`.
Envelope sukses: `{"message":"Success","data":{…},"code":200}`; paginasi Laravel
(`data.data[]`, `data.current_page`, `data.last_page`, `data.total`).

| Data | Endpoint |
|---|---|
| Daftar badan usaha | `GET /badan-usaha?sort=nama_badan_usaha&page=N&limit=100&search=` |
| Detail perusahaan | `GET /badan-usaha/{id}` |
| Izin (SK, WIUP, komoditas) | `GET /badan-usaha/{id}/list-perizinan?page=N&limit=100` |
| Direksi | `GET /badan-usaha/{id}/list-direksi` |
| Pemegang saham | `GET /badan-usaha/{id}/list-kepemilikan-saham` |

Catatan: gunakan parameter **`limit`** (bukan `per_page`); `id_wiup` ada di field
top-level tiap izin. Data disimpan ke SQLite dengan skema tabel `badan_usaha` +
`perizinan` (lihat kolom di `minerba-kalimantan.db`).

### A.2 Geoportal ESDM (poligon WIUP)

Layer **`WIUP_Publish`** (ArcGIS REST):
```
https://geoportal.esdm.go.id/monaresia/sharing/servers/3b305b4113384b41b7490479e0702093/rest/services/Pusat/WIUP_Publish/MapServer/0/query
```
Contoh query (GeoJSON, geometri lengkap, filter Kalimantan):
```
?where=pulau%3D'KALIMANTAN'&outFields=*&returnGeometry=true&outSR=4326
 &resultOffset=0&resultRecordCount=100&f=geojson
```
`maxRecordCount` server = 100 → paginasi lewat `resultOffset`. Tanggal
(`tgl_berlaku`/`tgl_akhir`) dalam epoch **milidetik**; `sk_iup` sering ber-padding spasi
(perlu `.strip()`). Simpan sebagai GeoJSON ke `data/wiup/`.

### A.3 Geoportal ESDM (kehutanan — IPPKH & overlay kawasan hutan)

Ini yang ditarik `scripts/fetch_geoportal_hutan.py`; kueri, jumlah fitur, MD5, dan tanggal
unduhan tercatat di `data/geoportal/MANIFEST.csv` supaya reproduksinya bisa diaudit.

| Berkas | Layer (ArcGIS REST `gis1`) | Filter |
|---|---|---|
| `ippkh_eksplorasi.geojson` | `Izin_Pinjam_Pakai_Kawasan_Hutan/MapServer/0` | `kode_prov IN (61,62,63,64,65)` |
| `ippkh_operasi.geojson` | `Izin_Pinjam_Pakai_Kawasan_Hutan/MapServer/1` | `kode_prov IN (61,62,63,64,65)` |
| `overlay_hutan.geojson` | `Overlay_WIUP_vs_Kawasan_Hutan/MapServer/0` | `pulau='KALIMANTAN'` |

Base: `https://geoportal.esdm.go.id/gis1/rest/services/`.

### A.4 Raster

- **Hansen GFC-2025 v1.13** — `scripts/download_hansen.py --kalimantan-all` (URL granul
  ada di dalam skrip; 4 tile × 2 layer).
- **MapBiomas Indonesia C4.1** — `scripts/mapbiomas/fetch_mapbiomas_lulc.py`; ukuran &
  MD5 tiap tahun dibandingkan dengan `scripts/mapbiomas/manifest_c41_2000_2024.csv` yang
  ikut di bundel ini.
