"""The full processing chain for one image.

    load -> (optional) denoise -> AI upscale (1 or 2 passes) -> exact resize
         -> (optional) sharpen -> save

The AI model always works at 4x. For 2x and 3x we run 4x and then shrink with
a high quality filter (this looks better than a native 2x model). For 8x we
run the AI twice.
"""

from __future__ import annotations

import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageFilter, ImageOps

from . import engine as eng

SUPPORTED_INPUT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".jfif"}
OUTPUT_FORMATS = {
    "png": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
    "tiff": ("TIFF", ".tif"),
}
SCALES = (2, 3, 4, 8)
MAX_OUTPUT_MEGAPIXELS = 400  # protects against running out of memory
TILE_FALLBACKS = (0, 256, 128, 64)  # 0 = engine picks automatically

Image.MAX_IMAGE_PIXELS = None  # images can be very large; we enforce our own limit

ProgressFn = Callable[[float, str], None]


@dataclass
class Settings:
    model: str = "render"
    scale: int = 4
    denoise: int = 0       # 0-100, removes render grain before the AI step
    sharpen: int = 25      # 0-100, light final sharpening
    max_quality: bool = False  # slower (about 8x), slightly cleaner
    tile: int = 0          # 0 = automatic
    gpu: Optional[int] = None
    out_format: str = "png"
    jpg_quality: int = 95
    output_dir: Optional[str] = None  # None = "upscaled" folder next to the image
    suffix: str = "_upscaled"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    source: Path
    output: Path
    in_size: tuple[int, int]
    out_size: tuple[int, int]
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def collect_images(paths) -> list[Path]:
    """Expand files and folders into a sorted, de-duplicated list of images."""
    out: list[Path] = []
    seen = set()
    for p in map(Path, paths):
        items = sorted(p.rglob("*")) if p.is_dir() else [p]
        for f in items:
            if f.is_file() and f.suffix.lower() in SUPPORTED_INPUT and f.resolve() not in seen:
                if p.is_dir() and "upscaled" in f.relative_to(p).parts:
                    continue  # don't re-process our own results
                seen.add(f.resolve())
                out.append(f)
    return out


def ai_passes(scale: int) -> int:
    return 2 if scale > 4 else 1


def output_path(src: Path, s: Settings) -> Path:
    folder = Path(s.output_dir) if s.output_dir else src.parent / "upscaled"
    ext = OUTPUT_FORMATS[s.out_format][1]
    base = folder / f"{src.stem}{s.suffix}_x{s.scale}{ext}"
    candidate, n = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}_{n}{ext}")
        n += 1
    return candidate


def load_image(path: Path) -> tuple[Image.Image, dict]:
    im = Image.open(path)
    info = {k: im.info[k] for k in ("icc_profile", "dpi") if k in im.info}
    im = ImageOps.exif_transpose(im)
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v / 256).convert("L")  # 16-bit to 8-bit
    im = im.convert("RGBA" if has_alpha else "RGB")
    return im, info


def denoise(im: Image.Image, strength: int) -> Image.Image:
    """Remove render grain / JPEG noise while keeping edges (non-local means)."""
    if strength <= 0:
        return im
    import cv2
    import numpy as np

    alpha = im.getchannel("A") if im.mode == "RGBA" else None
    arr = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)
    h = 1.0 + strength * 0.11  # 0..100 -> ~1..12
    arr = cv2.fastNlMeansDenoisingColored(arr, None, h, h, 7, 21)
    out = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
    if alpha is not None:
        out.putalpha(alpha)
    return out


def sharpen(im: Image.Image, strength: int) -> Image.Image:
    if strength <= 0:
        return im
    return im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=int(strength * 1.6), threshold=2))


def save_image(im: Image.Image, path: Path, s: Settings, info: dict) -> None:
    fmt = OUTPUT_FORMATS[s.out_format][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    kw: dict = {}
    if "icc_profile" in info:
        kw["icc_profile"] = info["icc_profile"]
    if "dpi" in info:
        try:  # keep the same printed size: more pixels means more dpi
            kw["dpi"] = tuple(round(float(d) * s.scale) for d in info["dpi"])
        except (TypeError, ValueError):
            pass
    if fmt == "JPEG":
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        kw.update(quality=s.jpg_quality, subsampling=0, optimize=True)
    elif fmt == "WEBP":
        kw.update(quality=s.jpg_quality, method=6)
    elif fmt == "PNG":
        kw.update(compress_level=6)
    elif fmt == "TIFF":
        kw.update(compression="tiff_lzw")
    im.save(path, fmt, **kw)


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #
def process_image(
    src: Path,
    s: Settings,
    engine_dir: Path,
    on_progress: Optional[ProgressFn] = None,
    cancel: Optional[threading.Event] = None,
) -> Result:
    src = Path(src)
    report = on_progress or (lambda f, m: None)
    model = eng.MODELS[s.model]
    passes = ai_passes(s.scale)
    notes: list[str] = []

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise eng.Cancelled()

    report(0.0, "Loading")
    im, info = load_image(src)
    w, h = im.size
    target = (w * s.scale, h * s.scale)
    if target[0] * target[1] / 1e6 > MAX_OUTPUT_MEGAPIXELS:
        raise ValueError(
            f"Result would be {target[0]}x{target[1]} pixels, which is too big. "
            f"Choose a smaller scale for this image."
        )
    check_cancel()

    if s.denoise > 0:
        report(0.02, "Removing noise")
        im = denoise(im, s.denoise)
        check_cancel()

    with tempfile.TemporaryDirectory(prefix="render_upscaler_") as tmp:
        tmp = Path(tmp)
        current = tmp / "in_0.png"
        im.save(current, "PNG", compress_level=1)  # plain ASCII path: safest for the engine
        for i in range(passes):
            out = tmp / f"out_{i}.png"
            span_start = 0.05 + 0.85 * i / passes
            span = 0.85 / passes
            label = "AI upscaling" + (f" (pass {i + 1} of {passes})" if passes > 1 else "")
            tiles = (s.tile,) if s.tile else TILE_FALLBACKS
            last_err: Optional[Exception] = None
            for t in tiles:
                check_cancel()
                report(span_start, label)
                try:
                    eng.run_engine(
                        engine_dir, current, out, model,
                        tile=t, gpu=s.gpu, tta=s.max_quality,
                        on_progress=lambda f, a=span_start, b=span, l=label: report(a + b * f, l),
                        cancel=cancel,
                    )
                    if t and not s.tile:
                        notes.append(f"Used smaller tiles ({t}px) to fit graphics memory.")
                    last_err = None
                    break
                except eng.EngineError as e:  # usually out of GPU memory: retry with smaller tiles
                    last_err = e
                    out.unlink(missing_ok=True)
            if last_err is not None:
                raise last_err
            current = out

        report(0.92, "Finishing")
        result = Image.open(current)
        result.load()

    if im.mode == "RGBA" and result.mode != "RGBA":
        result = result.convert("RGBA")
    elif im.mode == "RGB" and result.mode != "RGB":
        result = result.convert("RGB")
    if result.size != target:
        result = result.resize(target, Image.Resampling.LANCZOS)
    check_cancel()

    result = sharpen(result, s.sharpen)
    dest = output_path(src, s)
    report(0.96, "Saving")
    save_image(result, dest, s, info)
    report(1.0, "Done")
    return Result(src, dest, (w, h), target, notes)
