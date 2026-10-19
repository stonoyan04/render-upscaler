"""Desktop window (Qt / PySide6). Same code runs on Windows, macOS and Linux."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPainter, QPainterPath,
    QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QProgressDialog, QPushButton, QSizePolicy, QSlider, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from . import engine as eng
from .pipeline import OUTPUT_FORMATS, SCALES, SUPPORTED_INPUT, Settings, collect_images, process_image

ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_OUTPUT = Qt.ItemDataRole.UserRole + 1
ROLE_INFO = Qt.ItemDataRole.UserRole + 2

ACCENT = "#E3A857"


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def pil_to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888)
    return q.copy()  # detach from the Python buffer


def load_qimage(path: Path) -> QImage:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return pil_to_qimage(im)


def thumbnail(path: Path, size: int = 96) -> tuple[QIcon, tuple[int, int]]:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        dims = im.size
        im.thumbnail((size, size))
        return QIcon(QPixmap.fromImage(pil_to_qimage(im))), dims


# --------------------------------------------------------------------------- #
# Before / after viewer
# --------------------------------------------------------------------------- #
class CompareView(QWidget):
    """Shows the original and the result on top of each other with a slider.

    Drag with the left mouse button to move the divider, scroll to zoom,
    drag with the right or middle button to pan, double-click to fit.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.before: Optional[QPixmap] = None
        self.after: Optional[QPixmap] = None
        self.split = 0.5
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self._pan_from: Optional[QPointF] = None
        self._dragging = False
        self.setMouseTracking(True)
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_images(self, before: Optional[QImage], after: Optional[QImage]):
        self.before = QPixmap.fromImage(before) if before is not None else None
        self.after = QPixmap.fromImage(after) if after is not None else None
        self.reset_view()

    def reset_view(self):
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self.update()

    # geometry ---------------------------------------------------------------
    def _canvas_size(self):
        ref = self.after or self.before
        return (ref.width(), ref.height()) if ref else (1, 1)

    def _target_rect(self) -> QRectF:
        cw, ch = self._canvas_size()
        pad = 16
        fit = min((self.width() - 2 * pad) / cw, (self.height() - 2 * pad) / ch)
        s = fit * self.zoom
        w, h = cw * s, ch * s
        x = (self.width() - w) / 2 + self.offset.x()
        y = (self.height() - h) / 2 + self.offset.y()
        return QRectF(x, y, w, h)

    # painting ---------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#141416"))

        if not self.before:
            p.setPen(QColor("#7d7d86"))
            f = p.font()
            f.setPointSize(f.pointSize() + 2)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Drop renders here\n\nor use “+ Images” on the left")
            return

        r = self._target_rect()
        p.drawPixmap(r, self.before, QRectF(self.before.rect()))
        if self.after:
            x = r.left() + r.width() * self.split
            p.save()
            p.setClipRect(QRectF(x, r.top(), r.right() - x, r.height()))
            p.drawPixmap(r, self.after, QRectF(self.after.rect()))
            p.restore()

            p.setPen(QPen(QColor(ACCENT), 2))
            top, bottom = max(r.top(), 0), min(r.bottom(), self.height())
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
            cy = (top + bottom) / 2
            p.setBrush(QColor(ACCENT))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, cy), 11, 11)
            p.setPen(QPen(QColor("#1b1b1f"), 2))
            p.drawLine(QPointF(x - 4, cy - 4), QPointF(x - 7, cy))
            p.drawLine(QPointF(x - 7, cy), QPointF(x - 4, cy + 4))
            p.drawLine(QPointF(x + 4, cy - 4), QPointF(x + 7, cy))
            p.drawLine(QPointF(x + 7, cy), QPointF(x + 4, cy + 4))
            self._tag(p, "BEFORE", QPointF(14, 14), left=True)
            self._tag(p, "AFTER", QPointF(self.width() - 14, 14), left=False)
        else:
            self._tag(p, "ORIGINAL", QPointF(14, 14), left=True)

        zoom_txt = f"{int(round(self._pixel_zoom() * 100))}%"
        self._tag(p, zoom_txt, QPointF(self.width() - 14, self.height() - 38), left=False)

    def _pixel_zoom(self) -> float:
        cw, _ = self._canvas_size()
        return self._target_rect().width() / cw

    def _tag(self, p: QPainter, text: str, pos: QPointF, left: bool):
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 16, fm.height() + 8
        x = pos.x() if left else pos.x() - w
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, pos.y(), w, h), 6, 6)
        p.fillPath(path, QColor(0, 0, 0, 150))
        p.setPen(QColor("#f2f2f2"))
        p.drawText(QRectF(x, pos.y(), w, h), Qt.AlignmentFlag.AlignCenter, text)

    # interaction -----------------------------------------------------------
    def _set_split_from(self, x: float):
        r = self._target_rect()
        if r.width() > 0:
            self.split = min(1.0, max(0.0, (x - r.left()) / r.width()))
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.after:
            self._dragging = True
            self._set_split_from(e.position().x())
        elif e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or not self.after:
            self._pan_from = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._set_split_from(e.position().x())
        elif self._pan_from is not None:
            self.offset += e.position() - self._pan_from
            self._pan_from = e.position()
            self.update()
        elif self.after:
            self.setCursor(Qt.CursorShape.SplitHCursor)

    def mouseReleaseEvent(self, _):
        self._dragging = False
        self._pan_from = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, _):
        self.reset_view()

    def wheelEvent(self, e):
        if not self.before:
            return
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = min(64.0, max(1.0, self.zoom * factor))
        factor = new_zoom / self.zoom
        # keep the point under the cursor still
        center = QPointF(self.width() / 2, self.height() / 2) + self.offset
        cursor = e.position()
        self.offset += (cursor - center) * (1 - factor)
        self.zoom = new_zoom
        if self.zoom == 1.0:
            self.offset = QPointF(0, 0)
        self.update()


# --------------------------------------------------------------------------- #
# Background workers
# --------------------------------------------------------------------------- #
class UpscaleWorker(QObject):
    started_item = Signal(int)
    progress = Signal(int, float, str)
    item_done = Signal(int, str, str)
    item_failed = Signal(int, str)
    finished = Signal(bool)

    def __init__(self, jobs: list[tuple[int, Path]], settings: Settings, engine_dir: Path):
        super().__init__()
        self.jobs, self.settings, self.engine_dir = jobs, settings, engine_dir
        self.cancel = threading.Event()

    def run(self):
        for row, path in self.jobs:
            if self.cancel.is_set():
                break
            self.started_item.emit(row)
            t0 = time.time()
            try:
                r = process_image(
                    path, self.settings, self.engine_dir,
                    on_progress=lambda f, m, row=row: self.progress.emit(row, f, m),
                    cancel=self.cancel,
                )
                note = f"{r.out_size[0]}×{r.out_size[1]} · {time.time() - t0:.0f}s"
                if r.notes:
                    note += " · " + r.notes[0]
                self.item_done.emit(row, str(r.output), note)
            except eng.Cancelled:
                break
            except Exception as e:
                self.item_failed.emit(row, str(e))
        self.finished.emit(self.cancel.is_set())


class DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(str, str)  # engine dir, error

    def __init__(self):
        super().__init__()
        self.cancel = threading.Event()

    def run(self):
        try:
            d = eng.download_engine(progress=lambda a, b: self.progress.emit(a, b), cancel=self.cancel)
            self.finished.emit(str(d), "")
        except eng.Cancelled:
            self.finished.emit("", "cancelled")
        except Exception as e:
            self.finished.emit("", str(e))


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class LabeledSlider(QWidget):
    def __init__(self, value: int, hint: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(value)
        self.slider.setToolTip(hint)
        self.label = QLabel()
        self.label.setFixedWidth(34)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider.valueChanged.connect(self._sync)
        lay.addWidget(self.slider)
        lay.addWidget(self.label)
        self._sync(value)

    def _sync(self, v):
        self.label.setText("Off" if v == 0 else str(v))

    def value(self) -> int:
        return self.slider.value()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 820)
        self.setAcceptDrops(True)
        self.store = QSettings("RenderUpscaler", "RenderUpscaler")
        self.engine_dir: Optional[Path] = None
        self.output_dir: Optional[str] = self.store.value("output_dir") or None
        self.thread: Optional[QThread] = None
        self.worker: Optional[UpscaleWorker] = None
        self._queue: list[int] = []
        self._done_count = 0

        self._build_ui()
        self._load_settings()
        QTimer.singleShot(150, self.ensure_engine)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(1)
        self.setCentralWidget(root)

        side = QWidget()
        side.setObjectName("side")
        side.setMinimumWidth(390)
        side.setMaximumWidth(480)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 16, 16, 16)
        sl.setSpacing(12)

        title = QLabel(APP_NAME)
        title.setObjectName("title")
        sub = QLabel("AI upscaling for architectural renders")
        sub.setObjectName("subtitle")
        sl.addWidget(title)
        sl.addWidget(sub)

        # files
        files_box = QGroupBox("Images")
        fl = QVBoxLayout(files_box)
        self.list = QListWidget()
        self.list.setIconSize(QSize(56, 56))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(170)
        self.list.currentRowChanged.connect(self.show_item)
        fl.addWidget(self.list)
        row = QHBoxLayout()
        self.btn_add = QPushButton("+ Images")
        self.btn_add_dir = QPushButton("+ Folder")
        self.btn_remove = QToolButton()
        self.btn_remove.setText("Remove")
        self.btn_clear = QToolButton()
        self.btn_clear.setText("Clear")
        self.btn_add.clicked.connect(self.add_files_dialog)
        self.btn_add_dir.clicked.connect(self.add_folder_dialog)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_list)
        for b in (self.btn_add, self.btn_add_dir, self.btn_remove, self.btn_clear):
            row.addWidget(b)
        fl.addLayout(row)
        sl.addWidget(files_box, 1)

        # settings
        set_box = QGroupBox("Settings")
        form = QFormLayout(set_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setVerticalSpacing(10)

        self.model = QComboBox()
        for m in eng.MODELS.values():
            self.model.addItem(m.label, m.key)
            self.model.setItemData(self.model.count() - 1, m.hint, Qt.ItemDataRole.ToolTipRole)
        form.addRow("AI model", self.model)

        scale_row = QWidget()
        srl = QHBoxLayout(scale_row)
        srl.setContentsMargins(0, 0, 0, 0)
        srl.setSpacing(4)
        self.scale_group = QButtonGroup(self)
        for s in SCALES:
            b = QPushButton(f"{s}×")
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setToolTip("8× runs the AI twice and takes longer" if s == 8 else f"Make the image {s} times larger")
            self.scale_group.addButton(b, s)
            srl.addWidget(b)
        form.addRow("Enlarge", scale_row)

        self.denoise = LabeledSlider(0, "Removes render grain and JPEG noise before upscaling. "
                                        "Use 20–40 for noisy fast renders.")
        form.addRow("Remove noise", self.denoise)
        self.sharpen = LabeledSlider(25, "Light final sharpening for crisp edges.")
        form.addRow("Sharpen", self.sharpen)

        self.max_quality = QCheckBox("Max quality (about 8× slower)")
        self.max_quality.setToolTip("Runs the AI several times with flipped/rotated copies and averages them.")
        form.addRow("", self.max_quality)

        self.fmt = QComboBox()
        for key, label in (("png", "PNG (lossless)"), ("jpg", "JPG (small file)"),
                           ("webp", "WEBP"), ("tiff", "TIFF (for print)")):
            self.fmt.addItem(label, key)
        form.addRow("Save as", self.fmt)

        out_row = QWidget()
        orl = QHBoxLayout(out_row)
        orl.setContentsMargins(0, 0, 0, 0)
        self.out_label = QLabel()
        self.out_label.setObjectName("muted")
        self.out_label.setWordWrap(True)
        self.out_label.setMinimumWidth(110)
        self.out_label.setToolTip("Results are saved in an \"upscaled\" folder next to each image, unless you choose a folder.")
        b_out = QToolButton()
        b_out.setText("Change")
        b_out.clicked.connect(self.choose_output)
        b_out_reset = QToolButton()
        b_out_reset.setText("Reset")
        b_out_reset.clicked.connect(lambda: self.set_output_dir(None))
        orl.addWidget(self.out_label, 1)
        orl.addWidget(b_out)
        orl.addWidget(b_out_reset)
        form.addRow("Save to", out_row)
        self.set_output_dir(self.output_dir)

        # advanced
        self.adv_toggle = QToolButton()
        self.adv_toggle.setText("Advanced ▸")
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setObjectName("link")
        form.addRow("", self.adv_toggle)
        self.adv = QWidget()
        al = QFormLayout(self.adv)
        al.setContentsMargins(0, 0, 0, 0)
        self.tile = QComboBox()
        for label, val in (("Automatic", 0), ("512 px", 512), ("256 px", 256), ("128 px", 128), ("64 px", 64)):
            self.tile.addItem(label, val)
        self.tile.setToolTip("Big images are processed in tiles. Lower this if you get memory errors.")
        al.addRow("Tile size", self.tile)
        self.gpu = QComboBox()
        self.gpu.addItem("Automatic", None)
        for i in range(4):
            self.gpu.addItem(f"GPU {i}", i)
        self.gpu.setToolTip("Only needed on computers with more than one graphics card.")
        al.addRow("Graphics card", self.gpu)
        self.adv.setVisible(False)
        self.adv_toggle.toggled.connect(self._toggle_adv)
        form.addRow(self.adv)
        sl.addWidget(set_box)

        # run
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.status = QLabel("Add renders to begin.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Upscale")
        self.btn_run.setObjectName("primary")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.clicked.connect(self.start)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setMinimumHeight(40)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel)
        run_row.addWidget(self.btn_run, 3)
        run_row.addWidget(self.btn_cancel, 1)
        sl.addLayout(run_row)
        sl.addWidget(self.progress)
        sl.addWidget(self.status)

        # viewer
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.viewer = CompareView()
        rl.addWidget(self.viewer, 1)
        bar = QFrame()
        bar.setObjectName("bar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        self.view_info = QLabel("Drag the line to compare · scroll to zoom · right-drag to move · double-click to fit")
        self.view_info.setObjectName("muted")
        self.btn_open = QPushButton("Open result folder")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.open_output_folder)
        bl.addWidget(self.view_info, 1)
        bl.addWidget(self.btn_open)
        rl.addWidget(bar)

        root.addWidget(side)
        root.addWidget(right)
        root.setStretchFactor(1, 1)
        root.setSizes([400, 920])

        act_open = QAction("Add images", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.add_files_dialog)
        self.addAction(act_open)

    def _toggle_adv(self, on: bool):
        self.adv.setVisible(on)
        self.adv_toggle.setText("Advanced ▾" if on else "Advanced ▸")

    # ------------------------------------------------------------- settings
    def _load_settings(self):
        st = self.store
        idx = self.model.findData(st.value("model", "render"))
        self.model.setCurrentIndex(max(idx, 0))
        scale = int(st.value("scale", 4))
        (self.scale_group.button(scale) or self.scale_group.button(4)).setChecked(True)
        self.denoise.slider.setValue(int(st.value("denoise", 0)))
        self.sharpen.slider.setValue(int(st.value("sharpen", 25)))
        self.max_quality.setChecked(str(st.value("max_quality", "false")).lower() == "true")
        self.fmt.setCurrentIndex(max(self.fmt.findData(st.value("format", "png")), 0))
        self.tile.setCurrentIndex(max(self.tile.findData(int(st.value("tile", 0))), 0))

    def current_settings(self) -> Settings:
        return Settings(
            model=self.model.currentData(),
            scale=self.scale_group.checkedId(),
            denoise=self.denoise.value(),
            sharpen=self.sharpen.value(),
            max_quality=self.max_quality.isChecked(),
            tile=self.tile.currentData(),
            gpu=self.gpu.currentData(),
            out_format=self.fmt.currentData(),
            output_dir=self.output_dir,
        )

    def _save_settings(self):
        s = self.current_settings()
        for k, v in (("model", s.model), ("scale", s.scale), ("denoise", s.denoise), ("sharpen", s.sharpen),
                     ("max_quality", s.max_quality), ("format", s.out_format), ("tile", s.tile)):
            self.store.setValue(k, v)
        self.store.setValue("output_dir", self.output_dir or "")

    def set_output_dir(self, path: Optional[str]):
        self.output_dir = path or None
        self.out_label.setText(path if path else "Next to originals")

    def choose_output(self):
        d = QFileDialog.getExistingDirectory(self, "Save results to", self.output_dir or str(Path.home()))
        if d:
            self.set_output_dir(d)

    # ---------------------------------------------------------------- engine
    def ensure_engine(self):
        self.engine_dir = eng.find_engine()
        if self.engine_dir:
            self.status.setText("Ready. Add renders, choose settings, press Upscale.")
            return
        ans = QMessageBox.question(
            self, "One-time setup",
            "The AI engine is not installed yet.\n\n"
            "It is about 50 MB and is downloaded only once from the official Real-ESRGAN "
            "project on GitHub. Download it now?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            self.status.setText("The AI engine is needed before you can upscale. Restart the app to download it.")
            return
        dlg = QProgressDialog("Downloading AI engine…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("One-time setup")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        th, wk = QThread(self), DownloadWorker()
        wk.moveToThread(th)
        th.started.connect(wk.run)

        def on_prog(done, total):
            if total:
                dlg.setValue(int(done * 100 / total))
                dlg.setLabelText(f"Downloading AI engine… {done / 1e6:.0f} of {total / 1e6:.0f} MB")

        def on_done(path, err):
            th.quit()
            th.wait()
            dlg.close()
            if path:
                self.engine_dir = eng.find_engine() or Path(path)
                self.status.setText("AI engine installed. Ready.")
            elif err != "cancelled":
                QMessageBox.warning(self, "Download failed",
                                    f"Could not download the AI engine:\n{err}\n\n"
                                    "Check your internet connection and restart the app.")

        wk.progress.connect(on_prog)
        wk.finished.connect(on_done)
        dlg.canceled.connect(wk.cancel.set)
        self._dl = (th, wk)
        th.start()

    # ----------------------------------------------------------------- files
    def add_paths(self, paths):
        existing = {Path(self.list.item(i).data(ROLE_PATH)).resolve() for i in range(self.list.count())}
        added = 0
        for f in collect_images(paths):
            if f.resolve() in existing:
                continue
            try:
                icon, dims = thumbnail(f)
            except Exception:
                continue
            info = f"{dims[0]}×{dims[1]}"
            item = QListWidgetItem(icon, f"{f.name}\n{info} · waiting")
            item.setData(ROLE_PATH, str(f))
            item.setData(ROLE_INFO, info)
            item.setToolTip(str(f))
            self.list.addItem(item)
            added += 1
        if added and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        if added:
            self.status.setText(f"{self.list.count()} image(s) ready.")

    def add_files_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_INPUT))
        files, _ = QFileDialog.getOpenFileNames(self, "Add renders", self.store.value("last_dir", str(Path.home())),
                                                f"Images ({exts})")
        if files:
            self.store.setValue("last_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder of renders",
                                             self.store.value("last_dir", str(Path.home())))
        if d:
            self.store.setValue("last_dir", d)
            self.add_paths([d])

    def remove_selected(self):
        if self.worker:
            return
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        if self.list.count() == 0:
            self.viewer.set_images(None, None)

    def clear_list(self):
        if self.worker:
            return
        self.list.clear()
        self.viewer.set_images(None, None)
        self.btn_open.setEnabled(False)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def show_item(self, row: int):
        if row < 0:
            return
        item = self.list.item(row)
        try:
            before = load_qimage(Path(item.data(ROLE_PATH)))
            out = item.data(ROLE_OUTPUT)
            after = load_qimage(Path(out)) if out and Path(out).exists() else None
            self.viewer.set_images(before, after)
            self.btn_open.setEnabled(bool(after))
        except Exception as e:
            self.status.setText(f"Could not show image: {e}")

    # ------------------------------------------------------------------- run
    def start(self):
        if self.worker:
            return
        if not self.engine_dir:
            self.ensure_engine()
            if not self.engine_dir:
                return
        rows = [self.list.row(i) for i in self.list.selectedItems()] if len(self.list.selectedItems()) > 1 else []
        rows = rows or list(range(self.list.count()))
        if not rows:
            self.status.setText("Add some renders first.")
            return
        self._save_settings()
        settings = self.current_settings()
        jobs = [(r, Path(self.list.item(r).data(ROLE_PATH))) for r in sorted(rows)]
        self._queue = [r for r, _ in jobs]
        self._done_count = 0
        self._failed = 0

        self.thread = QThread(self)
        self.worker = UpscaleWorker(jobs, settings, self.engine_dir)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.started_item.connect(self._on_started)
        self.worker.progress.connect(self._on_progress)
        self.worker.item_done.connect(self._on_done)
        self.worker.item_failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)
        self._set_running(True)
        self.thread.start()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText("Stopping…")

    def _set_running(self, on: bool):
        self.btn_run.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        for w in (self.btn_add, self.btn_add_dir, self.btn_remove, self.btn_clear, self.model,
                  self.fmt, self.tile, self.gpu, self.max_quality):
            w.setEnabled(not on)
        for b in self.scale_group.buttons():
            b.setEnabled(not on)
        self.denoise.setEnabled(not on)
        self.sharpen.setEnabled(not on)

    def _item_text(self, row: int, state: str):
        item = self.list.item(row)
        item.setText(f"{Path(item.data(ROLE_PATH)).name}\n{state}")

    def _on_started(self, row: int):
        self._item_text(row, f"{self.list.item(row).data(ROLE_INFO)} · working…")
        self.list.setCurrentRow(row)

    def _on_progress(self, row: int, frac: float, msg: str):
        total = len(self._queue)
        overall = (self._done_count + frac) / total
        self.progress.setValue(int(overall * 1000))
        self.status.setText(f"Image {self._done_count + 1} of {total}: {msg} {int(frac * 100)}%")
        self._item_text(row, f"{self.list.item(row).data(ROLE_INFO)} · {msg.lower()} {int(frac * 100)}%")

    def _on_done(self, row: int, out: str, note: str):
        self._done_count += 1
        item = self.list.item(row)
        item.setData(ROLE_OUTPUT, out)
        self._item_text(row, f"✓ {note}")
        item.setForeground(QColor("#9fd59a"))
        if self.list.currentRow() == row:
            self.show_item(row)

    def _on_failed(self, row: int, err: str):
        self._done_count += 1
        self._failed += 1
        item = self.list.item(row)
        self._item_text(row, "✗ failed (hover for details)")
        item.setToolTip(err)
        item.setForeground(QColor("#ef8a80"))

    def _on_finished(self, cancelled: bool):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.worker = None
        self._set_running(False)
        ok = self._done_count - self._failed
        if cancelled:
            self.status.setText(f"Stopped. {ok} image(s) finished.")
        elif self._failed:
            self.status.setText(f"Finished: {ok} done, {self._failed} failed (hover a failed image for details).")
            self.progress.setValue(1000)
        else:
            self.status.setText(f"Finished: {ok} image(s) upscaled.")
            self.progress.setValue(1000)

    def open_output_folder(self):
        row = self.list.currentRow()
        out = self.list.item(row).data(ROLE_OUTPUT) if row >= 0 else None
        folder = Path(out).parent if out else (Path(self.output_dir) if self.output_dir else None)
        if folder and folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def closeEvent(self, e):
        self._save_settings()
        if self.worker:
            self.worker.cancel.set()
            self.thread.quit()
            self.thread.wait(5000)
        super().closeEvent(e)


STYLE = f"""
* {{ font-size: 13px; }}
QMainWindow, QWidget#side {{ background: #1d1d21; color: #e8e8ec; }}
QWidget {{ color: #e8e8ec; }}
QLabel#title {{ font-size: 20px; font-weight: 700; }}
QLabel#subtitle, QLabel#muted {{ color: #9a9aa5; }}
QGroupBox {{ border: 1px solid #2e2e35; border-radius: 10px; margin-top: 14px; padding: 12px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #b9b9c3; font-weight: 600; }}
QListWidget {{ background: #16161a; border: 1px solid #2e2e35; border-radius: 8px; padding: 4px; }}
QListWidget::item {{ padding: 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #2c2a26; color: #ffffff; }}
QPushButton, QToolButton, QComboBox {{
    background: #2a2a31; border: 1px solid #383841; border-radius: 7px; padding: 6px 10px; }}
QPushButton:hover, QToolButton:hover, QComboBox:hover {{ border-color: #55555f; }}
QPushButton:disabled, QToolButton:disabled, QComboBox:disabled {{ color: #66666f; }}
QPushButton#seg {{ padding: 6px 0; min-width: 44px; }}
QPushButton#seg:checked {{ background: {ACCENT}; color: #1b1b1f; border-color: {ACCENT}; font-weight: 700; }}
QPushButton#primary {{ background: {ACCENT}; color: #1b1b1f; border: none; font-weight: 700; font-size: 14px; }}
QPushButton#primary:hover {{ background: #ecb96c; }}
QPushButton#primary:disabled {{ background: #6b5b44; color: #2a2a2a; }}
QToolButton#link {{ background: transparent; border: none; color: #b9b9c3; padding: 0; }}
QComboBox QAbstractItemView {{ background: #2a2a31; selection-background-color: #3a3730; }}
QSlider::groove:horizontal {{ height: 4px; background: #33333b; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #f1f1f4; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QProgressBar {{ background: #2a2a31; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QFrame#bar {{ background: #1d1d21; border-top: 1px solid #2e2e35; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QSplitter::handle {{ background: #2e2e35; }}
QToolTip {{ background: #2a2a31; color: #e8e8ec; border: 1px solid #44444d; }}
"""


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setOrganizationName("RenderUpscaler")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    icon_path = eng.app_root() / "assets" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    files = [a for a in sys.argv[1:] if Path(a).exists()]
    if files:
        win.add_paths(files)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
