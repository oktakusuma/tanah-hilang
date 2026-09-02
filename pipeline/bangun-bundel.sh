#!/usr/bin/env bash
# Pembungkus tipis `bangun.sh` untuk BUNDEL PUBLIK.
#
# `bangun.sh` sengaja dibiarkan byte-identik dengan repo pengembangan supaya mudah
# disinkronkan. Dua hal yang hanya berlaku di repo itu: keluaran statistik ditulis ke
# webapp/src/generated/ (tak ada web app di bundel ini) dan langkah 10 membandingkan
# hasilnya dengan basis data arsip generasi lama (tak disertakan di bundel).
# Skrip ini menjalankan langkah 01-09 lewat bangun.sh, lalu memanggil verifikasi
# tanpa pembanding arsip dan dengan berkas statistik di dalam data/.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
STATS="${STATS:-data/dashboard-stats.json}"

bash pipeline/bangun.sh --sampai 08 "$@"
"$PY" pipeline/09_sajikan.py --db data/tanah-hilang.db --db-lengkap data/tanah-hilang-lengkap.db \
      --geojson data/wiup/kalimantan_with_loss.geojson --stats "$STATS"
for H in minerba lengkap; do
  case "$H" in minerba) DB=data/tanah-hilang.db;; lengkap) DB=data/tanah-hilang-lengkap.db;; esac
  [ -f "$DB" ] || continue
  "$PY" pipeline/10_verifikasi.py --db "$DB" --himpunan "$H" --stats "$STATS"
done
echo "[bangun-bundel.sh] selesai — hasil: data/tanah-hilang.db, data/tanah-hilang-lengkap.db, $STATS"
