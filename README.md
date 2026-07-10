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
- `data-full/kalimantan.db`, `data/kalimantan.db`, `data/analysis/*` — output pipeline.
- `stata/` — **data panel penelitian (belum dipublikasikan)**; hanya dipakai
  langkah 8 yang bersifat opsional. Semua langkah lain berjalan tanpanya.

---

## 2. Prasyarat

- **Python 3.11+**
- Dependensi:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install rasterio shapely numpy scipy pandas matplotlib
  ```
  (`scipy` dipakai uji signifikansi di langkah 9; `pandas` hanya untuk langkah 8
  yang opsional; `matplotlib` hanya untuk grafik opsional.)

Jalankan semua perintah **dari folder `Tanah Hilang/`** (skrip memakai path
relatif seperti `data/...`).

Empat sumber data: **Geoportal ESDM** (poligon WIUP), **Hansen GFC v1.13**
(raster kehilangan pohon), **MinerbaOne** (perusahaan/izin), **geoBoundaries +
BPS** (batas & kepadatan kabupaten).

---

## 3. Pipeline (9 langkah inti)

Data WIUP & MinerbaOne **sudah disertakan** (langkah scrape sudah dilakukan),
jadi mulai dari langkah 1 di bawah.

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

> Langkah 8–9 **wajib dijalankan SETELAH langkah 7** — `filter_minerba` membangun
> ulang `data/kalimantan.db` dari nol, sehingga tabel hasil langkah 8–9 ikut
> terhapus bila urutannya terbalik.

**8 — *(OPSIONAL)* Muat panel penelitian (paparan sentralisasi per kabupaten)**

> Butuh berkas panel penelitian `stata/Data all v0.7.dta` yang **tidak
> disertakan** dalam paket ini (data penelitian belum dipublikasikan). Lewati
> langkah ini bila tak memilikinya — langkah 9 dan semua langkah lain **tidak
> bergantung** padanya; hanya tabel `exposure_kabupaten` yang tak terbentuk.

```bash
python script/import_exposure_panel.py \
       --dta "stata/Data all v0.7.dta" --db data/kalimantan.db
```
→ tabel **`exposure_kabupaten`** (56 kab: `exp_sentralisasi`/`exp_coal`/`exp_z`;
22 kabupaten "kontrol murni" ber-exp=0).

**9 — Bangun tabel analisis 3 periode kewenangan izin**
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
Mann–Whitney (Holm).

### Langkah pelengkap & opsional

- **`prep_bps_boundaries.py`** — *(opsional)* bangun ulang batas kabupaten dari
  geoBoundaries. Hasilnya sudah disertakan.
- **`sync_geojson_from_db.py`** — *(pelengkap)* regenerasi
  `data/wiup/kalimantan_with_loss.geojson` (825 konsesi + loss per tahun)
  langsung dari `kalimantan.db` — dipakai panduan QGIS.
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

**Tabel analisis** (langkah 8–9; turunan — bisa dibangun ulang kapan pun):

| Tabel | Isi |
|---|---|
| `exposure_kabupaten` | *(hanya bila langkah 8 dijalankan)* paparan sentralisasi 2020 per kabupaten |
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
