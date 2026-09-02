#!/usr/bin/env bash
# Orkestrator pipeline v3 → data/tanah-hilang.db (himpunan minerba, dipakai app)
#                        → data/tanah-hilang-lengkap.db (himpunan lengkap, +galian C)
#
# Prinsip (pipeline/SKEMA.md): gagal keras bila prasyarat absen, tiap langkah idempoten,
# tiap skrip --db eksplisit, tanpa path hard-coded, provenansi ditulis oleh pemilik tabel.
#
#   bash pipeline/bangun.sh                    # kedua himpunan, dari nol
#   bash pipeline/bangun.sh --himpunan minerba # satu himpunan
#   bash pipeline/bangun.sh --dari 04          # lanjut dari langkah 04 (DB sudah ada)
#   bash pipeline/bangun.sh --sampai 08        # berhenti setelah langkah 08
#   bash pipeline/bangun.sh --tanpa-md5        # lewati verifikasi MD5 raster MapBiomas
#
# Pipeline ARSIP (kerangka pra-pivot, kalimantan.db + mapbiomas.db lama) tetap di
# rescrape/process.sh dan TIDAK dipanggil dari sini.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"; [ -x "$PY" ] || PY=python3

HIMPUNAN_SEMUA=(minerba lengkap); DARI="00"; SAMPAI="99"; EXTRA_PRASYARAT=()
while [ $# -gt 0 ]; do
  case "$1" in
    --himpunan) HIMPUNAN_SEMUA=("$2"); shift 2;;
    --dari) DARI="$2"; shift 2;;
    --sampai) SAMPAI="$2"; shift 2;;
    --tanpa-md5|--tanpa-mapbiomas) EXTRA_PRASYARAT+=("$1"); shift;;
    *) echo "argumen tak dikenal: $1" >&2; exit 2;;
  esac
done

db_untuk() { case "$1" in minerba) echo data/tanah-hilang.db;; lengkap) echo data/tanah-hilang-lengkap.db;; esac; }
langkah() { [ "$1" \< "$DARI" ] && return 1; [ "$1" \> "$SAMPAI" ] && return 1; echo; echo "== [$1] $2 (${HIM:-semua})"; return 0; }

echo "[bangun.sh] 00 prasyarat…"; "$PY" pipeline/00_prasyarat.py "${EXTRA_PRASYARAT[@]}"

for HIM in "${HIMPUNAN_SEMUA[@]}"; do
  DB="$(db_untuk "$HIM")"
  if [ "$DARI" = "00" ] && [ -f "$DB" ]; then echo "[bangun.sh] hapus $DB (bangun dari nol)"; rm -f "$DB" "$DB-journal"; fi
  langkah 01 "identitas: konsesi, registri, sumber, kepadatan"      && "$PY" pipeline/01_identitas.py      --db "$DB" --himpunan "$HIM"
  langkah 02 "hansen: ringkas, tahunan, izin_laju"                   && "$PY" pipeline/02_hansen.py         --db "$DB" --himpunan "$HIM"
  langkah 03 "izin_klasifikasi (indikasi)"                           && "$PY" pipeline/03_izin.py           --db "$DB" --himpunan "$HIM"
  langkah 04 "mapbiomas_tahunan + kelas + gabungan"                  && "$PY" pipeline/04_mapbiomas.py      --db "$DB" --himpunan "$HIM"
  langkah 05 "transisi: kohort, konsesi, pasangan"                   && "$PY" pipeline/05_transisi.py       --db "$DB" --himpunan "$HIM"
  langkah 06 "kawasan hutan & IPPKH"                                 && "$PY" pipeline/06_kawasan_hutan.py  --db "$DB" --himpunan "$HIM"
  langkah 07 "umur izin"                                             && "$PY" pipeline/07_umur_izin.py      --db "$DB" --himpunan "$HIM"
  langkah 08 "keyakinan pra-izin"                                    && "$PY" pipeline/08_keyakinan.py      --db "$DB" --himpunan "$HIM"
done

# Sajikan & verifikasi (stats JSON memuat kedua himpunan; geojson QGIS dari minerba)
langkah 09 "sajikan: geojson QGIS + dashboard-stats.json" && "$PY" pipeline/09_sajikan.py \
  --db data/tanah-hilang.db --db-lengkap data/tanah-hilang-lengkap.db
for HIM in "${HIMPUNAN_SEMUA[@]}"; do
  DB="$(db_untuk "$HIM")"
  ARSIP=(); if [ "$HIM" = minerba ]; then ARSIP=(--arsip data/arsip/kalimantan.db --arsip-mapbiomas data/arsip/mapbiomas.db); fi
  langkah 10 "verifikasi invarian + paritas arsip" && "$PY" pipeline/10_verifikasi.py --db "$DB" --himpunan "$HIM" \
      --stats webapp/src/generated/dashboard-stats.json "${ARSIP[@]}"
done
echo; echo "[bangun.sh] selesai."
