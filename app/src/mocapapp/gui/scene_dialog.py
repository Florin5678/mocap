"""Reference-points dialog (§6.4, D4): points table, world-axes panel with live diagrams, validation, presets."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.calibration import scene_points as sp
from mocapapp.gui.common import IssueView, error_box, para

AXIS_COLORS = {"X": QColor("#d32f2f"), "Y": QColor("#2e7d32"), "Z": QColor("#1565c0")}
MEASURING_HELP = """<b>Measuring the points</b><ul>
<li>Mark points with tape crosses on the floor and marks on boxes, stools or stands at different heights.</li>
<li>Measure each point from the origin along +X, +Y (to the left of +X) and up (Z), to within 5 mm, in the unit
chosen above the table (metres, centimetres, inches or feet).</li>
<li>Spread them through the whole space where the person moves; put several above the floor.</li>
<li>Worked example: origin = floor cross by the door, +X toward the window. A cross 2 m toward the window:
(2, 0, 0). A mark on a 0.75 m stool, 1 m toward the window and 1 m to the left: (1, 1, 0.75).</li>
<li>Accuracy budget: points within 5 mm give camera-position errors well below the 1 cm target.</li></ul>"""


def presets_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    path = Path(base or Path.home() / ".mocapapp") / "scene_presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SceneDiagram(QWidget):
    """Axis glyph, top-down map (X-Y, seen from above) and side view (X-Z) of the entered points."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.points: list[sp.ScenePoint] = []
        self.setMinimumSize(300, 230)

    def set_points(self, points: list[sp.ScenePoint]) -> None:
        self.points = points
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt name)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().base())
        w, h = self.width(), self.height()
        glyph = QRectF(0, 0, w * 0.32, h * 0.5)
        top = QRectF(w * 0.34, 0, w * 0.66, h * 0.56)
        side = QRectF(w * 0.34, h * 0.58, w * 0.66, h * 0.42)
        self._glyph(p, glyph)
        self._view(p, top, 0, 1, "Seen from above (X right, Y up the page)")
        self._view(p, side, 0, 2, "Seen from the side (X right, Z up)")
        p.end()

    def _arrow(self, p: QPainter, a: QPointF, b: QPointF, color: QColor, label: str) -> None:
        p.setPen(QPen(color, 2.5))
        p.drawLine(a, b)
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        for d in (2.6, -2.6):
            p.drawLine(b, QPointF(b.x() - 9 * math.cos(ang + d / 6), b.y() - 9 * math.sin(ang + d / 6)))
        f = QFont(p.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(QPointF(b.x() + 4 * math.cos(ang) - 4, b.y() + 12 * math.sin(ang) + 4), label)

    def _glyph(self, p: QPainter, r: QRectF) -> None:
        o = QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.72)
        s = min(r.width(), r.height()) * 0.42
        self._arrow(p, o, QPointF(o.x() + s, o.y() + s * 0.3), AXIS_COLORS["X"], "+X")
        self._arrow(p, o, QPointF(o.x() + s * 0.75, o.y() - s * 0.45), AXIS_COLORS["Y"], "+Y")
        self._arrow(p, o, QPointF(o.x(), o.y() - s), AXIS_COLORS["Z"], "+Z up")
        p.setPen(QPen(self.palette().text().color()))
        p.setBrush(self.palette().text())
        p.drawEllipse(o, 3, 3)
        p.drawText(QPointF(o.x() - 30, o.y() + 18), "origin")

    def _view(self, p: QPainter, r: QRectF, ia: int, ib: int, title: str) -> None:
        pen_text = QPen(self.palette().text().color())
        p.setPen(QPen(self.palette().mid().color()))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(2, 2, -2, -2))
        p.setPen(pen_text)
        p.drawText(QPointF(r.left() + 8, r.top() + 16), title)
        pts = [(q.label, q.xyz[ia], q.xyz[ib]) for q in self.points]
        xs = [a for _, a, _ in pts] + [0.0]
        ys = [b for _, _, b in pts] + [0.0]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 0.5)
        inner = r.adjusted(30, 26, -30, -14)
        scale = min(inner.width(), inner.height()) / span
        cx = inner.center().x() - (max(xs) + min(xs)) / 2 * scale
        cy = inner.center().y() + (max(ys) + min(ys)) / 2 * scale

        def to_px(a: float, b: float) -> QPointF:
            return QPointF(cx + a * scale, cy - b * scale)

        o = to_px(0, 0)
        names = "XYZ"
        self._arrow(p, o, QPointF(o.x() + 40, o.y()), AXIS_COLORS[names[ia]], "+" + names[ia])
        self._arrow(p, o, QPointF(o.x(), o.y() - 40), AXIS_COLORS[names[ib]], "+" + names[ib])
        p.setPen(pen_text)
        small = QFont(p.font())
        small.setBold(False)
        small.setPointSizeF(max(7.0, small.pointSizeF() - 2))
        p.setFont(small)
        for label, a, b in pts:
            q = to_px(a, b)
            p.setBrush(self.palette().text())
            p.drawEllipse(q, 3, 3)
            p.drawText(QPointF(q.x() + 5, q.y() - 4), label)


class SceneDialog(QDialog):
    def __init__(self, parent: QWidget | None, scene: sp.Scene | None, has_extrinsics: bool = False,
                 presets: list | None = None, on_save_preset=None, title: str = "Reference points"):
        """presets (the project's PointsPreset list) and on_save_preset(name, scene): presets kept in the project
        (§16.2). Without them, presets are files in the app's settings folder (0.1.x)."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 600)
        self.has_extrinsics = has_extrinsics
        self.presets, self.on_save_preset = presets, on_save_preset
        self._original = scene.model_copy(deep=True) if scene else None
        scene = scene or sp.Scene()

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(para(f"At least {sp.MIN_POINTS} points, measured in the unit chosen here. They are saved in "
                          "metres for Pose2Sim (<code>object_coords_3d</code> in Config.toml)."))
        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel("<b>Unit of the numbers</b>"))
        self.unit_box = QComboBox()
        for key, name in sp.UNIT_NAMES.items():
            self.unit_box.addItem(name, key)
        self.unit_box.setCurrentIndex(max(0, self.unit_box.findData(scene.unit)))
        self.unit_box.setToolTip("The numbers in the table stay as typed; the unit says what they mean. Typed in "
                                 "centimetres while metres was chosen? Choose centimetres.")
        self.unit_box.currentIndexChanged.connect(self._unit_changed)
        unit_row.addWidget(self.unit_box)
        unit_row.addStretch()
        ll.addLayout(unit_row)
        self.table = QTableWidget(0, 4)
        self._set_headers()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._changed)
        ll.addWidget(self.table, 1)
        rows = QHBoxLayout()
        for text, slot in (("Add", self._add), ("Remove", self._remove), ("Up", lambda: self._move(-1)),
                           ("Down", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            rows.addWidget(b)
        ll.addLayout(rows)
        io = QHBoxLayout()
        for text, slot in (("Paste Config.toml snippet ...", self._paste), ("Import CSV ...", self._import_csv),
                           ("Export CSV ...", self._export_csv), ("Show as Config.toml", self._show_config)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            io.addWidget(b)
        ll.addLayout(io)
        pr = QHBoxLayout()
        for text, slot in (("Save as preset ...", self._save_preset), ("Load preset ...", self._load_preset)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            pr.addWidget(b)
        pr.addStretch()
        ll.addLayout(pr)

        right = QWidget()
        rl = QVBoxLayout(right)
        axes = QGroupBox("World axes (right-handed, Z up)")
        al = QVBoxLayout(axes)
        self.origin = QLineEdit(scene.origin_text)
        self.origin.setPlaceholderText("e.g. the tape cross on the floor by the door")
        self.x_text = QLineEdit(scene.x_axis_text)
        self.x_text.setPlaceholderText("e.g. the window (horizontal)")
        self.y_note = QLineEdit(scene.y_axis_note)
        self.y_note.setPlaceholderText("optional: your own description, e.g. the whiteboard wall")
        for label, w in (("The origin (0, 0, 0) is at", self.origin),
                         ("+X points from the origin toward", self.x_text)):
            al.addWidget(QLabel(f"<b>{label}</b>"))
            al.addWidget(w)
            w.textChanged.connect(self._validate)
        al.addWidget(para("<b>+Z</b> points up, away from the floor (fixed). The floor should be Z = 0."))
        y = QLabel("<b>+Y</b> follows from X and Z.")
        y.setWordWrap(True)
        al.addWidget(y)
        al.addWidget(self.y_note)
        rl.addWidget(axes)
        self.diagram = SceneDiagram()
        rl.addWidget(self.diagram, 1)
        osim = QLabel(sp.OPENSIM_NOTE)
        osim.setWordWrap(True)
        osim.setStyleSheet("color: palette(placeholder-text)")
        rl.addWidget(osim)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([400, 500])
        self.issues = IssueView(min_height=80)
        help_box = QGroupBox("How to measure (click to show)")
        help_box.setCheckable(True)
        help_box.setChecked(False)
        hl = QVBoxLayout(help_box)
        help_text = QLabel(MEASURING_HELP)
        help_text.setWordWrap(True)
        help_text.setVisible(False)
        help_box.toggled.connect(help_text.setVisible)
        hl.addWidget(help_text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save points")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addWidget(help_box)
        layout.addWidget(self.issues)
        layout.addWidget(buttons)
        self.set_points(scene.points)

    # ---------- table <-> points ----------

    @property
    def unit(self) -> str:
        return self.unit_box.currentData()

    def _set_headers(self) -> None:
        u = self.unit
        self.table.setHorizontalHeaderLabels(["Label", f"X ({u})", f"Y ({u})", f"Z ({u}) up"])

    def _unit_changed(self) -> None:
        self._set_headers()
        self._validate()

    def set_points(self, points: list[sp.ScenePoint]) -> None:
        """Show points given in metres, in the chosen unit."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for q in points:
            self._append(q.label, *(sp.from_metres(v, self.unit) for v in q.xyz))
        self.table.blockSignals(False)
        self._validate()

    def _append(self, label: str, x="", y="", z="") -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((label, x, y, z)):
            self.table.setItem(r, c, QTableWidgetItem(v if isinstance(v, str) else f"{v:g}"))

    def points(self) -> tuple[list[sp.ScenePoint], list[str]]:
        """Parsed rows in metres, and plain-language problems with rows that are not numbers."""
        pts, bad = [], []
        for r in range(self.table.rowCount()):
            cells = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(4)]
            if not any(cells):
                continue
            try:
                xyz = [sp.to_metres(float(v.replace(",", ".")), self.unit) for v in cells[1:]]
            except ValueError:
                bad.append(f"Row {r + 1} ({cells[0] or 'no label'}): X, Y and Z must be numbers.")
                continue
            pts.append(sp.ScenePoint(label=cells[0] or f"P{r + 1}", x=xyz[0], y=xyz[1], z=xyz[2]))
        return pts, bad

    def scene(self) -> sp.Scene:
        return sp.Scene(origin_text=self.origin.text().strip(), x_axis_text=self.x_text.text().strip(),
                        y_axis_note=self.y_note.text().strip(), unit=self.unit, points=self.points()[0])

    def _changed(self, _item) -> None:
        self._validate()

    def _validate(self) -> None:
        from mocapapp.core.validate import Issue

        pts, bad = self.points()
        issues = [Issue("error", "table", b, "") for b in bad] + sp.check_scene(self.scene())
        self.issues.show_issues(issues, "The reference points look fine.")
        self.diagram.set_points(pts)
        self.ok_button.setEnabled(not bad)

    # ---------- buttons ----------

    def _add(self) -> None:
        self.table.blockSignals(True)
        self._append(f"P{self.table.rowCount() + 1}")
        self.table.blockSignals(False)
        self.table.setCurrentCell(self.table.rowCount() - 1, 1)

    def _remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
        self._validate()

    def _move(self, d: int) -> None:
        r = self.table.currentRow()
        if r < 0 or not 0 <= r + d < self.table.rowCount():
            return
        pts = [[self.table.item(k, c).text() if self.table.item(k, c) else "" for c in range(4)]
               for k in range(self.table.rowCount())]
        pts[r], pts[r + d] = pts[r + d], pts[r]
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row in pts:
            self._append(*row)
        self.table.blockSignals(False)
        self.table.setCurrentCell(r + d, 0)
        self._validate()

    def _paste(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Paste Config.toml snippet",
                                                 "object_coords_3d = [[x, y, z], ...] (comments become labels):")
        if ok and text.strip():
            try:
                self.set_points(sp.from_snippet(text))
            except ValueError as exc:
                error_box(self, "Not understood", str(exc))

    def _import_csv(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Import points", str(Path.home()), "CSV (*.csv);;All files (*)")
        if name:
            try:
                self.set_points(sp.from_csv(Path(name).read_text(encoding="utf-8-sig"), self.unit))
            except (OSError, ValueError) as exc:
                error_box(self, "Not imported", str(exc))

    def _export_csv(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, "Export points", str(Path.home() / "reference_points.csv"),
                                              "CSV (*.csv)")
        if name:
            Path(name).write_text(sp.to_csv(self.points()[0], self.unit), encoding="utf-8")

    def _show_config(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("As Config.toml")
        lay = QVBoxLayout(dlg)
        lay.addWidget(para("Written into every trial's Config.toml under [calibration.calculate.extrinsics.scene]:"))
        text = QPlainTextEdit(self.scene().as_config_snippet())
        text.setReadOnly(True)
        lay.addWidget(text)
        dlg.resize(600, 400)
        dlg.exec()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Name of this point set (e.g. Lab room A):")
        if ok and name.strip():
            if self.on_save_preset is not None:
                self.on_save_preset(name.strip(), self.scene())
                return
            safe = "".join(ch for ch in name.strip() if ch.isalnum() or ch in " -_")[:60]
            sp.save_scene(self.scene(), presets_dir() / f"{safe}.json")

    def _load_preset(self) -> None:
        if self.presets is not None:
            found = {f"{p.name} ({p.id})": p for p in self.presets}
        else:
            found = {p.stem: p for p in sorted(presets_dir().glob("*.json"))}
        names = list(found)
        if not names:
            QMessageBox.information(self, "No presets", "No saved point sets yet.")
            return
        name, ok = QInputDialog.getItem(self, "Load preset", "Point set:", names, 0, False)
        if ok:
            item = found[name]
            scene = sp.Scene.model_validate(item.points) if self.presets is not None else sp.load_scene(item)
            self.origin.setText(scene.origin_text)
            self.x_text.setText(scene.x_axis_text)
            self.y_note.setText(scene.y_axis_note)
            self.unit_box.blockSignals(True)
            self.unit_box.setCurrentIndex(max(0, self.unit_box.findData(scene.unit)))
            self.unit_box.blockSignals(False)
            self._set_headers()
            self.set_points(scene.points)

    def _accept(self) -> None:
        # changed points no longer delete the camera positions: they are recomputed from the saved clicks
        self.accept()
