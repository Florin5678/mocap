"""Extrinsic calibration dialog (§6.5): pick a frame, click the reference points, see the error live."""

from __future__ import annotations

import html
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGraphicsEllipseItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.calibration import extrinsics as ex
from mocapapp.core.calibration.intrinsics import IntrinsicsResult
from mocapapp.core.calibration.scene_points import MIN_CLICKED, Scene
from mocapapp.gui.common import para

CLICK_COLOR = QColor("#ffeb3b")
PROJ_COLOR = QColor("#00e5ff")


def to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class ImageView(QGraphicsView):
    """Left click = place the selected point; wheel = zoom at the cursor; right or middle drag = pan."""

    clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.pixmap = self.scene_.addPixmap(QPixmap())
        self.overlay: list = []
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pan = None

    def set_image(self, image: QImage) -> None:
        first = self.pixmap.pixmap().isNull()
        self.pixmap.setPixmap(QPixmap.fromImage(image))
        self.scene_.setSceneRect(self.pixmap.boundingRect())
        if first:
            self.fitInView(self.pixmap, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.LeftButton:
            p = self.mapToScene(event.position().toPoint())
            if self.pixmap.boundingRect().contains(p):
                self.clicked.emit(p.x(), p.y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pan is not None:
            d = event.position() - self._pan
            self._pan = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pan = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def draw_marks(self, clicks: dict, reprojected: dict | None, selected: str | None) -> None:
        for item in self.overlay:
            self.scene_.removeItem(item)
        self.overlay = []
        size = max(5.0, self.pixmap.boundingRect().width() / 180)
        font = QFont()
        font.setPointSizeF(size * 2.2)
        font.setBold(True)

        def mark(x, y, color, label, filled):
            e = QGraphicsEllipseItem(x - size, y - size, 2 * size, 2 * size)
            e.setPen(QPen(QColor("black"), size / 3))
            e.setBrush(QBrush(color) if filled else QBrush(Qt.BrushStyle.NoBrush))
            if not filled:
                e.setPen(QPen(color, size / 2))
            t = QGraphicsSimpleTextItem(label)
            t.setFont(font)
            t.setBrush(QBrush(color))
            t.setPen(QPen(QColor("black"), size / 8))
            t.setPos(QPointF(x + size * 1.2, y - size * 3))
            for item in (e, t):
                self.scene_.addItem(item)
                self.overlay.append(item)

        for label, (x, y) in (reprojected or {}).items():
            mark(x, y, PROJ_COLOR, label if label not in clicks or clicks[label] == "hidden" else "", False)
        for label, value in clicks.items():
            if isinstance(value, tuple):
                mark(value[0], value[1], QColor("#ff9800") if label == selected else CLICK_COLOR, label, True)


class ExtrinsicDialog(QDialog):
    def __init__(self, parent: QWidget | None, camera: str, video: Path, scene: Scene, intr: IntrinsicsResult,
                 previous: ex.ExtrinsicsResult | None = None):
        super().__init__(parent)
        self.camera, self.video, self.scene, self.intr = camera, Path(video), scene, intr
        self.setWindowTitle(f"Camera position - {camera}")
        self.resize(1300, 820)
        self.labels = [p.label for p in scene.points]
        self.state: dict[str, tuple[float, float] | str] = {}
        self.undo_stack: list[dict] = []
        self.result: ex.ExtrinsicsResult | None = None
        self.frame_index = 0
        if previous is not None:
            self.frame_index = previous.frame_index
            for c in previous.clicks:
                if c["label"] in self.labels:
                    self.state[c["label"]] = (c["u"], c["v"]) if c["u"] is not None else "hidden"

        cap = cv2.VideoCapture(str(self.video))
        n_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        cap.release()

        self.view = ImageView()
        self.view.clicked.connect(self._image_clicked)
        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel("Frame"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, n_frames - 1)
        self.spin = QSpinBox()
        self.spin.setRange(0, n_frames - 1)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.slider.sliderReleased.connect(lambda: self.load_frame(self.slider.value()))
        self.spin.editingFinished.connect(lambda: self.load_frame(self.spin.value()))
        frame_row.addWidget(self.slider, 1)
        frame_row.addWidget(self.spin)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(para("Select a point on the right, then click it in the picture. Mouse wheel = zoom, "
                            "right-drag = move. Frame 0 is what Pose2Sim itself would use."))
        ll.addWidget(self.view, 1)
        ll.addLayout(frame_row)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda _: self._redraw())
        rl.addWidget(QLabel(f"<b>Reference points</b> (at least {MIN_CLICKED}, 10 or more recommended)"))
        rl.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.hidden_button = QPushButton("Not visible")
        self.clear_button = QPushButton("Clear")
        self.undo_button = QPushButton("Undo")
        self.hidden_button.clicked.connect(self._toggle_hidden)
        self.clear_button.clicked.connect(self._clear)
        self.undo_button.clicked.connect(self._undo)
        for b in (self.hidden_button, self.clear_button, self.undo_button):
            buttons.addWidget(b)
        rl.addLayout(buttons)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        self.result_label.setMinimumHeight(140)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        rl.addWidget(self.result_label)
        self.override_box = QCheckBox("Accept anyway (error above 2.5 cm; recorded in the QA report)")
        self.override_box.setVisible(False)
        self.override_box.toggled.connect(lambda _: self._update_ok())
        rl.addWidget(self.override_box)
        rl.addWidget(para("This camera position is used for every trial of the session."))

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([900, 380])
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save camera position")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addWidget(self.buttons)

        self.spin.setValue(self.frame_index)
        self.load_frame(self.frame_index)
        self._fill_list()
        self.list.setCurrentRow(self._next_unset(0))
        self._solve()

    # ---------- state ----------

    @property
    def override(self) -> bool:
        return self.override_box.isChecked()

    def clicks(self) -> list[ex.Click]:
        out = []
        for label in self.labels:
            v = self.state.get(label)
            if isinstance(v, tuple):
                out.append(ex.Click(label, v[0], v[1]))
            elif v == "hidden":
                out.append(ex.Click(label, None, None))
        return out

    def n_visible(self) -> int:
        return sum(isinstance(v, tuple) for v in self.state.values())

    def _push(self) -> None:
        self.undo_stack.append(dict(self.state))

    def load_frame(self, index: int) -> None:
        try:
            frame = ex.read_frame(self.video, index)
        except ex.ExtrinsicsError as exc:
            self.result_label.setText(html.escape(str(exc)))
            return
        self.frame_index = index
        self.view.set_image(to_qimage(frame))
        self._redraw()

    def _fill_list(self) -> None:
        row = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.scene.points:
            v = self.state.get(p.label)
            where = (f"clicked at ({v[0]:.0f}, {v[1]:.0f})" if isinstance(v, tuple) else
                     "not visible" if v == "hidden" else "not set")
            err = ""
            if self.result and p.label in self.result.per_point_px:
                err = f", off by {self.result.per_point_px[p.label]:.1f} px"
            self.list.addItem(f"{p.label}  ({p.x:g}, {p.y:g}, {p.z:g})  -  {where}{err}")
        self.list.setCurrentRow(max(0, row))
        self.list.blockSignals(False)

    def _selected_label(self) -> str | None:
        r = self.list.currentRow()
        return self.labels[r] if 0 <= r < len(self.labels) else None

    def _next_unset(self, start: int) -> int:
        for k in list(range(start, len(self.labels))) + list(range(0, start)):
            if self.labels[k] not in self.state:
                return k
        return min(start, len(self.labels) - 1)

    # ---------- actions ----------

    def _image_clicked(self, x: float, y: float) -> None:
        label = self._selected_label()
        if label is None:
            return
        self._push()
        self.state[label] = (round(x, 1), round(y, 1))
        nxt = self._next_unset(self.list.currentRow() + 1)
        self._solve()
        self.list.setCurrentRow(nxt)

    def _toggle_hidden(self) -> None:
        label = self._selected_label()
        if label is None:
            return
        self._push()
        if self.state.get(label) == "hidden":
            del self.state[label]
        else:
            self.state[label] = "hidden"
            self.list.setCurrentRow(self._next_unset(self.list.currentRow() + 1))
        self._solve()

    def _clear(self) -> None:
        label = self._selected_label()
        if label in self.state:
            self._push()
            del self.state[label]
            self._solve()

    def _undo(self) -> None:
        if self.undo_stack:
            self.state = self.undo_stack.pop()
            self._solve()

    def _solve(self) -> None:
        self.result = None
        n = self.n_visible()
        if n >= MIN_CLICKED:
            try:
                self.result = ex.solve_camera(self.camera, self.scene, self.clicks(), self.intr.matrix,
                                              self.intr.distortions, self.frame_index)
            except ex.ExtrinsicsError as exc:
                self.result_label.setText(html.escape(str(exc)))
        if self.result is None and n < MIN_CLICKED:
            self.result_label.setText(f"{n} of at least {MIN_CLICKED} points clicked. The error is shown from "
                                      f"{MIN_CLICKED} points on.")
        elif self.result is not None:
            r = self.result
            word = {"ok": ("PASS", "#2e7d32"), "warn": ("CHECK (usable)", "#b26a00"),
                    "fail": ("FAIL", "#c62828")}[r.status]
            warn = "".join(f"<li>{html.escape(w)}</li>" for w in r.warnings)
            self.result_label.setText(
                f"<b style='color:{word[1]}'>{word[0]}</b>: error {r.rms_cm:.2f} cm ({r.rms_px:.1f} px) from "
                f"{r.n_points} points. Target below 1 cm; above 2.5 cm blocks the run.<br>Camera at "
                f"({', '.join(f'{v:.2f}' for v in r.camera_position)}) m. Light-blue circles show where every "
                f"point should be.{'<ul>' + warn + '</ul>' if warn else ''}")
        self.override_box.setVisible(self.result is not None and self.result.status == "fail")
        if not self.override_box.isVisible():
            self.override_box.setChecked(False)
        self._fill_list()
        self._redraw()
        self._update_ok()

    def _redraw(self) -> None:
        self.view.draw_marks(self.state, self.result.reprojected if self.result else None, self._selected_label())

    def _update_ok(self) -> None:
        ok = self.result is not None and (self.result.status != "fail" or self.override)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
