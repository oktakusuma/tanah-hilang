# SKEMA `tanah-hilang.db` — kontrak beku (W0, 2 Sep 2026)

Ini kontrak antar-workstream. **Jangan ubah nama tabel/kolom di sini tanpa mengubah dokumen ini
dulu** dan memberi tahu pemilik workstream lain. Semua tulisan Bahasa Indonesia; tidak ada kata
"rezim"; tiap tabel & kolom wajib punya baris `analysis_meta` / `column_meta` (4-unsur: rumus,
cara pakai, apa yang ditampilkan, cara mendapatkannya) **ditulis oleh skrip pemiliknya** lewat
`pipeline/lib/meta.py`.

Keputusan igoen (2 Sep 2026): satu file per himpunan; halaman arsip dicabut; dua himpunan
(`minerba` 825 → `data/tanah-hilang.db` dipakai app; `lengkap` 1.765 termasuk galian C →
`data/tanah-hilang-lengkap.db`); nama Indonesia. Membalik aturan 23 Agu "jangan campur
MapBiomas (CC BY-SA) ke DB Hansen (CC BY)": lisensi kini dicatat **per tabel** di
`analysis_meta.lisensi` + tabel `sumber`.

Konvensi nama: snake_case Indonesia; kunci konsesi tetap `kode_wiup` (kode resmi ESDM);
tahun = `tahun`; luas = `*_ha`; kehilangan tutupan pohon = `hilang_*`; hutan = `hutan_*`;
jendela tesis 2001–2024 **tidak** ditulis di nama kolom kecuali kolom itu memang total jendela
(`hilang_2001_2024_ha`). Tidak ada kolom jendela 2025 / 2009 / Descals di DB ini.

Semua skrip: `--db PATH` wajib (tanpa default tersembunyi), `--himpunan minerba|lengkap`
bila relevan, cwd bebas (path input relatif ke akar repo via `lib/db.AKAR`), gagal keras
(`SystemExit(2)`) bila prasyarat absen — **tidak boleh** `print("lewati")` lalu exit 0.

---

## 0. Tabel infrastruktur (pemilik: `lib/meta.py` + tiap skrip)

```sql
CREATE TABLE IF NOT EXISTS sumber (
  id            TEXT PRIMARY KEY,   -- 'hansen', 'mapbiomas', 'geoportal_wiup', 'geoportal_hutan', 'minerbaone', 'bps', 'geoboundaries'
  nama          TEXT NOT NULL,
  versi         TEXT,               -- 'GFC-2025 v1.13', 'Koleksi 4.1', 'WIUP_Publish', ...
  lisensi       TEXT NOT NULL,      -- 'CC BY 4.0', 'CC BY-SA 4.0', 'data publik pemerintah', ...
  url           TEXT,
  tanggal_akses TEXT,               -- 'YYYY-MM-DD'
  cakupan_tahun TEXT,               -- '2001-2024', '2000-2024', '2015-2024'
  sitasi        TEXT,               -- teks sitasi yang wajib dikutip
  catatan       TEXT
);
CREATE TABLE IF NOT EXISTS bangun (      -- jejak build, ditulis tiap skrip (kunci berprefiks nama skrip)
  kunci TEXT PRIMARY KEY, nilai TEXT NOT NULL, ditulis TEXT NOT NULL  -- ISO timestamp
);
-- kunci wajib: 'himpunan', 'pipeline_versi', 'git_commit', 'konsesi.n', 'konsesi.hash_geometri',
--              'mapbiomas.hash_geometri' (harus == konsesi.hash_geometri), '<skrip>.selesai'
CREATE TABLE IF NOT EXISTS analysis_meta (
  nama_tabel TEXT PRIMARY KEY, deskripsi TEXT NOT NULL, sumber TEXT NOT NULL,
  metode TEXT NOT NULL, skrip TEXT NOT NULL, lisensi TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'INTI'
);
CREATE TABLE IF NOT EXISTS column_meta (
  nama_tabel TEXT NOT NULL, nama_kolom TEXT NOT NULL, deskripsi TEXT NOT NULL,
  rumus TEXT, sumber TEXT, PRIMARY KEY (nama_tabel, nama_kolom)
);
```
`hash_geometri` = sha256 atas `kode_wiup || '\n' || geometri_geojson` diurutkan `kode_wiup`
(helper `lib/db.hash_geometri(con)`). Invarian: `bangun.mapbiomas.hash_geometri == bangun.konsesi.hash_geometri`.

---

## 1. Identitas (pemilik W1: `01_identitas.py`)

```sql
CREATE TABLE konsesi (
  kode_wiup        TEXT PRIMARY KEY,
  nama_usaha       TEXT NOT NULL,
  sk_iup           TEXT,
  komoditas        TEXT NOT NULL,      -- apa adanya dari Geoportal (BATUBARA, EMAS, ...)
  jenis_izin       TEXT,               -- IUP / IUPK / PKP2B / KK ...
  kegiatan         TEXT,               -- Eksplorasi / Operasi Produksi (bila ada)
  luas_sk_ha       REAL,               -- eks luas_sk
  luas_poligon_ha  REAL NOT NULL,      -- luas geometri (eks wiup_loss.polygon_area_ha)
  tahun_izin       INTEGER,            -- eks iup_year (tahun SK yang berlaku SAAT INI, bukan izin pertama)
  tgl_berlaku      TEXT,               -- 'YYYY-MM-DD' dari layer overlay IPPKH Geoportal (eks wiup_tanggal_pulih, 795); WIUP_Publish punya tgl_berlaku sendiri tapi hanya dipakai utk tahun_izin (11 konsesi beda tahun, 602 beda 1 hari zona waktu)
  tgl_berakhir     TEXT,
  asal_tanggal     TEXT,               -- 'ippkh_pulih' | NULL (nilai 'geoportal' dicadangkan, tak muncul di data sekarang)
  provinsi         TEXT, kabupaten TEXT, kabupaten_norm TEXT,  -- eks nama_prov, nama_kab, kab_normalized
  lokasi           TEXT,
  cnc              TEXT,
  geometri_geojson TEXT NOT NULL,
  bbox_min_lon REAL, bbox_min_lat REAL, bbox_max_lon REAL, bbox_max_lat REAL
);
CREATE TABLE konsesi_registri (            -- hasil pencocokan ke MinerbaOne; 1 baris per konsesi, NULL bila tak cocok
  kode_wiup          TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  cocok              INTEGER NOT NULL,      -- 0/1 (eks db_match yes/no)
  strategi_cocok     TEXT,                  -- 'T0_exact' | 'T1_norm_sk' | 'T2_fuzzy_name' | 'T3_digits' | NULL
  id_perizinan       TEXT, id_badan_usaha TEXT,
  nama_badan_usaha   TEXT, nib TEXT, npwp TEXT, alamat TEXT, kode_pos TEXT, jenis_badan_usaha TEXT,
  tanggal_berlaku    TEXT, tanggal_berakhir TEXT, tanggal_penetapan TEXT,
  tahap_kegiatan     TEXT, status_cnc TEXT,
  url_minerbaone     TEXT
);
CREATE TABLE kepadatan_penduduk (          -- salin apa adanya (BPS 2015–2024)
  kode_kabkot TEXT, provinsi TEXT, kabupaten TEXT, kabupaten_norm TEXT,
  tahun INTEGER, kepadatan REAL, satuan TEXT, sumber TEXT,
  PRIMARY KEY (kode_kabkot, tahun)
);
```
Sumber: `data/wiup/kalimantan_unique.geojson` (1.765; saring `himpunan=minerba` dengan daftar
komoditas `scripts/filter_minerba.py::MINERBA_COMMODITIES` — salin ke `lib/himpunan.py`),
`data/minerba-kalimantan.db` (`perizinan`, `badan_usaha`), logika cocok SK-persis
(`build_combined_db.step_match` → `strategi_cocok='T0_exact'`) + `match_harder.py`
(`T1_norm_sk`, `T2_fuzzy_name`, `T3_digits`), tanggal pulih dari
`build_kawasan_hutan.py` (bagian `wiup_tanggal_pulih`; geoportal IPPKH). `luas_poligon_ha`
dari batch CSV (`polygon_area_ha`) atau dihitung ulang dengan `_geo_common` — pilih batch CSV
agar identik dgn arsip.

Paritas: `minerba` = 825 baris, `lengkap` = 1.765; `cocok=1` = jumlah `db_match='yes'` arsip.

---

## 2. Hansen (pemilik W1: `02_hansen.py`)

```sql
CREATE TABLE hansen_ringkas (
  kode_wiup           TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  hutan_2000_ha       REAL NOT NULL,   -- eks forest_2000_ha (kanopi ≥30% tahun 2000)
  hilang_2001_2024_ha REAL NOT NULL,   -- Σ hansen_tahunan 2001..2024 (identitas, assert)
  pct_hutan_2000      REAL,            -- 100*hilang/hutan_2000 (NULL bila hutan_2000=0)
  tahun_puncak        INTEGER,         -- tahun hilang_ha terbesar (NULL bila semua 0)
  tile_hansen         TEXT             -- daftar tile GFC yang overlap
);
CREATE TABLE hansen_tahunan (
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup),
  tahun     INTEGER NOT NULL CHECK (tahun BETWEEN 2001 AND 2024),
  hilang_ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, tahun)
);
CREATE TABLE izin_laju (                   -- laju sebelum vs sesudah tahun izin, jendela 2001–2024 (eks wiup_temporal *_2024)
  kode_wiup          TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  tahun_izin         INTEGER,
  hilang_pra_ha      REAL, n_tahun_pra INTEGER, laju_pra_ha_thn REAL,       -- 2001..tahun_izin-1
  hilang_pasca_ha    REAL, n_tahun_pasca INTEGER, laju_pasca_ha_thn REAL,   -- tahun_izin..2024
  rasio_pasca_pra    REAL,            -- NULL bila pra=0 & pasca=0; 'inf' disimpan sbg NULL + vonis loss_only_after_iup
  vonis              TEXT NOT NULL    -- accelerated_post_iup | decelerated_post_iup | loss_only_after_iup | stable | no_loss_either | izin_setelah_jendela | tanpa_tahun_izin
);
```
Sumber: `data/analysis/batch_KALIMANTAN_t30_wide.csv` (kolom `loss_2001_ha`..`loss_2024_ha`,
`forest_2000_ha`, `polygon_area_ha`, `tiles`); logika vonis = `scripts/temporal_iup.py`
(ambang 1,5 / 0,67) **hanya jendela 2024**. `tahun_izin` > 2024 (2025 & 2026) → `izin_setelah_jendela`;
NULL → `tanpa_tahun_izin`.

Paritas (minerba): Σ `hilang_2001_2024_ha` = **1.548.812,60 ha**; Σ hutan_2000 = 3.943.141 ha
(39,3%); distribusi vonis = arsip `verdict_jendela_2024` (accelerated 248, decelerated 263,
stable 222, no_loss_either 14, loss_only_after_iup 2, **izin_setelah_jendela 63, tanpa_tahun_izin 13** —
arsip 59/17 karena 4 SK-2026 dulu masuk 'di luar rentang'; gabungan 76 sama). `pra_izin_dominan`
berbeda pada 15 konsesi vs arsip (sisi pasca kini 2024) — kelas/bukti tak terpengaruh.

---

## 3. Klasifikasi izin (pemilik W1: `03_izin.py`)

```sql
CREATE TABLE izin_klasifikasi (            -- INDIKASI, bukan kepastian (eks klasifikasi_izin)
  kode_wiup             TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  kelas                 TEXT NOT NULL,    -- IZIN_PERTAMA | PERPANJANGAN | TAK_DINILAI
  bukti                 TEXT,             -- KUAT | INDIKASI
  dasar                 TEXT NOT NULL,
  durasi_sk             INTEGER,
  masa_berlaku_diwarisi INTEGER NOT NULL,
  pra_izin_dominan      INTEGER,
  tahun_mulai_indikasi  INTEGER            -- jam indikasi: PERPANJANGAN = tahun_izin - 20, selainnya tahun_izin; NULL tanpa tahun
);
```
Logika = `scripts/klasifikasi_perpanjangan.py` dengan input dari `konsesi` + `konsesi_registri`
+ `izin_laju.hilang_pra_ha`. Paritas: distribusi kelas/bukti = arsip `klasifikasi_izin`.
`tahun_mulai_indikasi` (25 Sep 2026, permintaan penulis tesis): tahun konsesi DIANGGAP mulai
aktif — jam bersama poligon peta & Sankey "konsesi aktif" (aturan = T41C stata; jangkar
minerba: 378 aktif ≤2001, 808 ≤2024). KAVEAT: indikasi — aturan −20 meleset pada 82% konsesi
PERPANJANGAN yang bisa diperiksa (docs/analisis/bukaan-tambang-harga-dan-umur.md §4.4).

---

## 4. MapBiomas (pemilik W2: `04_mapbiomas.py`, `05_transisi.py`)

```sql
CREATE TABLE mapbiomas_kelas (            -- legenda resmi C4.1 (satu sumber utk web, QGIS, tabel)
  kelas INTEGER PRIMARY KEY, nama TEXT NOT NULL, kelompok TEXT NOT NULL, hex TEXT NOT NULL
);
CREATE TABLE mapbiomas_gabungan (         -- kategori buatan sendiri — WAJIB ada supaya tiap pemakaian bisa diberi keterangan
  gabungan TEXT NOT NULL, kelas INTEGER NOT NULL REFERENCES mapbiomas_kelas(kelas), keterangan TEXT,
  PRIMARY KEY (gabungan, kelas)
);  -- isi awal: 'Hutan' = 3,5,76 ; 'Pertanian non-sawit' = 9,21,40 ; 'Tubuh air' = 31,33
CREATE TABLE mapbiomas_tahunan (          -- eks landuse_konsesi
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup),
  tahun     INTEGER NOT NULL CHECK (tahun BETWEEN 2001 AND 2024),
  kelas     INTEGER NOT NULL REFERENCES mapbiomas_kelas(kelas),
  piksel    INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, tahun, kelas)
);
CREATE VIEW v_mapbiomas_ringkas AS         -- per konsesi × tahun
  SELECT kode_wiup, tahun,
         SUM(CASE WHEN kelas IN (3,5,76) THEN ha ELSE 0 END) AS hutan_ha,
         SUM(CASE WHEN kelas = 30 THEN ha ELSE 0 END)        AS tambang_ha,
         SUM(CASE WHEN kelas = 35 THEN ha ELSE 0 END)        AS sawit_ha,
         SUM(CASE WHEN kelas <> 27 THEN ha ELSE 0 END)       AS teramati_ha,
         SUM(ha) AS total_ha
  FROM mapbiomas_tahunan GROUP BY kode_wiup, tahun;
CREATE TABLE transisi_kohort (            -- catatan kaki kohort (n_keluar WAJIB ditampilkan bersama angka kohort)
  jenis TEXT NOT NULL, label TEXT PRIMARY KEY, awal INTEGER NOT NULL, akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, n_keluar INTEGER NOT NULL, catatan TEXT
);
CREATE TABLE transisi_konsesi (           -- per konsesi per label kohort
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), label TEXT NOT NULL REFERENCES transisi_kohort(label),
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, label, kelas_awal, kelas_akhir)
);
CREATE VIEW v_transisi_aliran AS           -- eks v_transisi_agregat (nama kelas di-join, bukan disimpan ganda)
  SELECT t.label, t.kelas_awal, t.kelas_akhir, a.nama AS nama_awal, b.nama AS nama_akhir,
         COUNT(DISTINCT t.kode_wiup) AS n_konsesi, SUM(t.piksel) AS piksel, ROUND(SUM(t.ha),2) AS ha
  FROM transisi_konsesi t JOIN mapbiomas_kelas a ON a.kelas=t.kelas_awal JOIN mapbiomas_kelas b ON b.kelas=t.kelas_akhir
  GROUP BY t.label, t.kelas_awal, t.kelas_akhir;
CREATE TABLE transisi_pasangan (          -- agregat semua 276 pasangan tahun (a<b), 2001–2024; TIDAK boleh dijumlahkan antar langkah
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (tahun_awal, tahun_akhir, kelas_awal, kelas_akhir), CHECK (tahun_awal < tahun_akhir)
);
CREATE TABLE transisi_pasangan_aktif (    -- spt transisi_pasangan TAPI hanya konsesi AKTIF menurut jam indikasi
  tahun_awal INTEGER NOT NULL, tahun_akhir INTEGER NOT NULL,
  baru_aktif INTEGER NOT NULL CHECK (baru_aktif IN (0, 1)),    -- 1 = entran: baru aktif dalam (awal, akhir]
  kelas_awal INTEGER NOT NULL, kelas_akhir INTEGER NOT NULL,
  n_konsesi INTEGER NOT NULL, piksel INTEGER NOT NULL, ha REAL NOT NULL,
  PRIMARY KEY (tahun_awal, tahun_akhir, baru_aktif, kelas_awal, kelas_akhir),
  CHECK (tahun_awal < tahun_akhir)
);
```
`transisi_pasangan_aktif` (25 Sep 2026): konsesi ikut pasangan (a,b) bila
`izin_klasifikasi.tahun_mulai_indikasi ≤ a` (`baru_aktif = 0`); yang baru aktif dalam (a,b]
masuk dengan `baru_aktif = 1` dan `kelas_awal` = kelas lahannya yang SEBENARNYA pada a (sebelum
izinnya mulai) — revisi 26 Sep 2026 menggantikan simpul semu `kelas_awal = -1`, supaya terbaca
lahan apa yang dibawa masuk. Massa antar kolom seimbang, **tinggi kolom Sankey = luas konsesi
aktif tahun itu** (diassert di skrip: Σ sisi asal `baru_aktif = 0` = komposisi tahun a utk aktif
≤ a; Σ sisi asal `baru_aktif = 1` = komposisi tahun a utk a < mulai ≤ b; Σ sisi tujuan =
komposisi tahun b utk aktif ≤ b; per sel, Σ kedua jenis ⊆ `transisi_pasangan`). Konsesi tanpa
`tahun_izin` tak pernah masuk.
Sumber: raster `data/external/mapbiomas/mapbiomas_c41_{2001..2024}.tif` (manifest MD5
`scripts/mapbiomas/manifest_c41_2000_2024.csv`), geometri dari **`konsesi` DB target** (bukan
`kalimantan.db`). Logika = `scripts/mapbiomas/landuse_konsesi.py` + `transisi_konsesi.py`
(label kohort: `2001-2024`, `umur-10_+0`, `umur+0_+10`; t0 = max(tahun_izin, 2001)).
Kelas 27 (awan) disimpan di `mapbiomas_tahunan` tapi dikecualikan dari transisi seperti sekarang.
Fire **tidak** dibawa (nol pembaca). Tulis `bangun.mapbiomas.hash_geometri`.

Invarian (assert di skrip + `10_verifikasi.py`): untuk tiap label, Σ ha transisi (sisi awal) =
Σ `mapbiomas_tahunan` tahun_awal tanpa kelas 27, dan sisi akhir idem = **4.284.991,78 ha** utk
label 2001-2024 (minerba). Paritas Sankey 2001→2024 langsung total pita berubah (kelas_awal ≠
kelas_akhir, tanpa 27) = **1.270.225,42 ha**; lubang tambang 2024 = 157.287,46 ha. (Angka lama
1.269.862 / 4.283.529,6 / Σ 23 langkah 3.130.393 berasal dari bangun 31 Agu sebelum arsip
dibangun ulang 1 Sep — **basi**; nilai berlaku Σ 23 langkah = 3.130.393,14 ha, rasio 2,46×.

> **Definisi angka 3.130.393,14 ha** (Σ 23 langkah tahunan): dijumlahkan dari
> `transisi_pasangan` pasangan berurutan `tahun_akhir = tahun_awal + 1`, `kelas_awal <>
> kelas_akhir`, **di-join ke `mapbiomas_kelas` sehingga kelas 0 (nodata) gugur** — penyaring
> yang sama dengan pembandingnya `v_transisi_aliran` (1.270.225,46 ha). Tanpa membuang
> kelas 0 hasilnya 3.130.403,21 ha, dan itu **tidak sebanding** dengan penyebutnya
> (temuan audit 3 Sep 2026 — dua agen sempat memakai definisi berbeda).
Catatan presisi: `v_transisi_aliran` membulatkan per baris sehingga Σ-nya 1.270.225,46 ha —
selisih pembulatan 0,04 ha, bukan angka berbeda.)
**Kaveat data (himpunan lengkap):** 8 konsesi galian C berkomoditas PASIR LAUT / PASIR KUARSA
punya **nol piksel MapBiomas** — poligonnya di perairan, di luar cakupan raster darat (bukan galat).
Mereka sah tak punya baris `mapbiomas_tahunan`/`transisi_konsesi`; rekonsiliasi `05_transisi.py`
membandingkan terhadap anggota kohort **yang punya piksel**. Himpunan minerba tak terdampak (825/825
punya piksel).

**Kohort `umur-10_+0`: 624 masuk / 201 keluar** (arsip 682/143 memakai raster 2000 sebagai lantai;
v3 konsisten jendela tesis 2001–2024 sehingga t0 = 2010 tak punya tahun −10 → keluar). Kohort
`2001-2024` 825/0 dan `umur+0_+10` 268/557 sama dengan arsip. Pemilik baris `sumber.mapbiomas` = **04**.

---

## 5. Kawasan hutan & IPPKH (pemilik W3: `06_kawasan_hutan.py`)

```sql
CREATE TABLE kawasan_hutan (              -- eks konsesi_kawasan_hutan
  kode_wiup TEXT NOT NULL REFERENCES konsesi(kode_wiup), fungsi_kode TEXT, fungsi_nama TEXT NOT NULL, luas_ha REAL NOT NULL,
  PRIMARY KEY (kode_wiup, fungsi_nama)
);
CREATE TABLE ippkh (                      -- eks konsesi_ippkh; potret izin AKTIF (batas bawah), bukan register sejarah
  kode_wiup TEXT PRIMARY KEY REFERENCES konsesi(kode_wiup),
  punya_ippkh INTEGER NOT NULL, punya_ippkh_tambang INTEGER NOT NULL,
  n_ippkh INTEGER NOT NULL, n_ippkh_tambang INTEGER NOT NULL,
  luas_irisan_ha REAL, luas_ippkh_sk_ha REAL, rasio_ippkh_thd_luas_sk REAL,
  tgl_ippkh_awal TEXT, tgl_ippkh_akhir TEXT, cocok_nama INTEGER
);
CREATE TABLE ippkh_irisan (               -- eks ippkh_konsesi_irisan (audit spasial vs nama)
  kode_wiup TEXT NOT NULL, id_ippkh TEXT NOT NULL, layer TEXT, nama_ppkh TEXT, no_ppkh TEXT,
  tgl_ppkh TEXT, tgl_berakhir TEXT, jenis_ppkh TEXT, status TEXT,
  luas_ppkh_sk_ha REAL, luas_ppkh_hitung_ha REAL, luas_irisan_ha REAL, pangsa_ippkh_di_konsesi REAL, cocok_nama INTEGER,
  PRIMARY KEY (kode_wiup, id_ippkh)
);
```
Sumber: `data/geoportal/{ippkh_eksplorasi,ippkh_operasi,overlay_hutan}.geojson` + `MANIFEST.csv`.
Logika = `scripts/build_kawasan_hutan.py` (tanpa bagian `wiup_tanggal_pulih` — itu pindah ke 01).
Pemilik baris `sumber.geoportal_hutan` = **06** (membaca MANIFEST); 01 menulis sumber lain saja.
Paritas (minerba): 274 konsesi punya IPPKH, 256 tambang; HL 112.056 ha / 49 konsesi; KK 27.423 / 33.

---

## 6. Umur izin (pemilik W3: `07_umur_izin.py`)

Tabel `umur_izin_kurun`, `umur_izin_tahunan`, `umur_izin_konsesi` — **kolom sama persis** dengan
arsip (sudah Indonesia), kecuali `loss_ha` → `hilang_ha`. Input: `konsesi.tahun_izin`,
`hansen_ringkas.hutan_2000_ha`, `hansen_tahunan`. Logika = `scripts/build_umur_izin.py`
(TAHUN_MAX 2024). Kedua rancangan (`A_seimbang`, `B_semua`) dan `n_konsesi` per titik wajib.
Paritas: A_seimbang n=260, laju bahaya 1,782 → 2,392 → 2,494 %/th.

## 7. Keyakinan pra-izin (pemilik W3: `08_keyakinan.py`)

Tabel `keyakinan_pra_izin`, `keyakinan_model`, `keyakinan_ringkas` — kolom sama persis dengan
arsip kecuali: `iup_year` → `tahun_izin`, `loss_sejak_2001_ha` → `hilang_sejak_2001_ha`,
`loss_sejak_sk_ha` → `hilang_sejak_sk_ha`, `loss_harapan_ha` → `hilang_harapan_ha`; kunci
`keyakinan_ringkas` berawalan `loss_` → `hilang_`. Input: `konsesi` (tgl_berlaku/berakhir,
jenis_izin), `izin_klasifikasi`, `ippkh.tgl_ippkh_awal`, `hansen_tahunan`, `mapbiomas_tahunan`
kelas 30 (**DB yang sama**, bukan file lain), `konsesi_registri` (sinyal G). Logika =
`scripts/build_keyakinan_pra_izin.py` (benih 20260831, bootstrap 2000).
Paritas: `hilang_harapan_ha` = 1.189.114,10; batas bawah 547.714,03; batas atas 1.546.928,38;
AUC R 0,683 / RS 0,736; `peluang_akhir` identik per konsesi. **Selang bootstrap = 1.144.662,06 /
1.252.064,79** (BUKAN angka arsip 1.144.798,52 / 1.250.548,75): skrip v3 mengiterasi konsesi
`ORDER BY kode_wiup` (reproduksibel), arsip memakai urutan rowid geojson — tarikan acak terpermutasi
antar konsesi, harapan/batas/koefisien/peluang tak terpengaruh (temuan W3, terverifikasi dua arah).
Keputusan W0: pakai urutan terurut; dokumen yang mengutip selang lama diperbarui saat integrasi.

---

## 8. View gabungan (pemilik W1, dibuat setelah semua tabel: `09_sajikan.py` memastikan ada)

```sql
CREATE VIEW v_konsesi AS                  -- pengganti wiup_master; sumber tunggal API list/detail
  SELECT k.*, r.cocok, r.strategi_cocok, r.nama_badan_usaha, r.nib, r.alamat, r.jenis_badan_usaha,
         r.tanggal_berlaku AS registri_tanggal_berlaku, r.tanggal_berakhir AS registri_tanggal_berakhir, r.url_minerbaone,
         h.hutan_2000_ha, h.hilang_2001_2024_ha, h.pct_hutan_2000, h.tahun_puncak, h.tile_hansen,
         l.hilang_pra_ha, l.n_tahun_pra, l.laju_pra_ha_thn, l.hilang_pasca_ha, l.n_tahun_pasca, l.laju_pasca_ha_thn, l.rasio_pasca_pra, l.vonis,
         z.kelas AS kelas_izin, z.bukti AS bukti_izin, z.durasi_sk, z.masa_berlaku_diwarisi, z.pra_izin_dominan,
         i.punya_ippkh, i.punya_ippkh_tambang, i.tgl_ippkh_awal,
         y.peluang_akhir AS keyakinan_pra_izin, y.hilang_harapan_ha
  FROM konsesi k
  LEFT JOIN konsesi_registri r USING (kode_wiup) LEFT JOIN hansen_ringkas h USING (kode_wiup)
  LEFT JOIN izin_laju l USING (kode_wiup) LEFT JOIN izin_klasifikasi z USING (kode_wiup)
  LEFT JOIN ippkh i USING (kode_wiup) LEFT JOIN keyakinan_pra_izin y USING (kode_wiup);
```
`v_konsesi` dibuat oleh helper `lib/w1_util.pastikan_v_konsesi(con, skrip)` — dipanggil di akhir
`03_izin.py` dan lagi di `09_sajikan.py`; view hanya dibuat bila ketujuh tabel join sudah ada
(SQLite **menolak** CREATE VIEW yang merujuk tabel absen — bukan lazy). Di DB `lengkap` view
juga dibuat (tabel MapBiomas/keyakinan ada untuk himpunan lengkap).

---

## 9. Keluaran non-DB (pemilik W4: `09_sajikan.py`)

- `data/wiup/kalimantan_with_loss.geojson` (himpunan minerba; properti = kolom `v_konsesi` tanpa
  geometri + `hansen_tahunan` sbg `hilang_YYYY_ha`) — konsumen: panduan QGIS.
- `webapp/src/generated/dashboard-stats.json` skema baru:
  `{ "generated_at", "jendela": "2001-2024", "minerba": {n_konsesi, hutan_2000_ha, hilang_2001_2024_ha, pct_hutan_2000, n_provinsi, n_kabupaten, komoditas: {...}}, "lengkap": {n_konsesi, hilang_2001_2024_ha, pct_hutan_2000}, "keyakinan": {...ringkas}, "umur": {A_seimbang: [...]}, "ippkh": {n_punya, n_tambang}, "sankey_2001_2024": {total_berubah_ha}, "registri": {n_cocok, per_strategi} }`
  — tanpa kunci arsip (`periode`, `atribusi`, `lapisan`, `kohort` lama).

## 10. Verifikasi (pemilik W4: `10_verifikasi.py`)

Wajib PASS sebelum deploy: (1) skema = SKEMA.md (tabel & kolom persis); (2) `analysis_meta` &
`column_meta` 100% dua arah (kecuali `sumber`, `bangun`, kedua meta); (3) Σ `hansen_tahunan` =
`hilang_2001_2024_ha` per konsesi (tol 0,01); (4) `pct_hutan_2000` konsisten; (5) `izin_laju`
identitas pra/pasca vs `hansen_tahunan`; (6) hash geometri MapBiomas = konsesi; (7) Σ transisi =
Σ `mapbiomas_tahunan` (kedua sisi, tiap label); (8) `transisi_pasangan` punya 276 pasangan;
(9) `umur_izin_tahunan.n_konsesi > 0` di semua titik yang ada; (10) `keyakinan_ringkas`
lo ≤ harapan ≤ hi; (11) `dashboard-stats.json` = DB; (12) **paritas arsip** (`--arsip
data/arsip/kalimantan.db --arsip-mapbiomas data/arsip/mapbiomas.db`, hanya himpunan minerba):
angka jangkar §2, §4, §5, §6, §7 identik (tol 0,5 ha / 0,01 poin).
