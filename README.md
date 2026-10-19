# Render Upscaler

A desktop app that makes low-resolution images (photos, 3D renders, artwork, screenshots) sharp and large
(2×, 3×, 4× or 8×) with AI. Runs on **Windows, macOS (Intel and Apple Silicon) and Linux**,
uses any graphics card (NVIDIA, AMD, Intel, Apple), and works fully offline after the first start.

- Drag and drop images, or a whole folder (batch)
- Before / after viewer with a slider and zoom
- Noise removal for grainy photos and renders, final sharpening
- Saves PNG, JPG, WEBP or TIFF, keeps color profile, adjusts DPI so print size stays the same
- Also a command-line mode for scripts

## How it works

1. **Remove noise (optional).** Grain and JPEG noise are removed first, so the AI
   doesn't enlarge them.
2. **AI upscale.** The Real-ESRGAN neural network was trained on millions of pairs of
   sharp images and the same images made small and dirty. It recognises edges, textures, foliage,
   fabric and hair and redraws them at 4× the size, instead of just stretching pixels.
   For 2× and 3× the app runs 4× and shrinks with a high-quality filter (cleaner than a native
   2× model). For 8× it runs the AI twice.
3. **Tiles.** Big images are cut into tiles that fit in graphics memory and joined back.
   If the card runs out of memory the app retries with smaller tiles automatically.
4. **Sharpen and save.**

The AI invents believable detail, not the true detail: tiny text and small faces in the
background can look a little artificial. It cannot fix an out-of-focus or motion-blurred photo.

## Download and use

Get the installer for your computer from the
[Releases page](https://github.com/stonoyan04/render-upscaler/releases/latest):

| Computer | File | How to install |
|---|---|---|
| **Windows** 10/11 | `RenderUpscaler-…-windows-setup.exe` | Double-click and follow the steps. Adds a Start menu and desktop icon, and an uninstaller. No admin rights needed. |
| **Mac** (M1 or newer) | `RenderUpscaler-…-macos.dmg` | Open it and drag **Render Upscaler** into **Applications**. |
| **Linux** | `RenderUpscaler-…-linux-x86_64.AppImage` | Right-click → Properties → allow running as a program, then double-click. |

There is also `…-windows-portable.zip` for Windows if you prefer not to install (unzip and run).
On an Intel Mac, run from source instead (see below).

**First start warnings.** The app isn't code-signed yet, so the system asks once:
- Windows SmartScreen: click **More info → Run anyway**.
- macOS: if it says the app can't be opened, go to **System Settings → Privacy & Security**,
  scroll down and click **Open Anyway**.

Then: drop images in → pick **Enlarge** → press **Upscale**. Results go to an `upscaled`
folder next to the originals (or a folder you choose).

### Good settings

| Image type | Model | Remove noise | Sharpen |
|---|---|---|---|
| Normal photo or 3D render | Photos & realistic | Off – 15 | 20 – 30 |
| Grainy photo (low light) or fast render | Photos & realistic | 25 – 45 | 15 – 25 |
| Compressed JPG from WhatsApp/Telegram | Photos & realistic | 10 – 25 | 20 – 30 |
| Illustrations, logos, line art, screenshots | Drawings & graphics | Off | 10 – 20 |

*Max quality* is about 8× slower and only slightly cleaner; use it for the final hero image.

Tip: the best result is always a higher-resolution original. The upscaler is for when
you only have the small version, or making a new one would take too long.

## Run from source (developers)

Needs Python 3.9 or newer.

```bash
# macOS / Linux
./run.sh
# Windows
run.bat
```

Or by hand:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip ...
.venv/bin/python app.py
```

On first start the app downloads the AI engine (~50 MB) from the official
[Real-ESRGAN releases](https://github.com/xinntao/Real-ESRGAN/releases) and keeps it in:

- Windows: `%LOCALAPPDATA%\RenderUpscaler`
- macOS: `~/Library/Application Support/RenderUpscaler`
- Linux: `~/.local/share/render-upscaler`

Linux needs a Vulkan driver (`sudo apt install mesa-vulkan-drivers`, or the NVIDIA driver).

### Command line

```bash
python app.py cli photo.jpg                        # 4x, PNG
python app.py cli ./photos --scale 2 --denoise 30 --format jpg -o ./out
python app.py cli --help
```

## Build the app

PyInstaller can't cross-compile, so build each system on that system:

```bash
pip install -r requirements.txt pyinstaller
python build.py
```

The installer (with the AI engine packed inside) is written to `dist/`: a `-setup.exe` on Windows
(needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)), a `.dmg` on macOS, an `.AppImage` on Linux.

**GitLab CI** (`.gitlab-ci.yml`) runs the tests on every push. Push a tag like `v1.0.0`
(or start the jobs by hand) to build the **Linux** and **Windows** apps; download them from
the pipeline's artifacts and attach them to a GitLab Release. macOS runners are paid on
GitLab, so build the Mac app on a Mac with `python build.py`.

## Project layout

```
app.py                      start the app (python app.py)
render_upscaler/
  engine.py                 finds / downloads / runs the AI engine
  pipeline.py               denoise -> AI -> resize -> sharpen -> save
  gui.py                    the desktop window
  cli.py                    command-line version
build.py                    makes the app and its installer
tests/                      pytest tests
```

## Tests

```bash
pip install pytest && python -m pytest -q
```

## Credits and licence

App code: MIT (see `LICENSE`).
AI model and engine: [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) by Xintao Wang et al.
(BSD-3-Clause), running on [ncnn](https://github.com/Tencent/ncnn) (BSD-3-Clause).
Interface: Qt for Python / PySide6 (LGPL-3.0).
