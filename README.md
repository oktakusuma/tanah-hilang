# Tanah yang Hilang — Reproduksi Data (raw → jadi)

Paket ini berisi **data mentah + skrip pipeline** untuk membangun ulang basis data
akhir `data/kalimantan.db` dari nol — basis data yang dibaca web app *Tanah yang
Hilang* (analisis deforestasi konsesi tambang di Kalimantan, 2001–2025).

Ikuti langkah berurutan. Hasil akhirnya **dua** berkas SQLite:

- `data-full/kalimantan.db` — **lengkap**: semua WIUP (**±1.765**), mineral +
  batubara + galian C.
- `data/kalimantan.db` — **default minerba**: hanya mineral logam + batubara
  (**±825**). Inilah yang dibaca web app.

> **Catatan penting:** paket ini berisi **data mentah hasil scrape yang sudah
> jadi** + **skrip pengolahan**. Skrip *scraper*-nya (penarik data dari
> MinerbaOne & Geoportal) **tidak** disertakan — tetapi endpoint API-nya
> didokumentasikan di **Lampiran A** kalau Anda ingin menarik ulang sendiri.

---

## 1. Struktur folder

```
Tanah Hilang/
├── data/                                # INPUT mentah (sudah disertakan)
│   ├── minerba-kalimantan.db            #   MinerbaOne: 7.572 badan usaha + 8.461 izin
│   ├── kepadatan_penduduk.csv           #   BPS: kepadatan 56 kab/kota, 2015–2024
│   ├── wiup/
│   │   ├── kalimantan_raw.geojson       #   snapshot WIUP dari Geoportal (±1.765)
│   │   └── kalimantan_unique.geojson    #   WIUP unik (dedup kode_wiup) — dipakai analisis
│   └── boundaries/
│       └── kalimantan-kabupaten.geojson #   batas kabupaten (geoBoundaries)
├── script/                              # pipeline pengolahan (raw → jadi)
└── README.md                            # berkas ini
```

**Tidak** disertakan (dihasilkan/diunduh saat menjalankan):
- Raster Hansen (~1,3 GB) — diunduh oleh `download_hansen.py` (langkah 1).
- Raster Descals sawit (~146 MB) — diunduh oleh `fetch_descals.py` (prasyarat langkah 8 & 11).
- `data-full/kalimantan.db`, `data/kalimantan.db`, `data/analysis/*` — output pipeline.

> Paket ini **tidak** menyertakan `stata/` (panel penelitian tesis, belum
> dipublikasikan) — langkah pemuatannya (`import_exposure_panel.py`
> beserta tabel `exposure_kabupaten`) sudah **dihapus** dari pipeline utama;
> tak ada lagi langkah opsional yang membutuhkannya. Semua 13 langkah di bawah
> berjalan tanpa `stata/`.

---

## 2. Prasyarat

- **Python 3.11+**
- Dependensi:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install rasterio shapely numpy scipy matplotlib
  ```
  (`scipy` dipakai uji signifikansi di `build_periode_tables.py`, langkah 10;
  `matplotlib` hanya untuk grafik opsional `make_charts.py`/`trend_analysis.py`,
  di luar 13 langkah.)

Jalankan semua perintah **dari folder `Tanah Hilang/`** (skrip memakai path
relatif seperti `data/...`).

Empat sumber data: **Geoportal ESDM** (poligon WIUP), **Hansen GFC v1.13**
(raster kehilangan pohon), **MinerbaOne** (perusahaan/izin), **geoBoundaries +
BPS** (batas & kepadatan kabupaten).

---

## 3. Pipeline (13 langkah — sinkron dengan `rescrape/process.sh` repo utama)

Data WIUP & MinerbaOne **sudah disertakan** (langkah scrape sudah dilakukan),
jadi mulai dari langkah 1 di bawah. Urutan & penomoran mengikuti persis
`rescrape/process.sh` di repo utama (13 langkah, tanpa panel penelitian/exposure).

**1 — Unduh raster Hansen (~1,3 GB)**
```bash
python script/download_hansen.py --kalimantan-all
```
→ `data/raster/*.tif` (4 tile × `lossyear` + `treecover2000`). Bisa di-resume.

**2 — Hitung kehilangan hutan per konsesi (analisis spasial inti)**
```bash
python script/batch_analyze.py --province KALIMANTAN --threshold 30
```
→ `data/analysis/batch_KALIMANTAN_t30_wide.csv`. Metode: rasterisasi poligon →
filter kanopi ≥30% (baseline hutan 2000) → decode `lossyear` 1–25 → 2001–2025 →
koreksi luas piksel per lintang (~0,0774 ha).

**3 — Cocokkan ke data perusahaan (MinerbaOne)**
```bash
python script/enrich_with_db.py \
       --input data/analysis/batch_KALIMANTAN_t30_wide.csv \
       --db data/minerba-kalimantan.db
```
→ `data/analysis/batch_KALIMANTAN_t30_enriched.csv`. Join lewat nomor SK (persis).

**4 — Analisis temporal (laju sebelum vs sesudah izin)**
```bash
python script/temporal_iup.py
```
→ `data/analysis/temporal_iup_analysis.csv` (laju pra/pasca izin + verdict).

**5 — Rakit basis data LENGKAP (semua WIUP)**
```bash
mkdir -p data-full
python script/build_combined_db.py --output data-full/kalimantan.db --force
```
→ **`data-full/kalimantan.db`** (8 tabel + 1 view). Menggabungkan poligon, hasil
loss, temporal, salinan MinerbaOne, & `kepadatan_penduduk.csv` (BPS).

**6 — Pencocokan lanjutan (fuzzy)**
```bash
python script/match_harder.py --db data-full/kalimantan.db --apply
```
→ menaikkan match dengan 4 tingkat (SK persis → SK dinormalisasi → digit-saja →
kemiripan nama perusahaan).

**7 — Saring ke DEFAULT minerba (mineral + batubara)**
```bash
python script/filter_minerba.py \
       --input data-full/kalimantan.db --output data/kalimantan.db --force
```
→ **`data/kalimantan.db`** (±825 WIUP). Membuang galian C/batuan (pasir, andesit,
batu, tanah); menyisakan batubara + mineral logam. Registry perusahaan tetap utuh.

> Langkah 8–13 **wajib dijalankan SETELAH langkah 7** — `filter_minerba` membangun
> ulang `data/kalimantan.db` dari nol, sehingga tabel hasil langkah 8–13 ikut
> terhapus bila urutannya terbalik.

Sebelum langkah 8, unduh raster Descals sawit (~146 MB, sekali saja):
```bash
python script/fetch_descals.py    # -> data/external/descals/ (raster mentah, CC-BY-4.0)
```
Kalau dilewati, langkah 8 & 11 di bawah **otomatis dilewati** (skrip mengecek
keberadaan `data/external/descals/tiles`) dan `data/kalimantan.db` tetap valid
tanpa lapisan sawit — tak mengubah angka utama (1.603.251 ha).

**8 — Atribusi ke konversi sawit (Descals)** *(dilewati otomatis bila raster
Descals tak ada)*
```bash
python script/attribution_sawit.py --db data/kalimantan.db
```
→ tabel **`atribusi_sawit`** + **`atribusi_sawit_yearly`**. Sebagian kehilangan
tutupan pohon di dalam batas WIUP bisa jadi sebenarnya konversi ke kelapa
sawit, bukan pembukaan tambang — diuji dengan peta tahun-tanam sawit Descals
dkk. (2024) ("Global mapping of oil palm planting year from 1990 to 2021",
*Earth System Science Data* 16:5111-5129, doi:10.5194/essd-16-5111-2024). Data
raster: Zenodo doi:10.5281/zenodo.13379129 (v1.2), lisensi **CC-BY-4.0**
(atribusi saja, tanpa ShareAlike). Cakupan tahun tanam 1990–2021.
`atribusi_sawit` menyimpan, per konsesi: 3 varian jendela toleransi window
penuh 2001–2021 (`loss_sawit_tol2th_ha`/`_jeda5th_ha`/`_tahunsama_ha`), sisa
2022–2025 (`loss_2022_2025_ha`), **serta 3 kolom silang sawit × pra/pasca-izin**
(`loss_sawit_pra_izin_ha`, `loss_sawit_pasca_izin_2021_ha`,
`loss_pasca_izin_2021_ha`) yang memisahkan sawit dari efek pra/pasca-terbitnya-izin.

**9 — Klasifikasi izin pertama vs perpanjangan**
```bash
python script/klasifikasi_perpanjangan.py --db data/kalimantan.db
```
→ tabel **`klasifikasi_izin`**. Menguji apakah `iup_year` (dasar pengelompokan
3 periode kewenangan) benar-benar berarti "tahun izin pertama terbit", memakai
data registri sendiri (jenis izin, durasi SK) — tanpa sumber luar.

**10 — Bangun tabel analisis 3 periode kewenangan izin**
```bash
python script/build_periode_tables.py --db data/kalimantan.db
```
→ 9 tabel analisis (`periode_*` + `penerbit_tahunan_aktif`) + **`analysis_meta`** (provenance per
tabel: sumber + metode + skrip) + **`column_meta`** (kamus kolom: arti + rumus + sumber tiap
kolom, untuk tab Skema halaman Database). Periode dari tahun terbit izin (`iup_year`): Pra-2009 · P1 2009–2014
(UU 4/2009) · P2 2015–2019 (UU 23/2014) · P3 2020–2025 (UU 3/2020). Jendela:
izin 1998–2025 (4 konsesi `iup_year` 2026 + 7 tanpa tahun dikeluarkan → 814/825
dianalisis), deforestasi 2001–2025. Isinya: ringkasan per periode, deforestasi
tahunan (slope OLS), event-study (waktu relatif ke izin), kontrol komoditas,
distribusi ukuran (Gini/share top-10%), dan uji signifikansi Kruskal–Wallis +
Mann–Whitney (Holm). Dijalankan **setelah langkah 8–9** karena ia yang menulis
provenansi (`analysis_meta`/`column_meta`) untuk seluruh lapisan, termasuk
`atribusi_sawit` dan `klasifikasi_izin` (varian `periode_*_bersih` dibangun
dari lapisan sawit bila terisi). Tidak bergantung pada ubin peta (langkah
11) — `gen_descals_tiles.py` cuma menghasilkan gambar PNG untuk peta, tak
pernah dibaca skrip ini maupun tersimpan ke `kalimantan.db`.

**11 — Tile piksel sawit untuk peta** *(dilewati otomatis bila raster Descals
tak ada)*
```bash
python script/gen_descals_tiles.py
```
→ `data/tiles/descals/*.png` (tile XYZ, dipakai toggle sawit di peta web).
`gen_descals_tiles.py` meng-`import` `DESCALS_DIR` dari `attribution_sawit.py`
(harus berada di folder `script/` yang sama). Murni pekerjaan penyajian
(merender gambar) — tak menyentuh `kalimantan.db` sama sekali, jadi posisinya
setelah langkah 10 di sini sekadar mengelompokkannya bersama langkah penyajian
lain (12–13), bukan karena ada ketergantungan data.

**12 — Sinkronisasi geojson (untuk QGIS)**
```bash
python script/sync_geojson_from_db.py
```
→ regenerasi `data/wiup/kalimantan_with_loss.geojson` (825 konsesi + loss per
tahun) langsung dari `kalimantan.db` — dipakai panduan QGIS.

**13 — Perbarui angka narasi dashboard (JSON)**
```bash
python script/gen_dashboard_stats.py
```
→ `webapp/src/generated/dashboard-stats.json` (dibuat otomatis walau folder
`webapp/` tak ada di paket ini). Berkas ini sumber satu-satunya angka narasi
frontend repo utama (loss total, %, jumlah konsesi, dsb.) — di bundel ini
langkah 13 opsional untuk dijalankan (tak ada `webapp/` yang membacanya),
tapi disertakan supaya urutan tetap identik dengan `rescrape/process.sh`.

### Langkah pelengkap & opsional (di luar 13 langkah)

- **`prep_bps_boundaries.py`** — *(opsional)* bangun ulang batas kabupaten dari
  geoBoundaries. Hasilnya sudah disertakan.
- **`make_charts.py` + `trend_analysis.py`** — *(opsional)* figur PNG + uji tren
  Mann-Kendall untuk naskah; tidak dipakai web app.

---

## 4. Dua versi hasil (kenapa?)

Layer Geoportal `WIUP_Publish` memuat **semua** WIUP, termasuk **galian C /
batuan** (pasir kuarsa, andesit, batu gamping, tanah urug, dll) yang sering **bukan
di kawasan hutan**. Untuk analisis *deforestasi tambang* fokusnya adalah tambang
mineral & batubara, jadi:

| Berkas | Isi | Jumlah WIUP |
|---|---|---|
| `data-full/kalimantan.db` | Semua WIUP (minerba + galian C) | ±1.765 |
| `data/kalimantan.db` | **Default** — batubara + mineral logam | ±825 |

Komoditas yang **dipertahankan** di versi default (lihat `filter_minerba.py`):
batubara + mineral logam (bauksit, emas, bijih besi, besi, zirkon, timah, mangan,
antimoni, intan). Sisanya (pasir/batu/tanah/kuarsa) hanya ada di versi lengkap.

---

## 5. Hasil akhir — tabel `kalimantan.db`

**Tabel inti** (langkah 5–7):

| Tabel | Isi |
|---|---|
| `wiup_geoportal` | poligon konsesi |
| `wiup_loss` / `wiup_loss_yearly` | kehilangan hutan agregat / per tahun |
| `wiup_temporal` | verdict laju pra/pasca izin |
| `wiup_match` | pencocokan ke MinerbaOne |
| `badan_usaha` / `perizinan` | 7.572 perusahaan / 8.461 izin |
| `kepadatan_penduduk` | 56 kab/kota, BPS 2015–2024 |
| view `wiup_master` | gabungan semua (dibaca API/web) |

**Tabel analisis** (langkah 10; turunan — bisa dibangun ulang kapan pun):

| Tabel | Isi |
|---|---|
| `periode_ringkasan` | ringkasan per periode: n, luas, loss, %poligon, %akselerasi, korelasi |
| `periode_deforestasi_tahunan` | loss per periode per tahun kalender 2001–2025 (kohort penuh) |
| `periode_tahunan_aktif` | deret stok izin-aktif per periode-tahun (loss, n, luas, hutan-2000, loss kumulatif) |
| `penerbit_tahunan_aktif` | idem per PENERBIT (Bupati/Gubernur/Menteri; termasuk pra-2009) |
| `periode_slope` | slope OLS loss~tahun per periode **berbasis izin-aktif (since-permit)** + tahun puncak |
| `periode_eventstudy` | rata-rata loss pada waktu-relatif-ke-izin (t−15…t+15) |
| `periode_komoditas` | metrik per periode × grup (batubara vs mineral logam) |
| `periode_ukuran` | persentil luas, share top-10%, Gini (polarisasi ukuran) |
| `periode_signifikansi` | Kruskal–Wallis + Mann–Whitney (Holm) antar P1/P2/P3 |
| `analysis_meta` | **provenance** semua tabel (sumber, metode, skrip) |
| `column_meta` | **kamus kolom**: arti + rumus + sumber tiap kolom (semua tabel/view) — untuk tab Skema halaman Database |

**Tabel lapisan tambahan** (langkah 8, 9 & 11; cangkangnya dibuat di langkah 5
lewat `LAPISAN_SHELLS`, diisi oleh `attribution_sawit.py` /
`klasifikasi_perpanjangan.py` — urutan langkah pipeline tak menentukan,
`wiup_master` tetap valid walau lapisan belum diisi):

| Tabel | Isi |
|---|---|
| `atribusi_sawit` | per konsesi (825 baris): pecahan loss yang beririsan dgn tahun-tanam sawit Descals (3 varian jendela toleransi, window 2001–2021) + 3 kolom silang sawit × pra/pasca-izin (`loss_sawit_pra_izin_ha`, `loss_sawit_pasca_izin_2021_ha`, `loss_pasca_izin_2021_ha`) |
| `atribusi_sawit_yearly` | pecahan `atribusi_sawit` per (kode_wiup, tahun) — dasar varian `periode_*_bersih` |
| `klasifikasi_izin` | per konsesi: vonis IZIN_PERTAMA / PERPANJANGAN / TAK_DINILAI + kekuatan bukti |

Asal-usul tiap tabel analisis dapat dilacak langsung:
```bash
sqlite3 data/kalimantan.db "SELECT nama_tabel, sumber, metode FROM analysis_meta"
```

---

## 6. Catatan sumber & keterbatasan

- **Geoportal ESDM** — layer `WIUP_Publish` (semua WIUP; berbeda dari layer lama
  `Join_WIUP_vs_IPPKH` yang hanya memuat WIUP beririsan kawasan hutan). Tanggal
  (`tgl_berlaku`/`tgl_akhir`) = SK terkini, bukan izin pertama.
- **Hansen GFC v1.13** (CC BY 4.0): "tree cover loss" ≠ deforestasi permanen
  (termasuk kebakaran/rotasi tanaman); ambang kanopi 30% adalah pilihan model.
- **MinerbaOne** (`minerba-kalimantan.db`): data sekunder dari API publik
  MinerbaOne ESDM (registry nasional). Sebagian WIUP tak ter-cross-link (SK
  kosong/format berbeda) — data inti (nama, SK, komoditas, loss) tetap lengkap.
- **BPS / geoBoundaries**: kepadatan penduduk per kab/kota 2015–2024; batas
  administrasi dari geoBoundaries.

Lisensi data turunan: CC BY 4.0.

---

## Lampiran A — Sumber data untuk scrape ulang (opsional)

Skrip scraper tidak disertakan, tetapi datanya berasal dari **API publik** berikut
(tanpa autentikasi). Cukup untuk menarik ulang bila diperlukan.

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

Catatan: gunakan parameter **`limit`** (bukan `per_page`); `id_wiup` ada di
field top-level tiap izin. Data disimpan ke SQLite dengan skema tabel
`badan_usaha` + `perizinan` (lihat kolom di `minerba-kalimantan.db`).

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
(`tgl_berlaku`/`tgl_akhir`) dalam epoch **milidetik**; `sk_iup` sering ber-padding
spasi (perlu `.strip()`). Simpan sebagai GeoJSON ke `data/wiup/`.
