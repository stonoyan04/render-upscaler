"""Command line version, for scripts and batch jobs.

Examples:
    python -m render_upscaler cli render.jpg
    python -m render_upscaler cli ./photos --scale 2 --denoise 30 --format jpg
"""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from . import engine as eng
from .pipeline import OUTPUT_FORMATS, SCALES, Settings, collect_images, process_image


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="render-upscaler", description="AI image upscaler for photos, renders and artwork.")
    p.add_argument("inputs", nargs="*", help="image files and/or folders")
    p.add_argument("-o", "--output", help="output folder (default: 'upscaled' next to each image)")
    p.add_argument("-s", "--scale", type=int, choices=SCALES, default=4)
    p.add_argument("-m", "--model", choices=list(eng.MODELS), default="render")
    p.add_argument("--denoise", type=int, default=0, metavar="0-100")
    p.add_argument("--sharpen", type=int, default=25, metavar="0-100")
    p.add_argument("--max-quality", action="store_true", help="slower, slightly cleaner result")
    p.add_argument("-f", "--format", choices=list(OUTPUT_FORMATS), default="png")
    p.add_argument("--quality", type=int, default=95, help="JPG/WEBP quality")
    p.add_argument("--tile", type=int, default=0, help="tile size, 0 = automatic")
    p.add_argument("--gpu", type=int, default=None, help="GPU number if you have several")
    p.add_argument("--download-engine", action="store_true", help="download the AI engine and exit")
    p.add_argument("--version", action="version", version=__version__)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    engine_dir = eng.find_engine()
    if engine_dir is None or args.download_engine:
        print("Downloading AI engine (about 50 MB, one time only)...")
        last = [-1]

        def prog(done, total):
            pct = int(done * 100 / total) if total else 0
            if pct != last[0]:
                last[0] = pct
                print(f"\r  {pct}%", end="", flush=True)

        engine_dir = eng.download_engine(progress=prog)
        print(f"\n  Installed to {engine_dir}")
        if args.download_engine:
            return 0

    files = collect_images(args.inputs)
    if not files:
        print("No images found. Give image files or folders (jpg, png, webp, tif, bmp).")
        return 1

    s = Settings(
        model=args.model, scale=args.scale, denoise=max(0, min(100, args.denoise)),
        sharpen=max(0, min(100, args.sharpen)), max_quality=args.max_quality,
        tile=args.tile, gpu=args.gpu, out_format=args.format, jpg_quality=args.quality,
        output_dir=args.output,
    )

    failed = 0
    for i, f in enumerate(files, 1):
        start = time.time()
        prefix = f"[{i}/{len(files)}] {f.name}"

        def prog(frac, msg):
            print(f"\r{prefix}: {msg} {int(frac * 100):3d}%   ", end="", flush=True)

        try:
            r = process_image(f, s, engine_dir, on_progress=prog)
            print(f"\r{prefix}: {r.in_size[0]}x{r.in_size[1]} -> {r.out_size[0]}x{r.out_size[1]} "
                  f"in {time.time() - start:.1f}s  ->  {r.output}")
            for n in r.notes:
                print(f"    note: {n}")
        except KeyboardInterrupt:
            print("\nStopped.")
            return 130
        except Exception as e:  # keep going with the rest of the batch
            failed += 1
            print(f"\r{prefix}: FAILED - {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
