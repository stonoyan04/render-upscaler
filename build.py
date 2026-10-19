"""Build a double-clickable app for the computer you run this on.

    python build.py

Result:
    Windows : dist/Render Upscaler/Render Upscaler.exe   (+ a .zip to share)
    macOS   : dist/Render Upscaler.app                    (+ a .zip to share)
    Linux   : dist/Render Upscaler/Render Upscaler        (+ a .tar.gz to share)

The AI engine is downloaded and packed inside the app, so the person who
receives it does not need internet on first start.
PyInstaller cannot cross-compile: build the Windows app on Windows, the Mac
app on a Mac and the Linux app on Linux (or let GitLab CI do it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from render_upscaler import APP_NAME, __version__  # noqa: E402
from render_upscaler import engine as eng  # noqa: E402


def main() -> int:
    key = eng.platform_key()
    engine_dir = ROOT / "engine" / key
    if not (engine_dir / eng.exe_name()).exists():
        print(f"Downloading AI engine for {key} ...")
        eng.download_engine(dest=engine_dir, os_key=key)

    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)

    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--icon", str(ROOT / "assets" / "icon.png"),
        "--add-data", f"{engine_dir}{sep}engine/{key}",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
    ]
    if key == "macos":
        cmd += ["--osx-bundle-identifier", "com.renderupscaler.app"]
    cmd.append(str(ROOT / "app.py"))
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    dist = ROOT / "dist"
    archive_base = dist / f"RenderUpscaler-{__version__}-{key}"
    if key == "macos":
        # ditto keeps the .app bundle (symlinks, permissions) intact
        subprocess.check_call(["ditto", "-c", "-k", "--keepParent", f"{APP_NAME}.app",
                               f"{archive_base}.zip"], cwd=dist)
    elif key == "windows":
        shutil.make_archive(str(archive_base), "zip", dist, APP_NAME)
    else:
        shutil.make_archive(str(archive_base), "gztar", dist, APP_NAME)
    print(f"\nDone. Share the file in: {dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
