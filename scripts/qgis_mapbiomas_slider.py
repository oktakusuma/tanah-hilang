# Bikin MapBiomas C4.1 ikut slider tahun (Temporal Controller) di QGIS — padanan
# layer "MapBiomas — Tutupan Lahan" peta web yang berganti tahun mengikuti slider
# "Potret Data" (2001–2024). Pendamping scripts/qgis_loss_slider.py (Hansen).
#
# MapBiomas = SATU BERKAS PER TAHUN. Skrip ini memuat 24 berkas itu sebagai 24
# layer, menyalin gaya (palet kelas + NoData 0) dari satu layer yang sudah kamu
# warnai, lalu memberi tiap layer rentang waktunya sendiri: layer 2013 hanya
# tampil saat slider di 2013.
#
# Prasyarat:
#   1. Satu layer MapBiomas sudah diwarnai — Panduan QGIS langkah 11 (palet
#      "Load Color Map from File…" + Transparency 0). Isi namanya di STYLE_DARI.
#   2. Rasternya: SUMBER = "lokal" (unduh dulu:
#        python3 scripts/mapbiomas/fetch_mapbiomas_lulc.py --tahun 2001-2024
#      ± 210 MB/tahun, ± 5 GB untuk 24 tahun) ATAU SUMBER = "cloud" (tanpa unduh;
#      QGIS membaca sepotong-sepotong lewat internet — butuh koneksi tiap
#      menggeser peta, animasi lebih lambat).
#
# Cara pakai:
#   Plugins -> Python Console -> ikon "Show Editor" -> Open Script… -> file ini
#   -> ubah STYLE_DARI (dan opsi lain bila perlu) -> Run Script.
#   Lalu View -> Panels -> Temporal Controller -> ikon play -> rentang
#   2001-01-01 s.d. 2025-01-01, step 1 years (sama dengan langkah 8).
#
# Opsi POTONG_KE = "konsesi_valid" memotong tiap tahun ke batas konsesi (padanan
# "Hanya Area Konsesi"). Poligon dibetulkan (Fix geometries) lalu DILEBUR jadi
# satu (Dissolve) dulu: konsesi yang tumpang-tindih membuat GDAL lama gagal
# memotong tanpa pesan jelas (diuji 4 Okt 2026 di GDAL 3.8: tanpa dilebur hasilnya
# tak terpotong sama sekali; dengan dilebur bersih). ± 1,5 menit per tahun, QGIS
# tampak "diam" selama memotong. Berkas yang sudah ada dilewati, jadi aman
# dijalankan ulang bila terputus.

from pathlib import Path

from qgis.core import (
    Qgis,
    QgsDateTimeRange,
    QgsMapLayerStyle,
    QgsProject,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QTime

STYLE_DARI = "mapbiomas_c41_2024"   # <-- GANTI: nama layer MapBiomas yang SUDAH
                                    # diwarnai, persis seperti di panel Layers
SUMBER = "lokal"                    # "lokal" = berkas unduhan; "cloud" = baca
                                    # langsung dari server MapBiomas
FOLDER = ""                         # folder mapbiomas_c41_{tahun}.tif; kosong =
                                    # dicari otomatis: folder layer STYLE_DARI, lalu
                                    # <folder project>/data/external/mapbiomas
TAHUN_AWAL = 2001                   # jendela tesis 2001–2024, sama dgn peta web
TAHUN_AKHIR = 2024                  # MapBiomas C4.1 berhenti di 2024
POTONG_KE = ""                      # "" = tidak dipotong; "konsesi_valid" = nama
                                    # layer poligon untuk memotong (langkah 5)
FOLDER_POTONG = ""                  # kosong = <folder project>/data/raster/mapbiomas_konsesi
GROUP_NAME = "MapBiomas per tahun (slider)"

# uuid tiap tahun — disalin dari scripts/mapbiomas/manifest_c41_2000_2024.csv
# (sumber tunggalnya manifest itu; dipakai hanya bila SUMBER = "cloud").
UUID = {
    2000: "6a98541f-68e8-4a2e-b189-7788c64f9c69",
    2001: "096f01b6-079e-4025-af15-bc4c8a99dd65",
    2002: "bf749459-524b-484c-aae9-113985b88577",
    2003: "cf708c37-581b-486f-9e3f-a7e4b0b03bc1",
    2004: "aad07743-9f1b-4a90-8cba-0da902148518",
    2005: "f8c67636-8a24-4689-83cc-c205439c61ba",
    2006: "46fc04cc-4759-4774-a7cc-5ad8945baef8",
    2007: "70264a77-56e2-4496-8873-85e10b1baa72",
    2008: "6e028f24-6798-4fd3-9709-96a1c44acb5a",
    2009: "d86beab5-9bf9-46cd-b9cf-4570c4489426",
    2010: "a842d9ca-2f1d-4471-a0ec-9ab108246fcc",
    2011: "8572f1c4-bdb0-4e86-96d3-1b52ded4e1c2",
    2012: "8b341f42-7b6e-440a-a88c-85b5a99345ed",
    2013: "c9651361-dbd7-406f-b233-a231bea7fc77",
    2014: "2db80c97-09a0-46bf-a7a2-6f168d4b927e",
    2015: "fb65f8a2-5c16-466a-98fb-28d3859e4422",
    2016: "0e227d0b-c4e4-4043-acfb-171c5b8bf7c8",
    2017: "82ee4dff-df17-4a8d-ac99-e52a835691d6",
    2018: "f1a8b9bf-04ce-4358-ac75-c4fce3918633",
    2019: "3148b11a-8867-4655-bf18-fa854d77062a",
    2020: "3f9dadba-a72b-4b73-ac03-c0178a973d52",
    2021: "c61f0242-aa99-4f0a-909c-1aef24813b77",
    2022: "984ca3ad-acde-4d40-a9d9-90ad6a575bdb",
    2023: "bc2d18f7-a1da-498a-b752-ec1a18fbbdb8",
    2024: "47f29e9f-b69c-4b37-972b-e0e689e88d24",
}
URL_CLOUD = (
    "/vsicurl/https://storage.googleapis.com/mapbiomas-downloads/public/"
    "indonesia/maps/{uuid}/{tahun}_coverage_lclu_4-1-1_{uuid}.tif"
)


def kabari(teks):
    print(teks)
    QCoreApplication.processEvents()  # supaya pesan muncul selagi skrip jalan


project = QgsProject.instance()
root = project.layerTreeRoot()

cocok = project.mapLayersByName(STYLE_DARI)
if not cocok:
    raise RuntimeError(
        f"Layer '{STYLE_DARI}' tidak ketemu — cek nama di panel Layers, lalu "
        "sesuaikan STYLE_DARI di atas. Layer itu harus sudah diwarnai (langkah 11)."
    )
src = cocok[0]
gaya = QgsMapLayerStyle()
gaya.readFromLayer(src)  # palet kelas + NoData 0 + opasitas, persis seperti src

old = root.findGroup(GROUP_NAME)
if old and old.findLayer(src.id()):
    raise RuntimeError(
        f"'{STYLE_DARI}' berada di dalam grup '{GROUP_NAME}' buatan skrip ini. "
        "Pakai layer MapBiomas asli yang kamu warnai, di luar grup itu."
    )

# --- 1. Tentukan berkas per tahun -------------------------------------------
tahun_list = list(range(TAHUN_AWAL, TAHUN_AKHIR + 1))
if SUMBER == "cloud":
    berkas = {t: URL_CLOUD.format(uuid=UUID[t], tahun=t) for t in tahun_list}
elif SUMBER == "lokal":
    if FOLDER:
        calon = [Path(FOLDER).expanduser()]
    else:
        # STYLE_DARI bisa saja hasil potong (mis. mapbiomas_cropped) yang disimpan
        # di folder lain — jadi folder unduhan bawaan ikut dicari.
        calon = []
        sumber_src = src.source().split("|")[0]
        if not sumber_src.startswith("/vsi"):
            calon.append(Path(sumber_src).parent)
        if project.homePath():
            calon.append(Path(project.homePath()) / "data" / "external" / "mapbiomas")
    ada = [f for f in calon if any(f.glob("mapbiomas_c41_*.tif"))]
    if not ada:
        raise RuntimeError(
            "Berkas mapbiomas_c41_<tahun>.tif tidak ditemukan di: "
            + (", ".join(str(f) for f in calon) or "(tak ada folder untuk dicari)")
            + ".\n  Pilih salah satu: (a) SUMBER = \"cloud\" — tanpa unduh; "
            "(b) unduh dulu: python3 scripts/mapbiomas/fetch_mapbiomas_lulc.py --tahun "
            f"{TAHUN_AWAL}-{TAHUN_AKHIR}; (c) isi FOLDER dengan folder unduhannya."
        )
    folder = ada[0]
    berkas = {t: str(folder / f"mapbiomas_c41_{t}.tif") for t in tahun_list}
    hilang = [t for t in tahun_list if not Path(berkas[t]).exists()]
    if hilang:
        kabari(
            f"PERINGATAN: {len(hilang)} tahun belum diunduh di {folder}: "
            f"{', '.join(map(str, hilang))}.\n"
            f"  Unduh: python3 scripts/mapbiomas/fetch_mapbiomas_lulc.py --tahun "
            f"{TAHUN_AWAL}-{TAHUN_AKHIR}\n"
            "  atau ubah SUMBER = \"cloud\". Tahun-tahun itu dilewati dulu."
        )
        tahun_list = [t for t in tahun_list if t not in hilang]
else:
    raise RuntimeError('SUMBER harus "lokal" atau "cloud".')

if not tahun_list:
    raise RuntimeError("Tak ada satu tahun pun yang bisa dimuat.")

# --- 2. (Opsional) potong ke batas konsesi ----------------------------------
if POTONG_KE:
    import processing
    from osgeo import gdal

    gdal.UseExceptions()
    mask = project.mapLayersByName(POTONG_KE)
    if not mask:
        raise RuntimeError(
            f"Layer poligon '{POTONG_KE}' tidak ketemu. Buat dulu lewat Fix "
            "geometries (Panduan QGIS langkah 5, aksi 1), atau kosongkan POTONG_KE."
        )
    if FOLDER_POTONG:
        keluar = Path(FOLDER_POTONG).expanduser()
    else:
        rumah = project.homePath()
        if not rumah:
            raise RuntimeError("Simpan project dulu (langkah 0), atau isi FOLDER_POTONG.")
        keluar = Path(rumah) / "data" / "raster" / "mapbiomas_konsesi"
    keluar.mkdir(parents=True, exist_ok=True)

    satu = keluar / "_konsesi_satu.gpkg"
    if not satu.exists():
        kabari("Membetulkan & melebur poligon konsesi jadi satu…")
        valid = processing.run(
            "native:fixgeometries",
            {"INPUT": mask[0], "OUTPUT": str(keluar / "_konsesi_valid.gpkg")},
        )["OUTPUT"]
        processing.run("native:dissolve", {"INPUT": valid, "FIELD": [], "OUTPUT": str(satu)})

    for i, t in enumerate(tahun_list, 1):
        hasil = keluar / f"mapbiomas_{t}_konsesi.tif"
        if not hasil.exists():
            kabari(f"Memotong {t} ({i}/{len(tahun_list)}) — ± 1,5 menit…")
            sementara = keluar / f"mapbiomas_{t}_konsesi.sedang.tif"
            gdal.Warp(
                str(sementara),
                berkas[t],
                cutlineDSName=str(satu),
                cropToCutline=True,
                dstNodata=0,
                creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=2", "ZLEVEL=9", "TILED=YES"],
            )
            sementara.replace(hasil)  # baru diberi nama akhir setelah utuh
        berkas[t] = str(hasil)

# --- 3. Satu layer per tahun, masing-masing dengan rentang waktunya ----------
if old:
    old.parent().removeChildNode(old)  # dijalankan ulang: grup lama dibuang dulu
node = root.findLayer(src.id())
induk = node.parent()
group = induk.insertGroup(induk.children().index(node), GROUP_NAME)  # di posisi src

# 2024 di paling atas: saat Temporal Controller mati semua layer tergambar
# bertumpuk, jadi yang terlihat = tahun teratas = kondisi termutakhir.
dimuat = []
for t in sorted(tahun_list, reverse=True):
    layer = QgsRasterLayer(berkas[t], f"MapBiomas {t}", "gdal")
    if not layer.isValid():
        kabari(f"PERINGATAN: gagal membuka {berkas[t]} — dilewati.")
        continue
    gaya.writeToLayer(layer)

    tp = layer.temporalProperties()
    tp.setIsActive(True)
    try:
        tp.setMode(Qgis.RasterTemporalMode.FixedTemporalRange)
    except AttributeError:  # QGIS < 3.30 pakai enum lama
        tp.setMode(tp.ModeFixedTemporalRange)
    tp.setFixedTemporalRange(
        QgsDateTimeRange(
            QDateTime(QDate(t, 1, 1), QTime(0, 0)),
            QDateTime(QDate(t + 1, 1, 1), QTime(0, 0)),
            includeBeginning=True,
            includeEnd=False,  # 1 Jan tahun berikutnya milik layer berikutnya
        )
    )
    project.addMapLayer(layer, False)  # False = jangan taruh di root
    group.addLayer(layer)
    dimuat.append(t)

node.setItemVisibilityChecked(False)  # layer asal tak ikut slider -> dimatikan

kabari(
    f"Beres: {len(dimuat)} layer MapBiomas ({min(dimuat)}–{max(dimuat)}) di grup "
    f"'{GROUP_NAME}'. Layer '{STYLE_DARI}' dimatikan.\n"
    "Buka View -> Panels -> Temporal Controller, klik ikon play, set rentang "
    f"{TAHUN_AWAL}-01-01 s.d. {TAHUN_AKHIR + 1}-01-01 dengan step 1 years."
)
