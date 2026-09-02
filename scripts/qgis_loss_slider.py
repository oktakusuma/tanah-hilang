# Bikin slider tahun untuk raster Hansen lossyear di QGIS — replika tampilan
# halaman /peta webapp (warna per tahun, jendela tesis 2001-2024, opasitas piksel).
#
# Cara pakai:
#   1. Buka QGIS, pastikan layer VRT/clip lossyear sudah ada di panel Layers.
#   2. Ganti SRC_NAME di bawah sesuai nama layer-mu (persis seperti di panel).
#   3. Plugins -> Python Console -> ikon "Show Editor" -> buka file ini -> Run.
#   4. View -> Panels -> Temporal Controller -> klik ikon play (Animated
#      temporal navigation) -> set range 2001-01-01 s.d. 2025-01-01, step
#      1 years. Slider tahun muncul; geser = pindah tahun (padanan slider
#      "Potret Data" di peta web; posisi 2024 = "Agregat 2001-2024").
#
# WARNA PER TAHUN disalin persis dari peta web:
# webapp/src/components/map/HansenLossLayer.tsx (YEAR_LUT) — interpolasi linier
# RGB antara 3 titik: 2001 #fccee7 -> 2013 #df3da5 -> 2025 #700842, dibulatkan
# Math.round. Tabel YEAR_HEX di bawah adalah hasil interpolasi itu, byte demi
# byte; JANGAN ubah di sini saja tanpa mengubah web-nya juga.

from qgis.core import (
    Qgis,
    QgsDateTimeRange,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QDate, QDateTime, QTime
from qgis.PyQt.QtGui import QColor

SRC_NAME = "lossyear"          # <-- GANTI: nama layer VRT/clip di panel Layers
GROUP_NAME = "Loss per tahun (slider)"
CUMULATIVE = True              # True = tampilkan loss s.d. tahun N (kayak web)
                               # False = hanya loss pada tahun N itu saja
MIN_VALUE = 1                  # 1 = mulai 2001 (jendela tesis 2001-2024,
                               # sama seperti peta web).
MAX_VALUE = 24                 # 24 = berhenti 2024; kode 25 (piksel 2025) TIDAK
                               # digambar — di luar jendela tesis, seperti web.
OPACITY = 0.70                 # padanan slider opasitas layer Hansen di seksi
                               # LAYER peta web (bawaan 70%)

# Nilai piksel Hansen 1..25 = tahun hilang 2001..2025 -> warna PERSIS peta web
# (YEAR_LUT di HansenLossLayer.tsx). 2001 (#fccee7) juga titik awal gradien
# legenda di MapView.tsx; entri 25 (2025) disimpan hanya sebagai dokumentasi
# ramp — tidak dipakai selama MAX_VALUE = 24.
YEAR_HEX = {
    1: "#fccee7",   # 2001
    2: "#fac2e2",   # 2002
    3: "#f7b6dc",   # 2003
    4: "#f5aad7",   # 2004
    5: "#f29ed1",   # 2005
    6: "#f092cc",   # 2006
    7: "#ee86c6",   # 2007
    8: "#eb79c1",   # 2008
    9: "#e96dbb",   # 2009
    10: "#e661b6",  # 2010
    11: "#e455b0",  # 2011
    12: "#e149ab",  # 2012
    13: "#df3da5",  # 2013
    14: "#d6399d",  # 2014
    15: "#cd3495",  # 2015
    16: "#c3308c",  # 2016
    17: "#ba2b84",  # 2017
    18: "#b1277c",  # 2018
    19: "#a82374",  # 2019
    20: "#9e1e6b",  # 2020
    21: "#951a63",  # 2021
    22: "#8c155b",  # 2022
    23: "#831153",  # 2023
    24: "#790c4a",  # 2024
    25: "#700842",  # 2025 (tak dipakai — di luar jendela tesis)
}

project = QgsProject.instance()
matches = project.mapLayersByName(SRC_NAME)
if not matches:
    raise RuntimeError(
        f"Layer '{SRC_NAME}' tidak ketemu — cek nama di panel Layers, "
        f"lalu sesuaikan SRC_NAME di atas."
    )
src = matches[0]

root = project.layerTreeRoot()
old = root.findGroup(GROUP_NAME)
if old:
    root.removeChildNode(old)  # re-run aman: grup lama dibuang dulu
group = root.insertGroup(0, GROUP_NAME)

first_year = 2000 + MIN_VALUE
last_year = 2000 + MAX_VALUE
for n in range(MIN_VALUE, MAX_VALUE + 1):
    year = 2000 + n
    layer = QgsRasterLayer(src.source(), f"Loss s.d. {year}", src.providerType())
    if not layer.isValid():
        raise RuntimeError(f"Gagal buka ulang sumber VRT untuk tahun {year}")

    lo = MIN_VALUE if CUMULATIVE else n
    classes = [
        QgsPalettedRasterRenderer.Class(v, QColor(YEAR_HEX[v]), str(2000 + v))
        for v in range(lo, n + 1)
    ]
    # Nilai di luar daftar kelas (0 = tidak ada loss; 25 = piksel 2025 di luar jendela;
    # NoData 255 hasil clip) otomatis transparan — sama dgn web yang hanya
    # mewarnai piksel ber-loss di jendela tahunnya.
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
    layer.setOpacity(OPACITY)

    tp = layer.temporalProperties()
    tp.setIsActive(True)
    try:
        tp.setMode(Qgis.RasterTemporalMode.FixedTemporalRange)
    except AttributeError:  # QGIS < 3.30 pakai enum lama
        tp.setMode(tp.ModeFixedTemporalRange)
    tp.setFixedTemporalRange(
        QgsDateTimeRange(
            QDateTime(QDate(year, 1, 1), QTime(0, 0)),
            QDateTime(QDate(year + 1, 1, 1), QTime(0, 0)),
            includeBeginning=True,
            includeEnd=False,
        )
    )

    project.addMapLayer(layer, False)  # False = jangan taruh di root
    group.addLayer(layer)

print(
    f"Beres: {MAX_VALUE - MIN_VALUE + 1} layer ({first_year}-{last_year}) dibuat "
    f"di grup '{GROUP_NAME}'.\n"
    "Buka View -> Panels -> Temporal Controller, klik ikon play, "
    f"set range {first_year}-01-01 s.d. {last_year + 1}-01-01 dengan step 1 years."
)
