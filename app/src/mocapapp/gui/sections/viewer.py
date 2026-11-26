"""The 3D and Data tabs of Results (PROJECT.md §16.3): a trial's results as a 3D skeleton (play, scrub, rotate) and as
data (joint angles and marker positions over time: plot, table, comparison between trials, CSV). Reads the trial
folders; no export needed. The trial shown is chosen in the Results list (`show_trial`)."""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import outputs
from mocapapp.gui.common import error_box, open_path
from mocapapp.gui.main_window import Page

LEFT, RIGHT, MIDDLE, EXTRA = QColor("#1f77b4"), QColor("#d62728"), QColor("#444444"), QColor("#9e9e9e")
DEFAULT_ANGLES = ["arm_flex_r", "elbow_flex_r"]
NO_PARENT = QModelIndex()
SPEEDS = [("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)]


def side(name: str) -> QColor:
    if name[:1] in ("L", "l") and (len(name) > 1 and (name[1].isupper() or name[1] == "_")):
        return LEFT
    if name[:1] in ("R", "r") and (len(name) > 1 and (name[1].isupper() or name[1] == "_")):
        return RIGHT
    return MIDDLE


class SkeletonView(QWidget):
    """Orthographic 3D view of one frame. Drag to rotate, wheel to zoom, double-click to reset."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 320)
        self.markers: outputs.Markers | None = None
        self.frame = 0
        self.show_extra = True
        self.reset_view()
        self._drag = None

    def reset_view(self) -> None:
        self.yaw, self.pitch, self.zoom = 30.0, 15.0, 1.0
        self.update()

    def set_markers(self, markers: outputs.Markers | None) -> None:
        self.markers = markers
        self.frame = 0
        if markers is not None:
            pts = markers.xyz.reshape(-1, 3)
            pts = pts[~np.isnan(pts).any(axis=1)]
            if len(pts):
                lo, hi = pts.min(axis=0), pts.max(axis=0)
                self.center = np.array([(lo[0] + hi[0]) / 2, (min(lo[1], 0) + hi[1]) / 2, (lo[2] + hi[2]) / 2])
                self.extent = float(max(hi - lo)) + 0.5
            else:
                self.center, self.extent = np.zeros(3), 2.0
        self.update()

    def set_frame(self, i: int) -> None:
        self.frame = i
        self.update()

    def project(self, p) -> QPointF:
        a, b = math.radians(self.yaw), math.radians(self.pitch)
        x, y, z = p[0] - self.center[0], p[1] - self.center[1], p[2] - self.center[2]
        sx, sz = x * math.cos(a) - z * math.sin(a), x * math.sin(a) + z * math.cos(a)
        sy = y * math.cos(b) - sz * math.sin(b)
        scale = self.zoom * min(self.width(), self.height()) / max(self.extent, 0.5)
        return QPointF(self.width() / 2 + sx * scale, self.height() / 2 - sy * scale)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt name)
        g = QPainter(self)
        g.setRenderHint(QPainter.RenderHint.Antialiasing)
        g.fillRect(self.rect(), QColor("white"))
        if self.markers is None:
            g.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No 3D results for this trial yet.")
            return
        m = self.markers
        g.setPen(QPen(QColor("#e0e0e0"), 1))
        cx, cz = self.center[0], self.center[2]
        for k in range(-4, 5):  # floor (Y = 0), 0.5 m squares
            g.drawLine(self.project((cx + k * 0.5, 0, cz - 2)), self.project((cx + k * 0.5, 0, cz + 2)))
            g.drawLine(self.project((cx - 2, 0, cz + k * 0.5)), self.project((cx + 2, 0, cz + k * 0.5)))
        origin = np.zeros(3)
        for axis, color in ((0, "#c62828"), (1, "#2e7d32"), (2, "#1565c0")):  # X, Y, Z of the scene, 0.3 m
            end = origin.copy()
            end[axis] = 0.3
            g.setPen(QPen(QColor(color), 2))
            g.drawLine(self.project(origin), self.project(end))
            g.drawText(self.project(end * 1.15), "XYZ"[axis])
        fr = m.xyz[min(self.frame, len(m.xyz) - 1)]
        idx = {n: i for i, n in enumerate(m.names)}
        for a, b in m.bones:
            p, q = fr[idx[a]], fr[idx[b]]
            if np.isnan(p).any() or np.isnan(q).any():
                continue
            color = LEFT if LEFT in (side(a), side(b)) else RIGHT if RIGHT in (side(a), side(b)) else MIDDLE
            g.setPen(QPen(color, 3))
            g.drawLine(self.project(p), self.project(q))
        for i, name in enumerate(m.names):
            p = fr[i]
            extra = name.endswith("_study")
            if np.isnan(p).any() or (extra and not self.show_extra):
                continue
            color = EXTRA if extra else side(name)
            g.setPen(Qt.PenStyle.NoPen)
            g.setBrush(color)
            r = 2.5 if extra else 4
            g.drawEllipse(self.project(p), r, r)
        g.setPen(QColor("#555555"))
        g.drawText(8, self.height() - 8, "Drag to rotate, wheel to zoom, double-click to reset. Blue = left, "
                                         "red = right, grey dots = LSTM markers. Grid: floor, 0.5 m squares.")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._drag = (e.position(), self.yaw, self.pitch)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is None:
            return
        start, yaw, pitch = self._drag
        d = e.position() - start
        self.yaw = yaw - d.x() * 0.5
        self.pitch = max(-89.0, min(89.0, pitch + d.y() * 0.5))
        self.update()

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._drag = None

    def mouseDoubleClickEvent(self, _e) -> None:  # noqa: N802
        self.reset_view()

    def wheelEvent(self, e) -> None:  # noqa: N802
        self.zoom = max(0.2, min(8.0, self.zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()


class TableModel(QAbstractTableModel):
    """Time plus the chosen columns of an outputs.Table, without copying the numbers into Qt items."""

    def __init__(self):
        super().__init__()
        self.table: outputs.Table | None = None
        self.idx: list[int] = []

    def set(self, table: outputs.Table | None, columns: list[str]) -> None:
        self.beginResetModel()
        self.table = table
        self.idx = [table.columns.index(c) for c in columns] if table else []
        self.endResetModel()

    def rowCount(self, parent=NO_PARENT) -> int:  # noqa: N802
        return 0 if self.table is None or parent.isValid() else len(self.table.time)

    def columnCount(self, parent=NO_PARENT) -> int:  # noqa: N802
        return 0 if self.table is None or parent.isValid() else 1 + len(self.idx)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or self.table is None:
            return None
        r, c = index.row(), index.column()
        v = self.table.time[r] if c == 0 else self.table.values[r, self.idx[c - 1]]
        return "" if np.isnan(v) else f"{v:.3f}" if c == 0 else f"{v:.4g}"

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole or self.table is None:
            return None
        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)
        if section == 0:
            return "time (s)"
        i = self.idx[section - 1]
        return f"{self.table.columns[i]} ({self.table.units[i]})"


class Viewer(Page):
    """Never shown itself: Results shows its two tabs, `view_tab` and `data_tab`."""

    key, title = "viewer", "Viewer"

    def __init__(self, win):
        super().__init__(win)
        self._cache: dict[tuple[str, float], object] = {}
        self.markers: outputs.Markers | None = None
        self.checked: dict[str, set[str]] = {"angles": set(DEFAULT_ANGLES), "markers": set()}
        self.canvas = None

        self.trial_box = QComboBox(self)  # trials with results, then imported data (not shown: Results chooses)
        self.trial_box.setVisible(False)
        self.trial_box.currentIndexChanged.connect(lambda _: self.load_trial())
        self.source_box = QComboBox()
        self.source_box.setToolTip("Which 3D marker file to show: smoothed + LSTM is what the joint angles are "
                                   "usually computed from.")
        self.source_box.currentIndexChanged.connect(lambda _: self.load_markers())
        self.view_tab = self._view_tab()
        self.data_tab = self._data_tab()

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

    # ---------- layout ----------

    def _view_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("3D markers"))
        top.addWidget(self.source_box, 1)
        v.addLayout(top)
        self.view = SkeletonView()
        v.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self.set_frame)
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(190)
        self.speed_box = QComboBox()
        for text, value in SPEEDS:
            self.speed_box.addItem(text, value)
        self.speed_box.setCurrentIndex(2)
        self.speed_box.setToolTip("Playback speed")
        self.extra_box = QCheckBox("LSTM markers")
        self.extra_box.setChecked(True)
        self.extra_box.toggled.connect(self._show_extra)
        reset = QPushButton("Reset view")
        reset.clicked.connect(self.view.reset_view)
        for widget in (self.play_button, self.slider, self.time_label, self.speed_box, self.extra_box, reset):
            row.addWidget(widget, 1 if widget is self.slider else 0)
        v.addLayout(row)
        return w

    def _data_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        left = QVBoxLayout()
        self.dataset_box = QComboBox()
        self.dataset_box.addItem("Joint angles (.mot)", "angles")
        self.dataset_box.addItem("Marker positions (.trc)", "markers")
        self.dataset_box.currentIndexChanged.connect(lambda _: self.show_data())
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter, e.g. arm, elbow, _r, RWrist")
        self.filter_edit.textChanged.connect(self._filter_columns)
        self.columns = QListWidget()
        self.columns.itemChanged.connect(self._column_toggled)
        self.compare_box = QCheckBox("Compare all trials")
        self.compare_box.setToolTip("Plot the ticked columns of every trial with results (and the imported Pose2Sim "
                                    "data) on top of each other.")
        self.compare_box.toggled.connect(lambda _: self.plot())
        self.speed_plot_box = QCheckBox("Plot speed (rate of change)")
        self.speed_plot_box.setToolTip("deg/s for angles, m/s for positions")
        self.speed_plot_box.toggled.connect(lambda _: self.plot())
        self.csv_button = QPushButton("Save as CSV ...")
        self.csv_button.setToolTip("The ticked columns of this trial (all columns if none is ticked), for Excel, "
                                   "R, Python or MATLAB.")
        self.csv_button.clicked.connect(self.save_csv)
        self.folder_button = QPushButton("Open the results folder")
        self.folder_button.clicked.connect(self._open_folder)
        for widget in (QLabel("Show"), self.dataset_box, self.filter_edit, self.columns, self.compare_box,
                       self.speed_plot_box, self.csv_button, self.folder_button):
            left.addWidget(widget, 1 if widget is self.columns else 0)
        side_panel = QWidget()
        side_panel.setLayout(left)
        side_panel.setMaximumWidth(300)

        self.plot_area = QVBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.table_model = TableModel()
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addLayout(self.plot_area, 1)
        tl.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.table_view)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        outer.addWidget(side_panel)
        outer.addWidget(split, 1)
        return w

    def _ensure_plot(self) -> None:
        if self.canvas is not None:
            return
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure

        self.figure = Figure(figsize=(6, 3.2), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(220)
        self.plot_area.addWidget(NavigationToolbar2QT(self.canvas, self))
        self.plot_area.addWidget(self.canvas, 1)
        self.axes = self.figure.add_subplot()
        self.cursor = None

    # ---------- state ----------

    def gates_next(self) -> bool:
        return False

    def trial_dir(self, label: str | None = None) -> Path:
        """The results folder of a trial (its label) or of imported Pose2Sim data (its id)."""
        return outputs.result_dir(self.session, self.root, label or self.trial_box.currentData())

    def short_name(self, key: str) -> str:
        """For legends and summaries: 'trial 3', or the imported data's name."""
        link = outputs.find_link(self.session, key) if key.startswith("i") else None
        return link.name if link else f"trial {key}"

    def _read(self, path: Path, reader):
        key = (str(path), path.stat().st_mtime)
        if key not in self._cache:
            self._cache[key] = reader(path)
        return self._cache[key]

    def refresh(self) -> None:
        if self.session is None:
            self.timer.stop()
            return
        current = self.trial_box.currentData()
        trials = outputs.viewable(self.session, self.root)
        self.trial_box.blockSignals(True)
        self.trial_box.clear()
        for key, title in trials:
            self.trial_box.addItem(title, key)
        found = self.trial_box.findData(current)
        self.trial_box.setCurrentIndex(max(found, 0))
        self.trial_box.blockSignals(False)
        self.view_tab.setEnabled(bool(trials))
        self.data_tab.setEnabled(bool(trials))
        self.load_trial()

    def show_trial(self, label: str | None) -> bool:
        """Show this trial (False: it has no results to show)."""
        found = self.trial_box.findData(label)
        if found >= 0 and found != self.trial_box.currentIndex():
            self.trial_box.setCurrentIndex(found)
        return found >= 0

    def load_trial(self) -> None:
        self.timer.stop()
        self.play_button.setText("Play")
        if self.trial_box.currentData() is None:
            self.source_box.clear()
            self.markers = None
            self.view.set_markers(None)
            self.table_model.set(None, [])
            return
        files = outputs.marker_files(self.trial_dir())
        previous = self.source_box.currentText()
        self.source_box.blockSignals(True)
        self.source_box.clear()
        for label, path in files.items():
            self.source_box.addItem(label, str(path))
        self.source_box.setCurrentIndex(max(self.source_box.findText(previous), 0))
        self.source_box.blockSignals(False)
        self.load_markers()

    def load_markers(self) -> None:
        path = self.source_box.currentData()
        self.markers = None
        if path:
            try:
                self.markers = self._read(Path(path), outputs.read_markers)
            except (OSError, outputs.OutputError) as exc:
                error_box(self, "Cannot show the 3D markers", str(exc))
        self.view.set_markers(self.markers)
        n = len(self.markers.time) if self.markers else 1
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(n - 1, 0))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.play_button.setEnabled(self.markers is not None)
        self.set_frame(0)
        self.show_data()

    def current_table(self, label: str | None = None) -> outputs.Table | None:
        """The Data tab's table for a trial: joint angles, or the positions from the chosen marker source."""
        tdir = self.trial_dir(label)
        if self.dataset_box.currentData() == "angles":
            mot = outputs.angle_file(tdir)
            return self._read(mot, outputs.read_angles) if mot else None
        files = outputs.marker_files(tdir)
        path = files.get(self.source_box.currentText()) or next(iter(files.values()), None)
        return self._read(path, outputs.read_markers).table() if path else None

    # ---------- 3D ----------

    def set_frame(self, i: int) -> None:
        self.view.set_frame(i)
        if self.markers is not None and len(self.markers.time):
            t = self.markers.time[min(i, len(self.markers.time) - 1)]
            self.time_label.setText(f"{t:.2f} s   frame {i + 1}/{len(self.markers.time)}")
            self._move_cursor(t)
        else:
            self.time_label.setText("")

    def toggle_play(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play_button.setText("Play")
            return
        if self.markers is None:
            return
        if self.slider.value() >= self.slider.maximum():
            self.slider.setValue(0)
        self._clock = (time.monotonic(), self.slider.value())
        self.timer.start()
        self.play_button.setText("Pause")

    def _tick(self) -> None:
        """Real-time playback (frames are skipped rather than slowed down when drawing is slow)."""
        if self.markers is None or not self.isVisible():
            self.timer.stop()
            self.play_button.setText("Play")
            return
        start, first = self._clock
        rate = self.markers.rate or 30.0
        frame = first + int((time.monotonic() - start) * rate * self.speed_box.currentData())
        n = len(self.markers.time)
        if frame >= n:
            self._clock = (time.monotonic(), 0)
            frame = 0
        self.slider.setValue(frame)

    def _show_extra(self, on: bool) -> None:
        self.view.show_extra = on
        self.view.update()

    # ---------- data ----------

    def show_data(self) -> None:
        kind = self.dataset_box.currentData()
        table = None
        if self.trial_box.currentData() is not None:
            try:
                table = self.current_table()
            except (OSError, outputs.OutputError) as exc:
                self.summary.setText(str(exc))
        self.columns.blockSignals(True)
        self.columns.clear()
        if table is not None:
            if kind == "markers" and not self.checked["markers"]:
                wrist = next((c for c in table.columns if c.startswith("RWrist")), None)
                self.checked["markers"] = {wrist.rsplit(" ", 1)[0] + " Y"} if wrist else set()
            for c in table.columns:
                item = QListWidgetItem(f"{c} ({table.unit(c)})")
                item.setData(Qt.ItemDataRole.UserRole, c)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if c in self.checked[kind] else Qt.CheckState.Unchecked)
                self.columns.addItem(item)
        self.columns.blockSignals(False)
        self._filter_columns(self.filter_edit.text())
        if table is None:
            key = self.trial_box.currentData() or ""
            self.summary.setText("" if kind != "angles" or not key else
                                 "This folder has no joint angles (no kinematics/*.mot)." if key.startswith("i")
                                 else "This trial has no joint angles yet (the kinematics step did not finish).")
        self.csv_button.setEnabled(table is not None)
        self.plot()

    def ticked(self) -> list[str]:
        out = []
        for i in range(self.columns.count()):
            item = self.columns.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _column_toggled(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        group = self.checked[self.dataset_box.currentData()]
        (group.add if item.checkState() == Qt.CheckState.Checked else group.discard)(name)
        self.plot()

    def _filter_columns(self, text: str) -> None:
        words = text.lower().split()
        for i in range(self.columns.count()):
            item = self.columns.item(i)
            item.setHidden(bool(words) and not all(w in item.text().lower() for w in words))

    def plot(self) -> None:
        if self.trial_box.currentData() is None:
            return
        self._ensure_plot()
        ax = self.axes
        ax.clear()
        self.cursor = None
        columns = self.ticked()
        speed = self.speed_plot_box.isChecked()
        try:
            table = self.current_table()
        except (OSError, outputs.OutputError):
            table = None
        self.table_model.set(table, columns or (table.columns if table else []))
        if table is None or not columns:
            ax.text(0.5, 0.5, "Tick one or more columns on the left to plot them." if table is not None
                    else "No data for this trial.", ha="center", va="center", transform=ax.transAxes)
            self.canvas.draw_idle()
            self.summary.setText("")
            return
        labels = [self.trial_box.currentData()]
        if self.compare_box.isChecked():
            labels = [self.trial_box.itemData(i) for i in range(self.trial_box.count())]
        lines = []
        for label in labels:
            try:
                tab = table if label == self.trial_box.currentData() else self.current_table(label)
            except (OSError, outputs.OutputError):
                tab = None
            if tab is None:
                continue
            for c in columns:
                if c not in tab.columns:
                    continue
                y = outputs.speed(tab, c) if speed else tab.column(c)
                name = f"{c} - {self.short_name(label)}" if len(labels) > 1 else c
                ax.plot(tab.time, y, label=name, linewidth=1.4)
            for r in outputs.summary(tab, [c for c in columns if c in tab.columns]):
                if r["min"] is None:
                    lines.append(f"{r['column']}{f' ({self.short_name(label)})' if len(labels) > 1 else ''}: "
                                 "no data")
                    continue
                lines.append(f"<b>{r['column']}</b>{f' ({self.short_name(label)})' if len(labels) > 1 else ''}: "
                             f"min {r['min']:.3g}, max {r['max']:.3g}, range {r['range']:.3g} {r['unit']}; "
                             f"peak speed {r['peak_speed'] or 0:.3g} {outputs.rate_unit(r['unit'])}")
        units = sorted({table.unit(c) for c in columns})
        unit = ", ".join(outputs.rate_unit(u) for u in units) if speed else ", ".join(units)
        ax.set_xlabel("time (s)")
        ax.set_ylabel(("speed" if speed else "value") + f" ({unit})")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize="small", loc="best")
        if self.markers is not None:
            t = self.markers.time[min(self.slider.value(), len(self.markers.time) - 1)]
            self.cursor = ax.axvline(t, color="#888888", linewidth=1, linestyle="--")
        self.canvas.draw_idle()
        self.summary.setText("<br>".join(lines))

    def _move_cursor(self, t: float) -> None:
        if self.cursor is not None and self.data_tab.isVisible():
            self.cursor.set_xdata([t, t])
            self.canvas.draw_idle()

    def save_csv(self) -> None:
        table = self.current_table()
        if table is None:
            return
        label = self.trial_box.currentData()
        kind = "joint_angles" if self.dataset_box.currentData() == "angles" else "marker_positions"
        link = outputs.find_link(self.session, label) if label.startswith("i") else None
        if link:
            start = Path(link.path) / f"{Path(link.path).name}_{kind}.csv"
        else:
            start = Path(self.root) / f"{self.session.trial(label).code}_trial{label}_{kind}.csv"
        name, _ = QFileDialog.getSaveFileName(self, "Save as CSV", str(start), "CSV (*.csv)")
        if not name:
            return
        try:
            out = outputs.write_csv(table, Path(name), self.ticked() or None)
        except (OSError, outputs.OutputError) as exc:
            error_box(self, "CSV not saved", str(exc))
            return
        self.win.statusBar().showMessage(f"Saved: {out}", 6000)

    def _open_folder(self) -> None:
        if self.trial_box.currentData() is not None:
            tdir = self.trial_dir()
            open_path(tdir / ("kinematics" if self.dataset_box.currentData() == "angles" else "pose-3d"))

    def stop(self) -> None:
        """Results hid the 3D tab: stop playing."""
        self.timer.stop()
        self.play_button.setText("Play")
