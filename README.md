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
│                                        #   (bentuk lebar d2015..d2024; di-unpivot saat ingest)
│   ├── wiup/
│   │   ├── kalimantan_raw.geojson       #   snapshot WIUP dari Geoportal (±1.765)
│   │   └── kalimantan_unique.geojson    #   input kanonik analisis — lihat ralat di bawah
│   └── boundaries/
│       └── kalimantan-kabupaten.geojson #   batas kabupaten (geoBoundaries)
├── script/                              # pipeline pengolahan (raw → jadi)
└── README.md                            # berkas ini
```

> **Ralat label `kalimantan_unique.geojson`:** berkas ini **byte-identik** dengan
> `kalimantan_raw.geojson` (bisa dicek: `cmp data/wiup/kalimantan_raw.geojson
> data/wiup/kalimantan_unique.geojson`). Langkah "dedup `kode_wiup`" ternyata
> **no-op** — snapshot raw sudah unik per `kode_wiup`, tak ada baris yang
> terbuang. Nama berkas dipertahankan karena skrip analisis (`batch_analyze.py`)
> memang membaca nama ini sebagai input kanonik; label lama "WIUP unik (dedup)"
> memberi kesan ada penyaringan yang sebenarnya tidak terjadi.

**Tidak** disertakan (dihasilkan/diunduh saat menjalankan):
- Raster Hansen (~1,3 GB) — diunduh oleh `download_hansen.py` (langkah 4).
- Raster Descals sawit (~146 MB) — diunduh oleh `fetch_descals.py` (prasyarat langkah 10 & 15).
- `data-full/kalimantan.db`, `data/kalimantan.db`, `data/analysis/*` — output pipeline.

> Paket ini **tidak** menyertakan `stata/` (panel penelitian tesis, belum
> dipublikasikan) — langkah pemuatannya (`import_exposure_panel.py`
> beserta tabel `exposure_kabupaten`) sudah **dihapus** dari pipeline utama;
> tak ada lagi langkah opsional yang membutuhkannya. Semua 17 langkah (+14b)
> di bawah berjalan tanpa `stata/`.

---

## 2. Prasyarat

- **Python 3.14** — lingkungan kanonik yang menghasilkan angka ter-commit
  (freeze `pip` 2026-08-06); 3.11+ kemungkinan besar tetap jalan, tapi presisi
  byte-per-byte hanya terdokumentasi terhadap 3.14
- Dependensi — install dari `requirements.txt` (versi yang sama persis dipakai
  untuk menghasilkan angka kanonik tesis):
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  ```
  (`scipy` dipakai uji signifikansi di `build_laju_izin.py` (langkah 13,
  tabel `backtrack_signifikansi`) dan `build_periode_tables.py` (langkah 14) —
  tanpa scipy tabel signifikansi ditulis kosong/NULL, langkah lain tetap jalan;
  `matplotlib`/`openpyxl` hanya untuk skrip opsional
  `make_charts.py`/`trend_analysis.py`, di luar 17 langkah. Skrip verifikasi
  `verify_invariants.py`/`check_db_journal.py` murni stdlib.)

  > **Catatan pytest/tests:** `requirements.txt` menyertakan `pytest` karena
  > di-freeze dari repo pengembangan, yang punya folder `scripts/tests/`
  > (tes unit pipeline). **Bundel ini tidak menyertakan folder tests/** — tes
  > dijalankan di repo pengembangan; di sini pemeriksaan setara dilakukan
  > `verify_invariants.py` (bagian penutup pipeline, lihat §3).

  > **Catatan drift**: versi pustaka geospasial lain (rasterio/GEOS) tetap
  > bisa menjalankan pipeline ini, tapi hasilnya bisa bergeser sedikit di
  > keputusan piksel-tepi poligon — pada snapshot commit efeknya total di
  > bawah ±2 ha dari 1,99 juta ha, tidak mengubah kesimpulan mana pun.

Jalankan semua perintah **dari folder `Tanah Hilang/`** (skrip memakai path
relatif seperti `data/...`).

Empat sumber data: **Geoportal ESDM** (poligon WIUP), **Hansen GFC v1.13**
(raster kehilangan pohon), **MinerbaOne** (perusahaan/izin), **geoBoundaries +
BPS** (batas & kepadatan kabupaten).

---

## 3. Pipeline (17 langkah + 14b, 4 bagian — sinkron dengan `rescrape/process.sh` repo utama)

### Kerangka analisis terkini (baca dulu sebelum menjalankan)

- **Angka utama (headline)** kini berjendela **era UU Minerba 2009–2025**:
  **1.228.077 ha** tutupan pohon hilang (**34,4%** dari hutan-2009 =
  3.567.968 ha di dalam 825 konsesi minerba). Jendela penuh 2001–2025 tetap
  dilaporkan sebagai konteks: **1.603.251 ha** (**40,7%** dari hutan-2000).
- **Tiga metode "backtrack"** menentukan tahun MULAI menghitung kehilangan per
  konsesi (kolom `aturan` di semua tabel `backtrack_*`):
  - **Deteksi Hansen** (utama) — **codename di basis data: `aturan='CITRA'`**
    (kode sengaja TIDAK di-rename supaya query/skrip lama tetap jalan; label
    yang dipakai di web app & naskah tesis adalah "Deteksi Hansen"). Jam mulai
    dibaca dari **produk Hansen GFC** — bukan dari interpretasi citra satelit
    sendiri: tahun pertama dalam jendela bukti 2001–2021 yang mencatat
    *tree-cover loss* **non-sawit ≥ 1 ha** di dalam poligon (ambang ±11 piksel
    Hansen 30 m; jendela bukti berhenti 2021 = batas peta Descals), lalu
    `mulai` **diklem ≥ 2009** (jendela hitung era Minerba). Kohort **825**
    (semua konsesi — izin 2026/tanpa-tahun tetap masuk bila ada bukti).
  - **INDIKASI** — dari **kelas izin** (`klasifikasi_izin`): PERPANJANGAN
    dianggap aktif sepanjang jendela (mulai 2009 — kegiatan sudah berjalan
    sebelum SK perpanjangannya); IZIN_PERTAMA/TAK_DINILAI sejak
    max(2009, tahun SK). Kohort **818**.
  - **POLOS** — max(2009, tahun SK) **tanpa backtrack** sama sekali (batas
    bawah, menyangkal makna perpanjangan). Kohort **814** (825 − 7 tanpa
    tahun SK − 4 ber-SK 2026).
  - *(Aturan lama "C/PERKIRAAN" — perkiraan tahun izin asal via durasi SK,
    Ps. 47 UU 4/2009 — sudah **diarsipkan**: setop ditulis, baik di tabel
    `backtrack_*` maupun di `atribusi_izin_aktif` (kolom `mulai_c` /
    `loss_mulai_c_sampai_2025_ha` sudah tidak ada). Alasannya: dengan cara baca
    aditif yang benar, taksirannya selalu jatuh < 2009 → terklem → C ≡ INDIKASI.
    Datanya bisa diambil kembali dari riwayat git bila diperlukan untuk audit.)*
  - *(**`TANPA_ATRIBUSI`** — eks kode `X0`, `mulai` selalu 2009 — muncul di
    `atribusi_izin_aktif` sebagai **plafon pembanding**, BUKAN metode
    backtrack: ia sekadar "semua kehilangan era Minerba, tanpa atribusi izin
    apa pun". Karena itu ia tidak ada di keluarga `backtrack_*`.)*

  Rekonsiliasi total loss jendela `[mulai, 2025]` (basis kotor Hansen):
  **Deteksi Hansen/CITRA 1.227.970 · INDIKASI 1.038.362 · POLOS 589.487 ha**
  (plafon `TANPA_ATRIBUSI` = 1.228.077 ha).
- **Kosakata `aturan` diselaraskan** (Fase G): kode huruf lama `X0`/`B`/`D`
  diganti nama panjang `TANPA_ATRIBUSI`/`INDIKASI`/`POLOS` di seluruh basis
  data, sehingga `atribusi_izin_aktif` dan keluarga `backtrack_*` memakai
  kosakata yang sama.
- **Pilihan metode ikut mengubah irisan turunannya** — bukan cuma angka total.
  Contoh paling tajam, **zona bebas konsesi** (kab/kota tanpa satu pun konsesi
  aktif; tabel `backtrack_zona_bebas`, dari 56 kab/kota se-Kalimantan):
  menurut **POLOS** jumlahnya menyusut **32 → 10** sepanjang 2009–2019 (seolah
  perluasan tambang baru terjadi bertahap di sepanjang periode itu), sedangkan
  menurut **Deteksi Hansen** angkanya **datar di 10 sejak 2009** — di 46
  kab/kota sisanya sudah ada bukti pembukaan lahan **sebelum** SK-nya terbit.
  Itulah inti perbedaan ketiga metode, dan alasan ketiganya selalu disajikan
  berdampingan.
- **Periode P1/P2/P3** dalam kerangka utama kini **jendela TAHUN KALENDER** —
  P1 2009–2014 (kewenangan kabupaten) · P2 2015–2019 (provinsi) · P3 2020–2025
  (pusat) — yaitu *kapan* kehilangan terjadi (tabel
  `backtrack_periode_kalender`), **bukan** kohort tahun terbit SK. Tabel
  kohort-SK (`periode_*`, dan **`backtrack_kohort`** — eks `backtrack_periode`,
  kolomnya kini `kohort`, bukan `periode`) tetap dibangun sebagai pembanding.
- **Sawit (Descals dkk. 2024) first-class**: basis "**bersih**" = Hansen −
  sawit, hanya sampai **2021** (batas peta Descals); tahun **2022–2025 tak
  terperiksa** — tidak pernah dimasukkan ke penyebut persen mana pun, dan
  "bertepatan sawit" bukan klaim sebab-akibat.

Data WIUP & MinerbaOne **sudah disertakan** (langkah scrape sudah dilakukan),
jadi mulai dari langkah 1 di bawah. Urutan & penomoran mengikuti persis
`rescrape/process.sh` di repo utama (17 langkah + 14b dalam 4 bagian, ditutup
2 langkah verifikasi): **B1 Satukan data izin** (registry dulu, sebelum
diukur) → **B2 Hitung** (Hansen → CSV → tempel ke kedua basis data) →
**B3 Analisis** (tabel turunan tesis) → **B4 Sajikan** (artefak web) →
**verifikasi** (journal-mode + invarian angka).

### B1 — Satukan data izin (langkah 1–3)

**1 — Rakit registry LENGKAP (identitas semua WIUP, belum diukur)**
```bash
mkdir -p data-full
python script/build_combined_db.py --phase registry --output data-full/kalimantan.db --force
```
→ **`data-full/kalimantan.db`** (identitas ±1.765 WIUP: poligon, salinan
MinerbaOne, `kepadatan_penduduk.csv`, & pencocokan SK-persis
geoportal×perizinan LANGSUNG — tanpa lewat CSV pengukuran). Kolom hutan hilang
(`wiup_loss`/`wiup_loss_yearly`/`wiup_temporal`) masih **cangkang kosong**,
diisi nanti di langkah 8.

**2 — Pencocokan lanjutan (fuzzy)**
```bash
python script/match_harder.py --db data-full/kalimantan.db --apply
```
→ menaikkan match dengan 4 tingkat (SK persis → SK dinormalisasi → digit-saja →
kemiripan nama perusahaan).

**3 — Saring ke DEFAULT minerba (mineral + batubara)**
```bash
python script/filter_minerba.py \
       --input data-full/kalimantan.db --output data/kalimantan.db --force
```
→ **`data/kalimantan.db`** (±825 WIUP). Membuang galian C/batuan (pasir, andesit,
batu, tanah); menyisakan batubara + mineral logam. Registry perusahaan tetap
utuh. Kolom hutan hilang di `data/kalimantan.db` **masih kosong juga** sampai
langkah 9 — identitas & penyaringan selesai duluan, pengukuran menyusul.

### B2 — Hitung (langkah 4–10)

**4 — Unduh raster Hansen (~1,3 GB)**
```bash
python script/download_hansen.py --kalimantan-all
```
→ `data/raster/*.tif` (4 tile × `lossyear` + `treecover2000`). Bisa di-resume.

**5 — Hitung kehilangan hutan per konsesi (analisis spasial inti)**
```bash
python script/batch_analyze.py --province KALIMANTAN --threshold 30
```
→ `data/analysis/batch_KALIMANTAN_t30_wide.csv`. Metode: rasterisasi poligon →
filter kanopi ≥30% (baseline hutan 2000) → decode `lossyear` 1–25 → 2001–2025 →
koreksi luas piksel per lintang (~0,0774 ha). Dijalankan langsung dari
`data/wiup/kalimantan_unique.geojson` **mentah, untuk SEMUA ±1.765 WIUP** —
bukan dari basis data yang sudah disaring di langkah 3. Ini disengaja: tiap
konsesi diukur berdiri sendiri dari poligonnya sendiri, jadi angkanya identik
dihitung sebelum/sesudah saring, sekaligus memastikan hasil yang sama bisa
ditempel ke KEDUA basis data (langkah 8 & 9) tanpa raster dipindai dua kali.

**6 — Cocokkan ke data perusahaan (deliverable CSV)**
```bash
python script/enrich_with_db.py \
       --input data/analysis/batch_KALIMANTAN_t30_wide.csv \
       --db data/minerba-kalimantan.db
```
→ `data/analysis/batch_KALIMANTAN_t30_enriched.csv`. Join lewat nomor SK
(persis) — berkas analisis (CSV) ini terpisah dari pencocokan `wiup_match` di
langkah 1 (yang langsung geoportal×perizinan tanpa lewat CSV).

**7 — Analisis temporal (laju sebelum vs sesudah tahun izin)**
```bash
python script/temporal_iup.py
```
→ `data/analysis/temporal_iup_analysis.csv` (laju pra/pasca tahun izin +
verdict per konsesi; di basis data menjadi `wiup_temporal` dengan kolom
berjendela eksplisit: `loss_2001_sampai_tahun_izin_ha` /
`loss_tahun_izin_sampai_2025_ha`,
`rate_2001_sampai_tahun_izin_ha_per_year` /
`rate_tahun_izin_sampai_2025_ha_per_year`, plus varian mulai-2009).

**8 — Tempel pengukuran ke basis data LENGKAP**
```bash
python script/build_combined_db.py --phase pengukuran --db data-full/kalimantan.db
```
→ mengisi cangkang kosong `wiup_loss`/`wiup_loss_yearly`/`wiup_temporal` di
**`data-full/kalimantan.db`** (±1.765 WIUP) dari CSV langkah 5 & 7 — baris CSV
dibatasi ke `kode_wiup` yang ada di `wiup_geoportal` target. Ingest ini juga
menghitung kolom jendela era Minerba di `wiup_loss`
(`loss_2001_2008_ha`, `hutan_2009_ha`, `loss_2009_2025_ha`,
`loss_2009_2025_pct_hutan2009`).

**9 — Tempel pengukuran ke basis data DEFAULT**
```bash
python script/build_combined_db.py --phase pengukuran --db data/kalimantan.db
```
→ ulangi tempelan yang sama ke **`data/kalimantan.db`** (±825 WIUP minerba,
hasil saring langkah 3) — inilah yang dibaca web app.

> Langkah 10–17 **wajib dijalankan SETELAH langkah 9** — sebelum itu, kolom
> pengukuran di `data/kalimantan.db` belum terisi.

Sebelum langkah 10, unduh raster Descals sawit (~146 MB, sekali saja):
```bash
python script/fetch_descals.py    # -> data/external/descals/ (raster mentah, CC-BY-4.0)
```
Kalau dilewati, langkah 10 & 15 di bawah **otomatis dilewati** (pipeline
mengecek keberadaan `data/external/descals/tiles`) dan basis data tetap valid
— **TAPI awas**: tanpa lapisan sawit, "bukti" di langkah 13 jatuh ke Hansen
mentah, sehingga **jam mulai CITRA sendiri berubah** (bukan sekadar kolom
"bersih" jadi NULL). Untuk mereproduksi angka kanonik, langkah 10 tidak boleh
dilewati.

**10 — Atribusi ke konversi sawit (Descals)** *(dilewati otomatis bila raster
Descals tak ada; hanya `data/kalimantan.db`)*
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
penuh 2001–2021 (`loss_sawit_tol2th_2001_2021_ha` / `loss_sawit_jeda5th_2001_2021_ha`
/ `loss_sawit_tahunsama_2001_2021_ha`), sisa 2022–2025 (`loss_2022_2025_ha`),
jendela era Minerba (`loss_2009_2021_ha`, `loss_sawit_2009_2021_ha`), **serta
3 kolom silang sawit × pra/pasca-tahun-izin**
(`loss_sawit_2001_sampai_tahun_izin_ha`, `loss_sawit_tahun_izin_sampai_2021_ha`,
`loss_tahun_izin_sampai_2021_ha`) yang memisahkan sawit dari efek
pra/pasca-terbitnya-izin.

### B3 — Analisis (langkah 11–14b)

**11 — Klasifikasi izin pertama vs perpanjangan**
```bash
python script/klasifikasi_perpanjangan.py --db data/kalimantan.db
```
→ tabel **`klasifikasi_izin`**. Menguji apakah `iup_year` benar-benar berarti
"tahun izin pertama terbit", memakai data registri sendiri (jenis izin, durasi
SK) — tanpa sumber luar. Vonis `kelas` ∈ IZIN_PERTAMA / PERPANJANGAN /
TAK_DINILAI × `bukti` ∈ KUAT / INDIKASI. Hasilnya jadi dasar metode
**INDIKASI** (langkah 12–13).

**12 — Atribusi izin aktif era Minerba**
```bash
python script/build_atribusi_izin.py --db data/kalimantan.db
```
→ tabel **`atribusi_izin_aktif`** + **`atribusi_izin_aktif_ringkas`** (3 baris,
1 per aturan). Menjawab pertanyaan ATRIBUSI: berapa hutan hilang **ketika
izinnya benar-benar berlaku**, di jendela era UU Minerba 2009–2025.

**Bentuk baris (unpivot, Fase G):** satu baris per **(konsesi, aturan)** —
825 × 3 = **2.475 baris**, kolom `aturan` + `mulai` +
`loss_mulai_sampai_2025_ha`. Dulu aturan menjadi *kolom*
(`mulai_b`/`mulai_c`/`mulai_d` dst.); bentuk itu sudah ditinggalkan. Tiga nilai
`aturan`: **`TANPA_ATRIBUSI`** (eks X0 — plafon pembanding, `mulai` = 2009),
**`INDIKASI`** (eks B — PERPANJANGAN aktif sepanjang jendela → dasar metode
INDIKASI), **`POLOS`** (eks D — semua sejak max(2009, tahun SK) → dasar metode
POLOS). Aturan C/PERKIRAAN **setop ditulis** (diarsipkan, lihat Kerangka).

**Konsesi tanpa jangkar** (non-perpanjangan tanpa tahun SK — 7 dari 825):
barisnya tetap ditulis untuk audit, tetapi `mulai` **dan**
`loss_mulai_sampai_2025_ha` bernilai **NULL** (bukan 0 — 0 akan terbaca sebagai
"diukur, hasilnya nol"). Pasangan NULL ini ditegakkan invarian
`atribusi-null-berpasangan` di `verify_invariants.py`.

**Prasyarat keras langkah 13–14.**

**13 — Laju deforestasi per "jam izin" + tabel backtrack 3 metode**
```bash
python script/build_laju_izin.py --db data/kalimantan.db
```
→ tabel **`laju_izin_konsesi`** / **`laju_izin_eventstudy`** + **21 tabel
`backtrack_*`** + **2 VIEW kompatibilitas** (lihat §5). Pivot "laju dulu,
periode belakangan": tiap konsesi diberi tahun `mulai` versi **Deteksi Hansen**
(codename `CITRA` — lihat Kerangka di atas), lalu laju dihitung dua **basis
yang tak pernah dicampur**: **bersih** (Hansen − sawit, ≤2021 — utama) dan
**kotor** (Hansen penuh, ≤2025 — pendamping). Tabel `backtrack_*` mengulang
akuntansi yang sama untuk KETIGA metode (CITRA/INDIKASI/POLOS) sebagai analisis
sensitivitas. Butuh `atribusi_izin_aktif` (langkah 12).

> **Fase G — `laju_izin_ringkas` & `konsesi_aktif_tahunan` kini VIEW**, bukan
> tabel: keduanya hanyalah irisan `aturan='CITRA'` dari
> `backtrack_laju_ringkas` / `backtrack_tahunan`. Sebelumnya keduanya tabel
> terpisah yang dihitung ulang — sumber drift senyap bila salah satu jalur
> berubah. Nama & kolomnya dipertahankan supaya query lama tetap jalan; isinya
> terbukti identik (EXCEPT dua arah = 0 baris).

> **Fase C — 5 tabel irisan baru + logika wilayah pindah ke pipeline.**
> Langkah ini kini juga membangun `backtrack_wilayah`,
> `backtrack_komoditas_rinci`, `backtrack_konsesi_top`, `backtrack_keparahan`,
> dan `backtrack_zona_bebas` (semuanya × 3 aturan, jendela `[mulai versi
> aturan, 2025]`, penyebut persen = `hutan_2009_ha`) — lihat §5. Dua hal yang
> **pindah dari sisi penyaji ke pipeline** supaya angkanya bisa direproduksi
> dari basis data saja:
> - **Master 56 kab/kota Kalimantan + normalisasi ejaan** kini hidup di
>   `build_laju_izin.py` sebagai konstanta `MASTER_KABKOTA` dan fungsi
>   `normalisasi_kabkota()` (dulu di handler server Go).
> - **Pemecahan kabupaten gabungan** (satu konsesi tercatat di beberapa
>   kab/kota) kini dilakukan di pipeline, hektarnya **dibagi rata** ke tiap
>   kabupaten — dulu dihitung di klien. Hasilnya identik; bedanya sekarang
>   terikat invarian `backtrack-wilayah-rekonsil`.

> **Fase T (16 Agu) — 6 tabel penopang bagian "Temuan" + kolom besar efek.**
> Gelombang terakhir menambah enam tabel `backtrack_*` yang menjawab
> pertanyaan "seberapa kokoh kesimpulannya" — bukan mengubah angka utama.
> Tiganya adalah alat statistik baru yang perlu dikenali:
> - **Kurva Lorenz + Gini** (`backtrack_lorenz`) — pemusatan kerusakan
>   berdampingan dengan pemusatan **luas izin**. Ini pembanding yang penting:
>   kalau kerusakan hanya terpusat sebanyak luas izinnya terpusat, "yang besar
>   merusak besar" sudah cukup menjelaskan. Pada snapshot commit (aturan
>   `CITRA`): **10% konsesi teratas menanggung 55,04% kehilangan** sementara
>   10% konsesi terluas hanya memegang **47,39% luas SK**; `gini_loss`
>   **0,6935** > `gini_luas` **0,6259** — kerusakan **lebih** terpusat daripada
>   luas izin.
> - **Korelasi Spearman** (`backtrack_kesepakatan`, fungsi `spearman()` &
>   `_rank_rata()` di `build_laju_izin.py` — stdlib, bukan scipy) — kemiripan
>   deret tahunan 2009–2025 antar pasangan metode, dilaporkan berdampingan
>   dengan Pearson. Dua-duanya sengaja ditampilkan karena temuannya justru ada
>   di selisihnya: pada metrik `pct_thn` ketiga metode nyaris sepakat
>   (Spearman 0,9706–0,9853), tapi pada `loss_ha` CITRA vs POLOS **tak
>   berkorelasi sama sekali** (Pearson −0,0112 · Spearman −0,1176).
> - **Besar efek** (`besar_efek_r` di `backtrack_signifikansi`) — rank-biserial
>   `1 − 2·U/(n_a·n_b)` untuk tiap baris Mann–Whitney/Holm, NULL untuk baris
>   Kruskal–Wallis. Alasannya lugas: dengan n≈250 per grup, *p* kecil bisa
>   muncul dari selisih yang tak berarti, jadi **jangan mengutip *p* tanpa
>   kolom ini**. Rujukan kasar |r|: 0,1 kecil · 0,3 sedang · 0,5 besar.
>
> Tiga sisanya melayani pengakuan batas metode & pemetaan aktor:
> `backtrack_tak_terlihat` (berapa kehilangan yang jatuh **sebelum** jam tiap
> metode mulai, penyebutnya sengaja bebas metode), `backtrack_selisih`
> (sebaran jarak `iup_year` − tahun mulai aktif + sebaran tahun bukti mentah),
> `backtrack_tahun_ekstrem` & `backtrack_top_union` (3 tahun puncak/palung tiap
> metode; gabungan 10-besar ketiga metode dalam satu baris per konsesi).
> Ambang aktor juga dinaikkan: `TOP_N = 25` (dulu 10) → `backtrack_konsesi_top`
> kini **75 baris**.

**14 — Bangun tabel analisis 3 periode kewenangan izin**
```bash
python script/build_periode_tables.py --db data/kalimantan.db
```
→ tabel `periode_*` (kohort tahun SK — pembanding kerangka kalender),
`penerbit_tahunan_aktif`, `baseline_tahunan`, + **`analysis_meta`** (provenance
per tabel: sumber + metode + skrip + **kolom `status`**) + **`column_meta`** (kamus kolom dua arah:
arti + rumus + sumber tiap kolom, diverifikasi 100% terhadap
`PRAGMA table_info`). Periode dari tahun terbit izin (`iup_year`): Pra-2009 ·
P1 2009–2014 (UU 4/2009) · P2 2015–2019 (UU 23/2014) · P3 2020–2025
(UU 3/2020); jendela izin 1998–2025 (4 konsesi `iup_year` 2026 + 7 tanpa tahun
dikeluarkan → 814/825 dianalisis), deforestasi 2001–2025. Dijalankan
**terakhir di B3** karena ia yang menulis provenansi (`analysis_meta` /
`column_meta`) untuk seluruh lapisan — termasuk `atribusi_sawit`,
`klasifikasi_izin`, `atribusi_izin_aktif`, dan semua tabel laju/backtrack
langkah 12–13 (varian `periode_*_bersih` dibangun dari lapisan sawit bila
terisi; tabel signifikansi butuh scipy, ditulis kosong/NULL bila absen).

> **Fase G — kolom `status` di `analysis_meta`.** Tiap baris provenansi kini
> ditandai salah satu dari tiga status, supaya pembaca tahu tabel mana yang
> masih menopang kesimpulan dan mana yang tinggal jejak:
> **`AKTIF`** (42 tabel — dipakai kerangka utama) · **`ARSIP`** (10 — dibangun,
> tapi sudah tidak dipakai menyimpulkan; mis. keluarga `periode_*_bersih`,
> `periode_klasifikasi*`, `laju_izin_eventstudy`) · **`PROYEKSI`** (2 —
> `periode_tahunan_aktif` & `penerbit_tahunan_aktif`: tetap **tabel**, bukan
> view, dan isinya proyeksi kohort-SK, bukan pengukuran kerangka utama).
> ```bash
> sqlite3 data/kalimantan.db "SELECT status, COUNT(*) FROM analysis_meta GROUP BY status"
> ```

**14b — Ulangi klasifikasi + atribusi + laju + periode untuk data-full
(degradasi anggun)**
```bash
python script/klasifikasi_perpanjangan.py --db data-full/kalimantan.db
python script/build_atribusi_izin.py     --db data-full/kalimantan.db
python script/build_laju_izin.py         --db data-full/kalimantan.db
python script/build_periode_tables.py    --db data-full/kalimantan.db
```
→ `data-full/kalimantan.db` ikut **empat** langkah analisis yang sama. Lapisan
sawit memang tidak dibangun di sana (hanya untuk set 825 minerba) — skrip
**terdegradasi anggun**: kolom terkait NULL & tabel opsional dilewati.
Klasifikasi + atribusi izin **wajib** ikut: tanpa keduanya, tabel `backtrack_*`
di data-full hanya punya aturan CITRA — INDIKASI/POLOS butuh baris
`atribusi_izin_aktif`. Keduanya tak butuh Descals, jadi aman dijalankan di
data-full. Tanpa langkah ini, tabel laju/backtrack di data-full membeku pada
nilai build lama dan ikut ter-commit sebagai angka basi.

### B4 — Sajikan (langkah 15–17)

**15 — Tile piksel sawit untuk peta** *(dilewati otomatis bila raster Descals
tak ada)*
```bash
python script/gen_descals_tiles.py
```
→ `data/tiles/descals/*.png` (tile XYZ, dipakai toggle sawit di peta web).
`gen_descals_tiles.py` meng-`import` `DESCALS_DIR` dari `attribution_sawit.py`
(harus berada di folder `script/` yang sama). Murni pekerjaan penyajian
(merender gambar) — tak menyentuh `kalimantan.db` sama sekali.

**16 — Sinkronisasi geojson (untuk QGIS)**
```bash
python script/sync_geojson_from_db.py
```
→ regenerasi `data/wiup/kalimantan_with_loss.geojson` (825 konsesi + loss per
tahun) langsung dari `kalimantan.db` — dipakai panduan QGIS. Nama properti
mengikuti kolom berjendela eksplisit basis data (`loss_2001_2025_ha`,
`loss_2009_2025_ha`, dst.).

**17 — Perbarui angka narasi dashboard (JSON)**
```bash
python script/gen_dashboard_stats.py --out data/dashboard-stats.json
```
→ berkas JSON sumber satu-satunya angka narasi frontend repo utama (loss
total, %, jumlah konsesi, dsb.). **Khusus bundel ini**: folder `webapp/` tidak
disertakan, jadi pakai argumen `--out` seperti di atas (tanpa `--out`, default
skrip menulis ke `webapp/src/generated/dashboard-stats.json` — folder itu akan
dibuat otomatis, tapi tak ada yang membacanya di sini). Isi skrip **tidak
diubah** dari repo utama; hanya cara pemanggilannya yang berbeda.

### Penutup — verifikasi (bukan langkah pipeline; tak mengubah isi data)

```bash
# 1) Mode journal DB harus DELETE, bukan WAL (WAL menjatuhkan server read-only)
python script/check_db_journal.py data/kalimantan.db data-full/kalimantan.db --fix

# 2) Invarian angka analisis — dibuka KETAT baca-saja (mode=ro), exit 1 bila FAIL
python script/verify_invariants.py --db data/kalimantan.db --stats data/dashboard-stats.json
python script/verify_invariants.py --db data-full/kalimantan.db --light
```
`verify_invariants.py` menegakkan identitas internal yang harus berlaku untuk
rebuild mana pun (825 konsesi; identitas jendela Descals; sawit ≤ loss per
baris; rekonsiliasi `periode_ringkasan` vs hitung-ulang; `column_meta` 100%
dua arah; jangkar angka utama 1.603.251 ha — matikan dengan `--no-expect`
bila Anda sengaja memakai data berbeda). `--light` untuk `data-full/` yang
lapisan sawit/klasifikasinya memang cangkang kosong. Argumen `--stats` menerima
path JSON langkah 17 (di repo utama path-nya `webapp/src/generated/…`).

**Invarian tambahan untuk 5 tabel irisan Fase C:**

- **`backtrack-wilayah-rekonsil`** — Σ provinsi = Σ kabupaten = Σ komoditas
  rinci = Σ `backtrack_kohort`, diuji untuk **ketiga aturan**, toleransi 1 ha.
  Inilah pengaman pemecahan kabupaten gabungan: kalau pembagian hektarnya
  bocor, keempat jalur agregasi itu tak akan lagi ketemu.
- **`backtrack-zona-monoton`** — `n_kab_bersih` monoton tak naik sepanjang
  2009–2025 dan cacahnya rekonsil terhadap 56 kab/kota master.
- **`backtrack-keparahan-rekonsil`** — Σ ember + `n_tanpa_penyebut` = jumlah
  konsesi aktif @2025 di tiap aturan.
- **`backtrack-top-urut`** — **75 baris** `backtrack_konsesi_top` berperingkat
  menurun rapi di tiap aturan (25 teratas × 3 aturan; ambangnya dinaikkan dari
  10 ke 25 pada Fase T).
- Cek **non-negatif** diperluas ke kelima tabel baru.

**Lima invarian tambahan Fase T** (semuanya *skip*-aman: bila tabel/kolomnya
absen — mis. DB lama — invarian menulis **WARN**, bukan FAIL, lewat helper
`tabel_ada()` dan `kolom_ada()` yang baru ditambahkan):

- **`backtrack-tak-terlihat-rekonsil`** — untuk tiap (aturan × kohort SK):
  `tak_terlihat_ha` + `loss_terhitung_ha` = `loss_2009_2025_ha`. Penyebutnya
  **bebas metode**, jadi ini yang menjamin persen "tak terlihat" ketiga metode
  benar-benar sebanding.
- **`backtrack-semua-bebas-metode`** — baris kohort `SEMUA` harus sama dengan
  seluruh 825 konsesi sejak 2009 (**1.228.077 ha**) di ketiga aturan; penyebut
  bebas metode wajib utuh, bukan hasil penjumlahan yang bocor.
- **`backtrack-selisih-cacah`** — Σ ember tiap (aturan, jenis) = 825 konsesi;
  tak ada konsesi yang hilang saat dibagi ke ember jarak-tahun.
- **`backtrack-lorenz`** — kurva Lorenz **monoton naik**, berakhir persis 100%
  di persentil 100 (toleransi 0,01), dan `gini_loss` / `gini_luas` ∈ [0,1].
- **`backtrack-korelasi`** — semua `pearson` & `spearman` ∈ [−1,1]; dan
  `besar_efek_r` ∈ [−1,1] **serta hanya ada di baris `mann_whitney_holm`** —
  wajib NULL di baris `kruskal_wallis` (uji lintas 3 grup tak punya U
  berpasangan). Ini yang mencegah besar efek salah dilekatkan pada uji global.

**Gerbang yang harus lolos** pada snapshot yang di-commit (jalankan perintah di
atas; hasilnya harus sama):

```
data/kalimantan.db          → 54 pemeriksaan · 53 PASS · 1 WARN · 0 FAIL
data-full/ (--light)        → 36 pemeriksaan · 35 PASS · 1 WARN · 0 FAIL
```

Satu **WARN** itu memang diharapkan, bukan kegagalan: `identitas-jendela-descals`
menandai 2 konsesi yang selisih pembulatannya melewati toleransi 0,01 ha
(agregat 1,55 ha — jauh di bawah pagar 5 ha). `verify_invariants.py` hanya
`exit 1` bila ada **FAIL**.

### Langkah pelengkap & opsional (di luar 17 langkah)

- **`prep_bps_boundaries.py`** — *(opsional)* bangun ulang batas kabupaten dari
  geoBoundaries. Hasilnya sudah disertakan.
- **`make_charts.py` + `trend_analysis.py`** — *(opsional)* figur PNG + uji tren
  Mann-Kendall untuk naskah; tidak dipakai web app.

### Replikasi peta di QGIS (opsional)

Bundel ini juga bisa dipakai membangun **peta interaktif di QGIS** yang
tampilannya sepadan dengan halaman Peta web app (piksel loss Hansen berwarna
per tahun, jendela era Minerba 2009–2025, outline konsesi, slider tahun).
Urutannya:

1. **Jalankan pipeline sampai selesai** (bagian 3 di atas) → `data/kalimantan.db`
   jadi. Raster Hansen sudah terunduh di `data/raster/` sejak langkah 4
   (`download_hansen.py`).
2. **Ekspor geojson konsesi dari DB** (= langkah 16):
   ```bash
   python script/sync_geojson_from_db.py
   ```
   → `data/wiup/kalimantan_with_loss.geojson` (825 konsesi; properti a.l.
   `iup_year`, `loss_2009_2025_ha`, `loss_2001_ha`…`loss_2025_ha`) — inilah
   layer poligon untuk QGIS.
3. **Di QGIS**: gabungkan 4 TIF `lossyear` jadi satu VRT (*Raster →
   Miscellaneous → Build Virtual Raster*), clip ke poligon konsesi (*GDAL →
   Clip raster by mask layer*, NoData `255`), lalu jalankan
   **`script/qgis_loss_slider.py`** dari Python Console QGIS (edit `SRC_NAME`
   sesuai nama layer). Skrip membuat 17 layer "Loss s.d. 2009…2025" dengan
   **warna per tahun persis peta web** (tabel `YEAR_HEX` di dalam skrip) —
   aktifkan *Temporal Controller* rentang 2009-01-01 → 2026-01-01, step
   1 years, untuk slider tahunnya. Basemap padanan web: XYZ
   `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`;
   batas kabupaten: `data/boundaries/kalimantan-kabupaten.geojson`.

Yang **tidak** disertakan bundel: raster Hansen (unduh sendiri via
`download_hansen.py`, langkah 4) dan raster sawit Descals (via
`fetch_descals.py`) — tanpa Descals, lapisan piksel sawitnya saja yang tak
bisa direplikasi. Skrip `qgis_loss_slider.py` **byte-identik** dengan repo
utama; hanya bisa dijalankan **di dalam QGIS** (butuh modul `qgis.core`).

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

**Tabel inti** (langkah 1–3 rakit+saring registry; langkah 8–9 tempel pengukuran):

| Tabel | Isi |
|---|---|
| `wiup_geoportal` | poligon konsesi |
| `wiup_loss` | kehilangan agregat per konsesi, kolom berjendela eksplisit: `loss_2001_2025_ha`, `loss_pct_poligon_2001_2025`, `loss_2001_2025_pct_hutan2000` + jendela era Minerba `loss_2001_2008_ha` / `hutan_2009_ha` / `loss_2009_2025_ha` / `loss_2009_2025_pct_hutan2009` |
| `wiup_loss_yearly` | kehilangan per (konsesi, tahun) 2001–2025 |
| `wiup_temporal` | laju pra/pasca tahun izin + verdict (`rate_2001_sampai_tahun_izin_ha_per_year`, `rate_tahun_izin_sampai_2025_ha_per_year`, …) |
| `wiup_match` | pencocokan ke MinerbaOne |
| `badan_usaha` / `perizinan` | 7.572 perusahaan / 8.461 izin |
| `kepadatan_penduduk` | **bentuk long** (Fase G): 1 baris per (`kode_kabkot`, `tahun`) — 56 kab/kota × 10 tahun = **560 baris**, kolom `tahun` + `kepadatan`. Dulu kolom lebar `d2015`…`d2024` (56 baris). Berkas sumber `data/kepadatan_penduduk.csv` **tetap** bentuk lebar; unpivot terjadi saat ingest di `build_combined_db.py` |
| view `wiup_master` | gabungan semua (dibaca API/web) |

**Tabel lapisan pemeriksa** (langkah 10–12; cangkang `atribusi_sawit` &
`klasifikasi_izin` dibuat di langkah 1 lewat `LAPISAN_SHELLS` lalu diisi
`attribution_sawit.py` / `klasifikasi_perpanjangan.py`; tabel atribusi izin
dibuat langsung oleh `build_atribusi_izin.py` — `wiup_master` tetap valid
walau lapisan belum diisi):

| Tabel | Isi |
|---|---|
| `atribusi_sawit` | per konsesi (825): pecahan loss beririsan tahun-tanam sawit Descals — 3 varian jendela toleransi window 2001–2021 (`loss_sawit_tol2th_2001_2021_ha` dkk.), jendela Minerba (`loss_2009_2021_ha`, `loss_sawit_2009_2021_ha`), sisa tak-terperiksa `loss_2022_2025_ha`, + 3 kolom silang sawit × pra/pasca-tahun-izin |
| `atribusi_sawit_yearly` | pecahan `atribusi_sawit` per (kode_wiup, tahun) — berhenti persis 2021; dasar basis "bersih" & varian `periode_*_bersih` |
| `klasifikasi_izin` | per konsesi: vonis IZIN_PERTAMA / PERPANJANGAN / TAK_DINILAI + kekuatan bukti (KUAT/INDIKASI) |
| `atribusi_izin_aktif` | **bentuk unpivot** (Fase G): 1 baris per (konsesi, aturan) — 825 × 3 = **2.475 baris**; kolom `aturan` ∈ TANPA_ATRIBUSI/INDIKASI/POLOS, `mulai`, `loss_mulai_sampai_2025_ha`. Konsesi tanpa jangkar (7): `mulai` & loss **NULL** (bukan 0) |
| `atribusi_izin_aktif_ringkas` | 3 baris — Σ loss per aturan TANPA_ATRIBUSI/INDIKASI/POLOS + pct terhadap hutan-2009 (1.228.077 / 1.038.362 / 589.487 ha = 34,42% / 29,10% / 16,52%) |

**Tabel laju & backtrack** (langkah 13; jantung kerangka 3 metode):

| Tabel | Isi |
|---|---|
| `laju_izin_konsesi` | per konsesi (825): `mulai` versi Deteksi Hansen (`CITRA`), `dasar_mulai` (BUKTI/IZIN), `tahun_bukti`, hutan saat mulai, laju ha/thn & %/thn dua basis (kotor ≤2025, bersih tanpa-sawit ≤2021) |
| **view** `laju_izin_ringkas` | **VIEW** (Fase G) = `backtrack_laju_ringkas` WHERE `aturan='CITRA'` — distribusi laju (median/mean/persentil) per basis × dimensi (semua/kelas/periode) × kelompok |
| `laju_izin_eventstudy` | loss per tahun-relatif-SK (rel_year −10…+16) per kelas izin; t=0 PERPANJANGAN = SK perpanjangan (sisi pra tercemar — kurva bersih-tafsir = IZIN_PERTAMA). Status **ARSIP** di `analysis_meta` |
| **view** `konsesi_aktif_tahunan` | **VIEW** (Fase G) = `backtrack_tahunan` WHERE `aturan='CITRA'` — deret n konsesi mulai-aktif vs n SK terbit per tahun (+ n aktif-sebelum-SK) |
| `backtrack_tahunan` | deret tahunan per **aturan** (CITRA/INDIKASI/POLOS): n aktif, loss, loss tanpa-sawit, hutan awal tahun |
| `backtrack_kohort` | *(eks `backtrack_periode`)* Σ loss `[mulai, 2025]` per aturan × **kohort SK** — kolom kuncinya kini bernama `kohort` (Pra-2009/P1/P2/P3/TANPA_PERIODE), bukan `periode`, supaya tak tertukar dengan jendela kalender |
| `backtrack_periode_kalender` | **kerangka utama**: loss per aturan × **jendela kalender** P1 2009–2014 / P2 2015–2019 / P3 2020–2025 + `loss_tanpa_sawit_sampai_2021_ha`, `loss_2022_2025_belum_terperiksa_ha` (P3), n & luas & gini konsesi aktif |
| `backtrack_komoditas` | sel kohort × {BATUBARA, MINERAL LOGAM} per aturan; sejak Fase C ditambah penyebut `hutan_2009_ha` + `pct_hutan2009_mulai_aktif_sampai_2025` |
| `backtrack_klasifikasi` | sel kohort × kelas izin per aturan |
| `backtrack_stok` | stok izin-aktif per aturan: n, luas, hutan, loss flow & kumulatif sejak 2009. Kolom `grup_tipe` ∈ `kohort` (eks nilai `'periode'`, diganti Fase G) / `penerbit` |
| `backtrack_sawit` | pangsa sawit per aturan × periode; penyebut = loss `[mulai, 2021]` (batas Descals) |
| `backtrack_laju_ringkas` | distribusi laju ha/thn & %/thn per aturan × basis × dimensi — sumber angka rekonsiliasi 3 metode (Deteksi Hansen/CITRA 1.227.970 ha, n=825 · INDIKASI 1.038.362 ha, n=818 · POLOS 589.487 ha, n=814) |
| `backtrack_distribusi` | polarisasi ukuran per aturan: total/mean/median/**Gini** (rumus selisih-berpasangan) untuk metrik `luas_sk` & `ditambang` (± tanpa-sawit). Revisi 16 Agu: kolom `total_ha` ditambah; metrik `luas_sk_tanpa_sawit` DIHAPUS — luas SK fakta administratif poligon, koreksi sawit hanya utk deforestasi |
| `backtrack_signifikansi` | **24 baris** — Kruskal–Wallis + Mann–Whitney (Holm) antar P1/P2/P3 per aturan × metrik (loss, laju_pct); kosong bila scipy absen. Sejak Fase T membawa **`besar_efek_r`** (rank-biserial `1 − 2·U/(n_a·n_b)`) — terisi hanya di baris `mann_whitney_holm`, NULL di `kruskal_wallis`. Tanda positif = grup A bernilai lebih RENDAH dari grup B |

**Enam tabel penopang "Temuan"** (Fase T, langkah 13 juga — kekokohan
kesimpulan & pengakuan batas metode):

| Tabel | Isi |
|---|---|
| `backtrack_lorenz` | **33 baris** = 3 aturan × 11 titik (persentil 0,10,…,100). `pangsa_loss_teratas_pct` vs `pangsa_luas_teratas_pct` + `gini_loss` / `gini_luas` (diulang tiap baris — sifat sebaran, bukan sifat titik kurva). `n_konsesi` = ⌈persentil% × n⌉, pembulatan **ke atas** |
| `backtrack_kesepakatan` | **6 baris** = 3 pasangan metode (CITRA–INDIKASI, CITRA–POLOS, INDIKASI–POLOS) × 2 metrik (`loss_ha`, `pct_thn`): `pearson`, `spearman`, `n_irisan_top10`. Spearman dihitung stdlib (Pearson atas peringkat rata-rata), **tidak** butuh scipy |
| `backtrack_tak_terlihat` | **21 baris** = 3 aturan × 7 kohort (Pra-2009/P1/P2/P3/TANPA_TAHUN_SK/SK_LUAR_JENDELA/**SEMUA**): berapa kehilangan 2009–2025 yang jatuh **sebelum** jam tiap metode mulai. Penyebut `loss_2009_2025_ha` sengaja **bebas metode** supaya persen ketiga metode sebanding |
| `backtrack_selisih` | **37 baris** — blok `selisih` (6 ember jarak `iup_year` − tahun mulai: tak terdefinisi / ≤0 / 1–2 / 3–5 / 6–10 / 11+), `selisih_ringkas` (p25/median/p75/maks), `klem` (berapa konsesi bertahun-mulai persis 2009 = batas bawah jendela), dan `tahun_bukti` (13 baris, **hanya aturan CITRA**: cacah tahun bukti mentah sebelum diklem) |
| `backtrack_tahun_ekstrem` | **36 baris** = 3 aturan × 2 metrik × 2 arah (`puncak`/`palung`) × 3 peringkat — tahun tertinggi & terendah deret 2009–2025 tiap metode, disimpan terurut supaya penyaji tak mengurut ulang |
| `backtrack_top_union` | **20 baris** — gabungan 10-besar KETIGA metode, satu baris per konsesi. `peringkat_citra/indikasi/polos` adalah peringkat **penuh** (1..n seluruh konsesi metode itu, bukan 1..10) supaya "peringkat 1 di Deteksi Hansen, peringkat 94 di Polos" terbaca; `n_top10_metode` = di berapa metode ia masuk 10 besar (hanya **1 konsesi** masuk di ketiganya) |

**Lima tabel irisan halaman Statistik** (Fase C, langkah 13 juga; semua × 3
aturan, jendela `[mulai versi aturan, 2025]`, penyebut persen = `hutan_2009_ha`):

| Tabel | Isi |
|---|---|
| `backtrack_wilayah` | **186 baris** = 3 aturan × (1 `total` + 5 `provinsi` + 56 `kabupaten`), dibedakan kolom `tingkat`. Kabupaten gabungan dipecah & hektarnya dibagi rata (logika ini pindah dari klien ke pipeline). Baris `tingkat='total'` juga membawa dekomposisi sawit (`loss_sawit_mulai_aktif_sampai_2021_ha`, `persen_sawit_mulai_aktif_sampai_2021`) + `loss_2022_2025_belum_terperiksa_ha` |
| `backtrack_komoditas_rinci` | 3 × 13 komoditas = **39 baris** (BATUBARA, BAUKSIT(+DMP), EMAS(+DMP), BIJIH BESI(+DMP), BESI, ZIRKON, TIMAH, MANGAN, ANTIMONI, INTAN ALLUVIAL): n konsesi, luas SK, hutan-2009, loss (kotor & tanpa-sawit), % hutan-2009 |
| `backtrack_konsesi_top` | 3 × 25 = **75 baris** — 25 konsesi teratas per aturan (`TOP_N` dinaikkan dari 10 ke 25 pada Fase T). **Peringkatnya disimpan di basis data** (kolom `peringkat`), bukan diurutkan ulang di penyaji; lengkap dgn `nama_usaha`, `komoditas`, `nama_prov`, `mulai_aktif` |
| `backtrack_keparahan` | 3 × 5 ember = **15 baris** — sebaran konsesi menurut % hutan-2009 yang hilang (0–10 / 10–25 / 25–50 / 50–75 / 75%+), plus **`n_tanpa_penyebut`** (konsesi tanpa hutan-2009 → tak bisa dipersenkan; saat ini 0 di ketiga aturan) |
| `backtrack_zona_bebas` | 3 × 17 tahun (2009–2025) = **51 baris** — per aturan × `year`: `n_kab_total` (56), `n_kab_ada_konsesi`, `n_kab_bersih` + daftar nama (`kab_bersih` / `kota_bersih`) |

**Tabel analisis kohort-SK + provenansi** (langkah 14; turunan — bisa dibangun
ulang kapan pun):

| Tabel | Isi |
|---|---|
| `periode_ringkasan` (+`_bersih`) | ringkasan per periode kohort SK: n, luas, loss, %poligon, %akselerasi, korelasi (varian bersih: loss 2001–2021 tanpa sawit) |
| `periode_tahunan_aktif` (+`_bersih`) | deret stok izin-aktif per periode-tahun (varian bersih berhenti 2021). Tetap **tabel** (bukan view), ditandai **PROYEKSI** di `analysis_meta`; varian `_bersih` **ARSIP** |
| `penerbit_tahunan_aktif` | idem per PENERBIT (Bupati/Gubernur/Menteri; termasuk pra-2009). Ditandai **PROYEKSI** |
| `periode_slope` | slope OLS loss~tahun per periode berbasis izin-aktif (since-permit) + tahun puncak |
| `periode_komoditas` (+`_bersih`) | metrik per periode × grup (batubara vs mineral logam) |
| `periode_sawit` | Σ kolom `atribusi_sawit` per periode (pangsa sawit per periode, window 2001–2021) |
| `periode_klasifikasi` + `periode_klasifikasi_uji` | sebaran kelas izin per periode + uji Fisher exact antar periode |
| `periode_signifikansi` (+`_bersih`) | Kruskal–Wallis + Mann–Whitney (Holm) antar P1/P2/P3 |
| `baseline_tahunan` | deret loss seluruh konsesi 2001–2025 tanpa filter jendela izin (konteks) |
| `analysis_meta` | **provenance** semua tabel turunan (sumber, metode, skrip) + kolom **`status`** — **54 baris**: AKTIF (42) / ARSIP (10) / PROYEKSI (2) |
| `column_meta` | **kamus kolom dua arah**: arti + rumus + sumber tiap kolom semua tabel/view — **604 baris menutup 55 tabel**, diverifikasi 100% dua arah terhadap `PRAGMA table_info` oleh `verify_invariants.py` |

> **Mana yang masih menopang kesimpulan?** Lihat kolom `status` di
> `analysis_meta` — jangan menebak dari nama tabel. Yang ber-status **ARSIP**
> (10, tetap dibangun tapi tak dipakai menyimpulkan): `laju_izin_eventstudy`,
> `periode_ringkasan_bersih`, `periode_tahunan_aktif_bersih`,
> `periode_komoditas`, `periode_komoditas_bersih`, `periode_sawit`,
> `periode_klasifikasi`, `periode_klasifikasi_uji`, `periode_signifikansi`,
> `periode_signifikansi_bersih`. Yang **PROYEKSI** (2):
> `periode_tahunan_aktif`, `penerbit_tahunan_aktif`. Sisanya (42) **AKTIF**.

Asal-usul tiap tabel analisis dapat dilacak langsung:
```bash
sqlite3 data/kalimantan.db "SELECT nama_tabel, status, sumber, metode FROM analysis_meta"
# hanya yang menopang kesimpulan:
sqlite3 data/kalimantan.db "SELECT nama_tabel FROM analysis_meta WHERE status='AKTIF'"
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
- **Descals dkk. 2024** (CC BY 4.0): peta tahun-tanam sawit berhenti **2021** —
  kehilangan 2022–2025 **tak terperiksa** terhadap sawit; jangan masukkan ke
  penyebut persen sawit mana pun.

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
