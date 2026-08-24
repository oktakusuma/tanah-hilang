#!/usr/bin/env python3
"""
Unduh raster KEBAKARAN (area terbakar tahunan) MapBiomas Indonesia, 2000-2024.
Untuk tutupan lahan, pakai fetch_mapbiomas_lulc.py — sengaja dipisah.

    python script/fetch_mapbiomas_fire.py                 # unduh semua 2000-2024
    python script/fetch_mapbiomas_fire.py --tahun 2009-2015
    python script/fetch_mapbiomas_fire.py --dry-run       # tampilkan rencana + ukuran (offline, tanpa cek server)
    python script/fetch_mapbiomas_fire.py --check         # verifikasi berkas yang sudah ada

-> data/external/mapbiomas_fire/mapbiomas_fire_annual_{tahun}.tif

ISI RASTER (KOREKSI 23 Agu — diverifikasi dari ISI berkas 2015, bukan legenda):
    nilai >0 = area terbakar pada tahun itu; NILAINYA = kode kelas LULC yang
               terbakar (3 hutan, 13 non-hutan alami, 25, 35 sawit, 76 gambut,
               dst. — kode sama dgn coverage_lclu)
    nilai 0  = NoData (tidak terbakar / di luar daratan)
Legenda platform menulis "Burned area, pixelValue=1" — ITU SALAH utk berkas
ekspor ini; jangan menyaring arr == 1 (hasilnya kosong). Bukan bulan, bukan
intensitas, bukan frekuensi. (MapBiomas juga punya subtema Monthly,
Accumulated, dan Frequency; ketiganya TIDAK diunduh skrip ini.)

Grid-nya identik dengan raster tutupan lahan — 170.756 x 63.429 piksel,
ukuran piksel 0,00026949458523585647 derajat (~30 m), sudut kiri-atas sama.
Jadi bisa ditumpuk langsung tanpa reproyeksi atau resampling.

Ukuran total 25 tahun ~286 MB (rata-rata ~11 MB/tahun) — jauh lebih kecil
daripada tutupan lahan karena datanya jarang.

KENAPA INI BERGUNA: Hansen mencatat tutupan pohon hilang tanpa membedakan
sebabnya; kebakaran ikut terhitung. Lapisan ini memungkinkan kehilangan yang
berhimpit dengan area terbakar dipisahkan dari pembukaan lahan — pola kerja
yang sama dengan lapisan sawit Descals, tapi menutup celah yang berbeda.

CATATAN LISENSI: MapBiomas Indonesia rilis CC BY-SA, BUKAN CC BY-4.0 seperti
Hansen/Descals. ShareAlike menular ke karya turunan.

Sitasi: MapBiomas Indonesia - Fire (Annual burned area), accessed on {tanggal}
via: https://plataforma.mapbiomas.org
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://storage.googleapis.com/mapbiomas-downloads/public/indonesia/maps"
MANIFEST_DEFAULT = Path(__file__).with_name("manifest_fire_annual_2000_2024.csv")
OUT_DIR_DEFAULT = Path("data/external/mapbiomas_fire")
CHUNK = 1 << 20  # 1 MiB


def url_tahun(year: str, uuid: str) -> str:
    return f"{BASE}/{uuid}/{year}_fire_annual_4-1-1_{uuid}.tif"


def md5_berkas(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(CHUNK), b""):
            h.update(blok)
    return h.hexdigest()


def manusiawi(n: float) -> str:
    for satuan in ("B", "KB", "MB", "GB"):
        if n < 1024 or satuan == "GB":
            return f"{n:,.1f} {satuan}"
        n /= 1024
    return f"{n:.1f} GB"


def parse_tahun(s: str) -> list[int]:
    hasil: list[int] = []
    for bagian in s.split(","):
        bagian = bagian.strip()
        if not bagian:
            continue
        if "-" in bagian:
            a, b = bagian.split("-")
            hasil.extend(range(int(a), int(b) + 1))
        else:
            hasil.append(int(bagian))
    return sorted(set(hasil))


def verifikasi(path: Path, ukuran: int, md5: str, cek_md5: bool) -> tuple[bool, str]:
    if not path.exists():
        return False, "belum ada"
    nyata = path.stat().st_size
    if nyata != ukuran:
        return False, f"ukuran {nyata:,} != manifest {ukuran:,}"
    if cek_md5:
        h = md5_berkas(path)
        if h != md5:
            return False, f"md5 {h} != manifest {md5}"
    return True, "OK"


def unduh(url: str, path: Path, ukuran: int, maks_gagal: int = 10) -> None:
    """Unduh dengan resume (HTTP Range).

    Error HTTP 4xx = permanen (manifest/uuid salah, objek dihapus) -> langsung
    berhenti, tidak diulang. Gangguan jaringan/5xx diulang, tapi maksimal
    `maks_gagal` kali berturut-turut tanpa kemajuan byte.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    sudah = tmp.stat().st_size if tmp.exists() else 0
    if sudah > ukuran:
        tmp.unlink()
        sudah = 0

    gagal_beruntun = 0
    while sudah < ukuran:
        sebelum = sudah
        req = urllib.request.Request(url)
        if sudah:
            req.add_header("Range", f"bytes={sudah}-")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if sudah and resp.status != 206:
                    tmp.unlink(missing_ok=True)
                    sudah = 0
                    continue
                with open(tmp, "ab" if sudah else "wb") as f:
                    while True:
                        blok = resp.read(CHUNK)
                        if not blok:
                            break
                        f.write(blok)
                        sudah += len(blok)
                        pct = 100.0 * sudah / ukuran
                        print(f"\r    {manusiawi(sudah)} / {manusiawi(ukuran)} ({pct:5.1f}%)",
                              end="", flush=True)
        except urllib.error.HTTPError as e:
            # HTTPError harus ditangkap SEBELUM URLError (subclass-nya).
            if 400 <= e.code < 500:
                raise SystemExit(
                    f"\n    HTTP {e.code} untuk {url} — kesalahan permanen "
                    "(manifest/uuid salah atau objek dihapus), tidak diulang.")
            print(f"\n    server error {e.code}; mengulang dari byte {sudah:,}", flush=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"\n    koneksi putus ({e}); mengulang dari byte {sudah:,}", flush=True)
        sudah = tmp.stat().st_size if tmp.exists() else 0
        if sudah <= sebelum:
            gagal_beruntun += 1
            if gagal_beruntun >= maks_gagal:
                raise SystemExit(
                    f"\n    menyerah setelah {maks_gagal} percobaan tanpa kemajuan; "
                    "jalankan lagi untuk melanjutkan dari .part.")
        else:
            gagal_beruntun = 0
    print()
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DIR_DEFAULT))
    ap.add_argument("--tahun", default="2000-2024")
    ap.add_argument("--dry-run", action="store_true", help="tampilkan rencana, tak mengunduh")
    ap.add_argument("--check", action="store_true", help="verifikasi berkas yang sudah ada, tak mengunduh")
    ap.add_argument("--no-md5", action="store_true", help="lewati verifikasi md5 (ukuran saja)")
    a = ap.parse_args(argv)

    baris = {int(r["year"]): r for r in csv.DictReader(open(a.manifest))}
    diminta = [t for t in parse_tahun(a.tahun)]
    hilang = [t for t in diminta if t not in baris]
    if hilang:
        print(f"Tidak ada di manifest (lapisan kebakaran MapBiomas 2000-2024): {hilang}", file=sys.stderr)
    diminta = [t for t in diminta if t in baris]
    if not diminta:
        print("Tak ada tahun yang bisa diproses.", file=sys.stderr)
        return 1

    out_dir = Path(a.out)
    total = sum(int(baris[t]["bytes"]) for t in diminta)
    print(f"{len(diminta)} tahun ({diminta[0]}-{diminta[-1]}), total {manusiawi(total)}")

    gagal = 0
    perlu = 0
    for t in diminta:
        r = baris[t]
        path = out_dir / f"mapbiomas_fire_annual_{t}.tif"
        ukuran, md5 = int(r["bytes"]), r["md5"]
        ok, pesan = verifikasi(path, ukuran, md5, cek_md5=(a.check and not a.no_md5))

        if a.check:
            print(f"  {t}  {'OK  ' if ok else 'GAGAL'}  {pesan}")
            gagal += 0 if ok else 1
            continue

        if ok:
            print(f"  {t}  sudah ada, ukuran cocok — dilewati (md5 hanya diperiksa dengan --check)")
            continue

        perlu += ukuran
        if a.dry_run:
            print(f"  {t}  akan diunduh  {manusiawi(ukuran)}  ({pesan})")
            continue

        print(f"  {t}  mengunduh {manusiawi(ukuran)}")
        unduh(url_tahun(r["year"], r["uuid"]), path, ukuran)
        ok, pesan = verifikasi(path, ukuran, md5, cek_md5=not a.no_md5)
        if not ok:
            print(f"    VERIFIKASI GAGAL: {pesan}", file=sys.stderr)
            gagal += 1
        else:
            print("    verifikasi OK (ukuran + md5)")

    if a.dry_run:
        print(f"\nPerlu diunduh: {manusiawi(perlu)}")
    if a.check:
        print(f"\n{len(diminta) - gagal}/{len(diminta)} berkas lolos verifikasi.")
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main())
