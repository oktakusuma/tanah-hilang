"""
Download Hansen GFC v1.13 tiles dengan resume + progress.

Usage:
    python download_hansen.py --tiles 00N_110E 10N_110E
    python download_hansen.py --tiles 00N_110E 10N_110E --layers lossyear treecover2000
    python download_hansen.py --kalimantan-all   # download semua tile Kalimantan
"""

import argparse
import os
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

BASE = "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2025-v1.13"
VERSION = "GFC-2025-v1.13"

LAYERS = {
    "lossyear": "tahun kehilangan per pixel (1=2001, 25=2025, 0=no loss)",
    "treecover2000": "tutupan pohon 2000 (0-100% canopy density)",
    "datamask": "data validity mask (0=no data, 1=mapped, 2=water)",
    "gain": "tahun pertambahan tutupan pohon 2000-2012",
}

KALIMANTAN_TILES = ["00N_100E", "00N_110E", "10N_100E", "10N_110E"]


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download_with_resume(url: str, dest: Path, label: str = "") -> bool:
    """Download URL ke dest dengan resume support + progress bar."""
    # HEAD untuk dapat content-length
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            total = int(r.headers.get("Content-Length", 0))
    except urllib.error.HTTPError as e:
        print(f"  ❌ {label}: HEAD failed ({e.code})", file=sys.stderr)
        return False

    existing = dest.stat().st_size if dest.exists() else 0
    if existing == total:
        print(f"  ✅ {label}: already downloaded ({human_size(total)})",
              file=sys.stderr)
        return True
    if existing > total:
        print(f"  ⚠️  {label}: existing file larger than remote, re-downloading",
              file=sys.stderr)
        dest.unlink()
        existing = 0

    headers = {}
    mode = "wb"
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"
        print(f"  ↻ {label}: resuming from {human_size(existing)} of "
              f"{human_size(total)}", file=sys.stderr)
    else:
        print(f"  ⬇ {label}: downloading {human_size(total)}", file=sys.stderr)

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            with dest.open(mode) as f:
                chunk = 1 << 20  # 1 MB
                done = existing
                t0 = time.time()
                last_print = t0
                while True:
                    buf = r.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    now = time.time()
                    if now - last_print >= 0.5 or done == total:
                        speed = (done - existing) / max(now - t0, 0.001)
                        pct = 100 * done / total
                        eta = (total - done) / max(speed, 1)
                        print(
                            f"\r    {pct:5.1f}%  {human_size(done):>9} / "
                            f"{human_size(total):<9}  "
                            f"{human_size(int(speed))}/s  ETA {eta:5.0f}s",
                            end="", file=sys.stderr,
                        )
                        last_print = now
                print("", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"\n  ❌ {label}: error {e}", file=sys.stderr)
        return False

    final_size = dest.stat().st_size
    if final_size != total:
        print(f"  ⚠️  {label}: size mismatch {final_size} vs expected {total}",
              file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+",
                        help="tile IDs (e.g. 00N_110E 10N_110E)")
    parser.add_argument("--kalimantan-all", action="store_true",
                        help="download all 4 Kalimantan tiles")
    parser.add_argument("--layers", nargs="+",
                        default=["lossyear", "treecover2000"],
                        choices=list(LAYERS),
                        help="which layers to download")
    parser.add_argument("--outdir", type=Path, default=Path("data/raster"))
    args = parser.parse_args()

    if args.kalimantan_all:
        tiles = KALIMANTAN_TILES
    elif args.tiles:
        tiles = args.tiles
    else:
        parser.error("specify --tiles or --kalimantan-all")
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {args.outdir.resolve()}", file=sys.stderr)
    print(f"Tiles  : {tiles}", file=sys.stderr)
    print(f"Layers : {args.layers}\n", file=sys.stderr)

    failures = []
    for tile in tiles:
        for layer in args.layers:
            fname = f"Hansen_{VERSION}_{layer}_{tile}.tif"
            url = f"{BASE}/{fname}"
            dest = args.outdir / fname
            label = f"{layer}/{tile}"
            ok = download_with_resume(url, dest, label)
            if not ok:
                failures.append(label)

    print(f"\nDone. Success: {len(tiles)*len(args.layers) - len(failures)}, "
          f"failures: {len(failures)}", file=sys.stderr)
    if failures:
        for f in failures:
            print(f"  FAILED: {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
