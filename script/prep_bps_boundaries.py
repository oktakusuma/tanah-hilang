"""
Bangun batas kabupaten Kalimantan dari geoBoundaries (sumber BPS, 2020).

ADM2 geoBoundaries (519 fitur, hanya punya shapeName) tidak menyimpan provinsi,
jadi setiap kabupaten di-spatial-join ke ADM1 (provinsi) pakai representative point;
fallback ke irisan area terbesar bila titik jatuh tepat di garis batas.

Hasil:
  /tmp/kalimantan-kab-bps-fullres.geojson   -> dipakai zonal stats (full res)
  webapp/public/kalimantan-kabupaten.geojson -> asset peta (disimplifikasi via ogr2ogr)

Properties tiap fitur: provinsi, kabupaten, kab_normalized, tipe.
"""
import json
import sys
from pathlib import Path

from shapely.geometry import shape
from shapely.prepared import prep
from shapely.strtree import STRtree

ADM1 = Path("/tmp/gb_idn_adm1.geojson")
ADM2 = Path("/tmp/gb_idn_adm2.geojson")
OUT_FULL = Path("/tmp/kalimantan-kab-bps-fullres.geojson")

PROV_EN_ID = {
    "West Kalimantan": "KALIMANTAN BARAT",
    "Central Kalimantan": "KALIMANTAN TENGAH",
    "South Kalimantan": "KALIMANTAN SELATAN",
    "East Kalimantan": "KALIMANTAN TIMUR",
    "North Kalimantan": "KALIMANTAN UTARA",
}


def main():
    adm1 = json.loads(ADM1.read_text())
    prov_geoms, prov_names = [], []
    for f in adm1["features"]:
        en = f["properties"].get("shapeName")
        if en in PROV_EN_ID:
            prov_geoms.append(shape(f["geometry"]))
            prov_names.append(PROV_EN_ID[en])
    print(f"Provinsi Kalimantan: {len(prov_geoms)} -> {prov_names}", file=sys.stderr)

    prepared = [prep(g) for g in prov_geoms]
    tree = STRtree(prov_geoms)

    adm2 = json.loads(ADM2.read_text())
    out_feats = []
    for f in adm2["features"]:
        geom = shape(f["geometry"])
        own_area = geom.area
        # Assign to the Kalimantan province with the largest intersection, but only
        # KEEP the kabupaten if a majority (>=50%) of its own area falls in Kalimantan.
        # This excludes other-island kabupaten (e.g. Pangkep, Sulawesi) whose
        # representative point may land inside a loose ADM1 polygon over water.
        best, best_area = None, 0.0
        for i in tree.query(geom):
            try:
                a = geom.intersection(prov_geoms[i]).area
            except Exception:
                a = 0.0
            if a > best_area:
                best, best_area = i, a
        if best is None or own_area <= 0 or best_area / own_area < 0.5:
            continue
        prov_idx = best

        name = f["properties"]["shapeName"].strip()
        tipe = "KOTA" if name.lower().startswith("kota") else "KABUPATEN"
        out_feats.append({
            "type": "Feature",
            "properties": {
                "provinsi": prov_names[prov_idx],
                "kabupaten": name,
                "kab_normalized": name.upper(),
                "tipe": tipe,
            },
            "geometry": f["geometry"],
        })

    out_feats.sort(key=lambda x: (x["properties"]["provinsi"], x["properties"]["kabupaten"]))
    print(f"Kabupaten Kalimantan (BPS): {len(out_feats)}", file=sys.stderr)
    from collections import Counter
    for p, c in sorted(Counter(x["properties"]["provinsi"] for x in out_feats).items()):
        print(f"  {p}: {c}", file=sys.stderr)

    OUT_FULL.write_text(json.dumps({"type": "FeatureCollection", "features": out_feats}))
    print(f"Wrote {OUT_FULL}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
