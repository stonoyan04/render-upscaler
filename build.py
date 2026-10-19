"""Build the app and an installer for the computer you run this on.

    python build.py

Result in dist/:
    Windows : RenderUpscaler-<version>-windows-setup.exe   installer (Start menu, desktop icon, uninstaller)
              RenderUpscaler-<version>-windows-portable.zip  no-install version
    macOS   : RenderUpscaler-<version>-macos.dmg            open it, drag the app to Applications
    Linux   : RenderUpscaler-<version>-linux-x86_64.AppImage  double-click to run

The AI engine is packed inside, so the app works offline from the first start.
PyInstaller cannot cross-compile: build the Windows installer on Windows, the Mac
one on a Mac and the Linux one on Linux (or let GitHub Actions build all three).

Extra tools used (installed automatically on GitHub Actions):
    Windows : Inno Setup 6 (iscc)    https://jrsoftware.org/isinfo.php
    macOS   : hdiutil (built in)
    Linux   : appimagetool (downloaded automatically)
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from render_upscaler import APP_NAME, __version__  # noqa: E402
from render_upscaler import engine as eng  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
BUNDLE_ID = "com.renderupscaler.app"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
)


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd], **kw)


def make_ico() -> Path:
    from PIL import Image

    ico = BUILD / "icon.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    Image.open(ROOT / "assets" / "icon.png").save(
        ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    return ico


def pyinstaller(key: str, engine_dir: Path) -> None:
    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--icon", ROOT / "assets" / "icon.png",
        "--add-data", f"{engine_dir}{sep}engine/{key}",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--distpath", DIST,
        "--workpath", BUILD / "pyinstaller",
        "--specpath", BUILD,
    ]
    if key == "macos":
        cmd += ["--osx-bundle-identifier", BUNDLE_ID]
    cmd.append(ROOT / "app.py")
    run(cmd, cwd=ROOT)


# --------------------------------------------------------------------------- #
# Windows: Inno Setup installer
# --------------------------------------------------------------------------- #
def find_iscc() -> str:
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            for sub in ("Inno Setup 6", r"Programs\Inno Setup 6"):
                p = Path(base) / sub / "ISCC.exe"
                if p.exists():
                    return str(p)
    raise SystemExit("Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) and run again.")


def windows_installer(base: str) -> None:
    ico = make_ico()
    app_dir = DIST / APP_NAME
    iss = BUILD / "installer.iss"
    iss.write_text(f"""
[Setup]
AppId={{{{6B1D2E4C-3F7A-4C1B-9E1A-52D0C8A7F311}}
AppName={APP_NAME}
AppVersion={__version__}
AppPublisher=Smbat Tonoyan
AppPublisherURL=https://github.com/stonoyan04/render-upscaler
DefaultDirName={{autopf}}\\{APP_NAME}
DefaultGroupName={APP_NAME}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={DIST}
OutputBaseFilename={base}-windows-setup
SetupIconFile={ico}
UninstallDisplayIcon={{app}}\\{APP_NAME}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"

[Files]
Source: "{app_dir}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{autoprograms}}\\{APP_NAME}"; Filename: "{{app}}\\{APP_NAME}.exe"
Name: "{{autodesktop}}\\{APP_NAME}"; Filename: "{{app}}\\{APP_NAME}.exe"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{APP_NAME}.exe"; Description: "{{cm:LaunchProgram,{APP_NAME}}}"; Flags: nowait postinstall skipifsilent
""", encoding="utf-8")
    run([find_iscc(), "/Q", iss])
    shutil.make_archive(str(DIST / f"{base}-windows-portable"), "zip", DIST, APP_NAME)


# --------------------------------------------------------------------------- #
# macOS: drag-to-Applications disk image
# --------------------------------------------------------------------------- #
def macos_dmg(base: str) -> None:
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run(["ditto", DIST / f"{APP_NAME}.app", stage / f"{APP_NAME}.app"])
    (stage / "Applications").symlink_to("/Applications")
    dmg = DIST / f"{base}-macos.dmg"
    dmg.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", stage, "-ov", "-format", "UDZO", dmg])


# --------------------------------------------------------------------------- #
# Linux: AppImage
# --------------------------------------------------------------------------- #
def linux_appimage(base: str) -> None:
    appdir = BUILD / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    (appdir / "usr" / "lib").mkdir(parents=True)
    shutil.copytree(DIST / APP_NAME, appdir / "usr" / "lib" / "render-upscaler", symlinks=True)

    apprun = appdir / "AppRun"
    apprun.write_text(
        '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
        f'exec "$HERE/usr/lib/render-upscaler/{APP_NAME}" "$@"\n'
    )
    apprun.chmod(0o755)
    shutil.copy(ROOT / "assets" / "icon.png", appdir / "render-upscaler.png")
    (appdir / "render-upscaler.desktop").write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={APP_NAME}\nComment=AI image upscaler for photos, renders and artwork\n"
        "Exec=AppRun %F\nIcon=render-upscaler\nCategories=Graphics;Photography;\n"
        "MimeType=image/png;image/jpeg;image/webp;image/tiff;image/bmp;\nTerminal=false\n"
    )

    tool = BUILD / "appimagetool"
    if not tool.exists():
        print("Downloading appimagetool ...")
        urllib.request.urlretrieve(APPIMAGETOOL_URL, tool)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    out = DIST / f"{base}-linux-x86_64.AppImage"
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")  # works without FUSE
    run([tool, "--no-appstream", appdir, out], env=env)


def main() -> int:
    key = eng.platform_key()
    engine_dir = ROOT / "engine" / key
    if not (engine_dir / eng.exe_name()).exists():
        print(f"Downloading AI engine for {key} ...")
        eng.download_engine(dest=engine_dir, os_key=key)

    for d in (BUILD, DIST):
        shutil.rmtree(d, ignore_errors=True)

    pyinstaller(key, engine_dir)
    base = f"RenderUpscaler-{__version__}"
    {"windows": windows_installer, "macos": macos_dmg, "ubuntu": linux_appimage}[key](base)

    print("\nDone. Files to share:")
    for f in sorted(DIST.iterdir()):
        if f.is_file():
            print(f"  {f.name}  ({f.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
