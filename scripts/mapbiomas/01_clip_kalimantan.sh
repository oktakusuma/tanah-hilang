#!/usr/bin/env bash
# STATUS  : USANG — generasi-1 paket MapBiomas; digantikan scripts/mapbiomas/fetch_mapbiomas_lulc.py dan pipeline/04_mapbiomas.py
# CATATAN : lihat catatan di 01_clip_kalimantan.py
# LABEL   : 2 Sep 2026 (DB v3 tanah-hilang.db — CLAUDE/AUDIT-pipeline.md; jangan dihapus, tidak dipanggil pipeline/bangun.sh)
# Potong MapBiomas Indonesia Koleksi 4.1 ke Kalimantan, langsung dari cloud.
# File sumbernya Cloud-Optimized BigTIFF dan server-nya mendukung HTTP Range,
# jadi GDAL hanya mengunduh tile yang bersinggungan dengan cutline —
# bukan 200 MB per tahun. Total transfer untuk 5 provinsi Kalimantan
# biasanya di kisaran 30-60 MB per tahun, bukan 7,8 GB untuk semua tahun.
#
# Prasyarat: GDAL >= 3.1 (ikut QGIS, atau `conda install -c conda-forge gdal`)
# Jalankan: bash 01_clip_kalimantan.sh
set -euo pipefail

BASE="https://storage.googleapis.com/mapbiomas-downloads/public/indonesia/maps"
CUTLINE="${CUTLINE:-batas/kalimantan_kabupaten.gpkg}"   # ganti sesuai file batasmu
CUTLAYER="${CUTLAYER:-kalimantan_kabupaten}"
OUTDIR="${OUTDIR:-clip_wgs84}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$HERE/manifest_c41_national.csv}"

# CRS sama-luas untuk perhitungan hektar (silinder sama-luas, meridian tengah 115E).
# Piksel hasilnya persis 30 x 30 m = 900 m^2, jadi luas = jumlah piksel * 0,09 ha.
EQAREA='+proj=cea +lat_ts=0 +lon_0=115 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'
OUTDIR_EA="${OUTDIR_EA:-clip_equalarea}"

mkdir -p "$OUTDIR" "$OUTDIR_EA"

# GDAL/curl tuning untuk /vsicurl
export GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR
export CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif
export GDAL_HTTP_MULTIPLEX=YES
export VSI_CACHE=TRUE
export VSI_CACHE_SIZE=100000000

tail -n +2 "$MANIFEST" | while IFS=, read -r YEAR UUID VINTAGE; do
  [ -z "${YEAR:-}" ] && continue
  # Sanitasi ringan — manifest memang in-repo, tapi nilai aneh tak boleh masuk path/URL.
  case "$YEAR" in *[!0-9]*|"") echo "lewati baris aneh: YEAR='$YEAR'" >&2; continue;; esac
  case "$UUID" in *[!0-9a-f-]*|"") echo "lewati baris aneh: UUID='$UUID'" >&2; continue;; esac
  SRC="/vsicurl/${BASE}/${UUID}/${YEAR}_coverage_lclu_4-1-1_${UUID}.tif"
  DST="${OUTDIR}/kalimantan_lulc_${YEAR}.tif"
  DST_EA="${OUTDIR_EA}/kalimantan_lulc_${YEAR}_cea.tif"

  if [ -f "$DST_EA" ] && [ -f "$DST" ]; then echo "skip $YEAR (sudah ada)"; continue; fi
  echo "=== $YEAR (export $VINTAGE) ==="

  # Tulis ke nama sementara lalu rename setelah sukses, supaya proses yang
  # terbunuh di tengah tidak meninggalkan file parsial yang run berikutnya
  # di-skip sebagai "sudah ada". (Pola yang sama dengan .part di skrip fetch.)

  # (a) versi WGS84 untuk overlay/peta
  gdalwarp -overwrite -of GTiff \
    -cutline "$CUTLINE" -cl "$CUTLAYER" -crop_to_cutline \
    -dstnodata 0 -ot Byte -r near \
    -co COMPRESS=DEFLATE -co PREDICTOR=2 -co TILED=YES \
    -co BIGTIFF=IF_SAFER \
    "$SRC" "$DST.tmp"
  mv "$DST.tmp" "$DST"

  # (b) versi sama-luas untuk hitung hektar
  gdalwarp -overwrite -of GTiff \
    -t_srs "$EQAREA" -tr 30 30 -tap -r near \
    -dstnodata 0 -ot Byte \
    -co COMPRESS=DEFLATE -co PREDICTOR=2 -co TILED=YES \
    "$DST" "$DST_EA.tmp"
  mv "$DST_EA.tmp" "$DST_EA"
done

echo "Selesai. Cek: gdalinfo -stats ${OUTDIR}/kalimantan_lulc_2009.tif"
