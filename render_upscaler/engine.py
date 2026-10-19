"""Find, download and run the AI engine (Real-ESRGAN, ncnn + Vulkan build).

The engine is a small native program that runs the neural network on the
graphics card through Vulkan. Vulkan works on NVIDIA, AMD and Intel GPUs on
Windows and Linux, and on Apple GPUs on macOS, so the same app works everywhere.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ENGINE_VERSION = "v0.2.5.0"
ENGINE_BUILD = "20220424"
ENGINE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/"
    "{version}/realesrgan-ncnn-vulkan-{build}-{os}.zip"
)
ENV_ENGINE_DIR = "RENDER_UPSCALER_ENGINE"


@dataclass(frozen=True)
class Model:
    key: str
    file: str
    native_scale: int
    label: str
    hint: str


MODELS = {
    "render": Model(
        "render",
        "realesrgan-x4plus",
        4,
        "Photos & realistic",
        "Best for photos, 3D renders and any realistic image.",
    ),
    "graphics": Model(
        "graphics",
        "realesrgan-x4plus-anime",
        4,
        "Drawings & graphics",
        "Best for illustrations, line art, logos, screenshots and flat-color graphics.",
    ),
}


class EngineError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "ubuntu"  # works on any modern x86-64 Linux, not only Ubuntu


def exe_name() -> str:
    return "realesrgan-ncnn-vulkan.exe" if platform_key() == "windows" else "realesrgan-ncnn-vulkan"


def app_root() -> Path:
    """Folder the app runs from (works for source and PyInstaller builds)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    key = platform_key()
    if key == "windows":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "RenderUpscaler"
    if key == "macos":
        return Path.home() / "Library" / "Application Support" / "RenderUpscaler"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "render-upscaler"


def candidate_dirs() -> list[Path]:
    dirs = []
    if os.environ.get(ENV_ENGINE_DIR):
        dirs.append(Path(os.environ[ENV_ENGINE_DIR]))
    dirs.append(app_root() / "engine" / platform_key())
    dirs.append(user_data_dir() / "engine" / platform_key())
    return dirs


def _is_complete(folder: Path) -> bool:
    return (folder / exe_name()).is_file() and all(
        (folder / "models" / f"{m.file}.param").is_file() and (folder / "models" / f"{m.file}.bin").is_file()
        for m in MODELS.values()
    )


def _ensure_executable(folder: Path) -> Path:
    """Make sure the engine can be executed; copy it somewhere writable if needed."""
    exe = folder / exe_name()
    if platform_key() == "windows" or os.access(exe, os.X_OK):
        return folder
    try:
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return folder
    except OSError:
        target = user_data_dir() / "engine" / platform_key()
        if target.resolve() == folder.resolve():
            raise
        shutil.copytree(folder, target, dirs_exist_ok=True)
        (target / exe_name()).chmod(0o755)
        return target


def find_engine() -> Optional[Path]:
    for folder in candidate_dirs():
        if _is_complete(folder):
            return _ensure_executable(folder)
    return None


def download_engine(
    dest: Optional[Path] = None,
    os_key: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """Download the engine zip (~50 MB) and keep only the program and the models."""
    os_key = os_key or platform_key()
    dest = Path(dest or user_data_dir() / "engine" / os_key)
    url = ENGINE_URL.format(version=ENGINE_VERSION, build=ENGINE_BUILD, os=os_key)

    with tempfile.TemporaryDirectory(prefix="ru_dl_") as tmp:
        zip_path = Path(tmp) / "engine.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "RenderUpscaler"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

        wanted_models = {f"{m.file}.{ext}" for m in MODELS.values() for ext in ("param", "bin")}
        (dest / "models").mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = Path(info.filename).name
                if info.is_dir() or not name:
                    continue
                if name in wanted_models:
                    target = dest / "models" / name
                elif name.lower().startswith("realesrgan-ncnn-vulkan") or name.lower().endswith(".dll"):
                    target = dest / name
                else:
                    continue
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)

    exe = dest / ("realesrgan-ncnn-vulkan.exe" if os_key == "windows" else "realesrgan-ncnn-vulkan")
    if os_key != "windows" and exe.exists():
        exe.chmod(0o755)
    if not exe.exists():
        raise EngineError("Download finished but the engine program was not found in the archive.")
    return dest


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")


def run_engine(
    engine_dir: Path,
    input_png: Path,
    output_png: Path,
    model: Model,
    tile: int = 0,
    gpu: Optional[int] = None,
    tta: bool = False,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> None:
    """Run one AI pass (always the model's native 4x) on a single PNG."""
    cmd = [
        str(engine_dir / exe_name()),
        "-i", str(input_png),
        "-o", str(output_png),
        "-n", model.file,
        "-s", str(model.native_scale),
        "-m", str(engine_dir / "models"),
        "-t", str(tile),
        "-f", "png",
    ]
    if gpu is not None:
        cmd += ["-g", str(gpu)]
    if tta:
        cmd.append("-x")

    kwargs = {}
    if platform_key() == "windows":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no black console popping up

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=str(engine_dir),
        **kwargs,
    )
    log: list[str] = []
    stop_watch = threading.Event()

    def watch_cancel():
        while not stop_watch.wait(0.2):
            if cancel is not None and cancel.is_set():
                proc.kill()
                return

    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()
    try:
        assert proc.stderr is not None
        buf = b""
        while True:
            ch = proc.stderr.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                line = buf.decode("utf-8", "replace").strip()
                buf = b""
                if not line:
                    continue
                m = _PERCENT.fullmatch(line)
                if m and on_progress:
                    on_progress(min(float(m.group(1)) / 100.0, 1.0))
                elif not m:
                    log.append(line)
            else:
                buf += ch
        code = proc.wait()
    finally:
        stop_watch.set()

    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if code != 0 or not output_png.exists() or output_png.stat().st_size == 0:
        detail = "\n".join(log[-8:]) or f"exit code {code}"
        raise EngineError(f"The AI engine failed:\n{detail}")
