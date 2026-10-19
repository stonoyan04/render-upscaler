"""Tests. The fast ones use a fake engine; the last one uses the real engine if it is installed."""

import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render_upscaler import engine as eng  # noqa: E402
from render_upscaler.pipeline import (  # noqa: E402
    Settings, ai_passes, collect_images, output_path, process_image,
)


def make_image(path: Path, size=(64, 40), mode="RGB"):
    im = Image.new(mode, size, (120, 140, 160) if mode == "RGB" else (120, 140, 160, 200))
    for x in range(0, size[0], 8):
        for y in range(size[1]):
            im.putpixel((x, y), (250, 250, 250) if mode == "RGB" else (250, 250, 250, 255))
    im.save(path)
    return path


@pytest.fixture
def fake_engine(tmp_path):
    """A stand-in for the real engine: a Python script that does a plain 4x resize."""
    d = tmp_path / "engine"
    (d / "models").mkdir(parents=True)
    for m in eng.MODELS.values():
        (d / "models" / f"{m.file}.param").write_text("x")
        (d / "models" / f"{m.file}.bin").write_text("x")
    script = textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        from PIL import Image
        a = sys.argv
        i, o = a[a.index('-i') + 1], a[a.index('-o') + 1]
        for p in (25, 50, 100):
            print(f"{{p:.2f}}%", file=sys.stderr, flush=True)
        im = Image.open(i)
        im.resize((im.width * 4, im.height * 4)).save(o)
    """)
    if sys.platform.startswith("win"):
        pytest.skip("fake engine uses a shebang script")
    exe = d / eng.exe_name()
    exe.write_text(script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return d


def test_ai_passes():
    assert ai_passes(2) == ai_passes(3) == ai_passes(4) == 1
    assert ai_passes(8) == 2


def test_collect_images_skips_results(tmp_path):
    make_image(tmp_path / "a.jpg")
    make_image(tmp_path / "b.png")
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "upscaled").mkdir()
    make_image(tmp_path / "upscaled" / "a_upscaled_x4.png")
    names = [p.name for p in collect_images([tmp_path])]
    assert names == ["a.jpg", "b.png"]


def test_output_path_never_overwrites(tmp_path):
    src = make_image(tmp_path / "r.jpg")
    s = Settings(scale=4, out_format="jpg")
    first = output_path(src, s)
    first.parent.mkdir(parents=True)
    first.write_text("x")
    second = output_path(src, s)
    assert first.name == "r_upscaled_x4.jpg"
    assert second.name == "r_upscaled_x4_2.jpg"


@pytest.mark.parametrize("scale", [2, 3, 4, 8])
def test_scales_give_exact_size(tmp_path, fake_engine, scale):
    src = make_image(tmp_path / "r.png", size=(50, 30))
    seen = []
    r = process_image(src, Settings(scale=scale, sharpen=20), fake_engine,
                      on_progress=lambda f, m: seen.append(f))
    with Image.open(r.output) as out:
        assert out.size == (50 * scale, 30 * scale)
    assert seen[-1] == 1.0 and seen == sorted(seen)


def test_alpha_and_formats(tmp_path, fake_engine):
    src = make_image(tmp_path / "logo.png", mode="RGBA")
    r = process_image(src, Settings(scale=2, out_format="png"), fake_engine)
    assert Image.open(r.output).mode == "RGBA"
    r = process_image(src, Settings(scale=2, out_format="jpg"), fake_engine)
    assert Image.open(r.output).mode == "RGB"


def test_denoise_runs(tmp_path, fake_engine):
    src = make_image(tmp_path / "n.jpg")
    r = process_image(src, Settings(scale=2, denoise=40), fake_engine)
    assert r.output.exists()


def test_dpi_scaled_for_print(tmp_path, fake_engine):
    src = tmp_path / "p.png"
    Image.new("RGB", (20, 20)).save(src, dpi=(72, 72))
    r = process_image(src, Settings(scale=4), fake_engine)
    assert round(Image.open(r.output).info["dpi"][0]) == 288


@pytest.mark.skipif(eng.find_engine() is None, reason="real AI engine not installed")
def test_real_engine(tmp_path):
    src = make_image(tmp_path / "real.png", size=(48, 32))
    r = process_image(src, Settings(scale=4), eng.find_engine())
    assert Image.open(r.output).size == (192, 128)
