"""Calibration videos of one camera and the trials that use each (0.1.0rc17): "Intrinsic calibration for cam01".

Rows are trials, columns are the camera's videos for that role; exactly one box is ticked per trial. Adding,
replacing and removing videos takes effect straight away; the ticks are saved with OK.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.workflow import (
    SetupError,
    add_calib_videos,
    remove_calib_video,
    replace_calib_video,
    set_calib_assignment,
)
from mocapapp.gui.common import confirm, error_box, fit_rows, para

VIDEO_FILTER = "Videos (*.mp4 *.mov *.avi *.mkv *.MP4 *.MOV);;All files (*)"
MULTIPLE_TIP = "Multiple calibration videos detected. Click to assign calibrations to trials"


def pick_videos(parent, title: str, folder: str, many: bool = True) -> list[Path]:
    if many:
        names, _ = QFileDialog.getOpenFileNames(parent, title, folder, VIDEO_FILTER)
        return [Path(n) for n in names]
    name, _ = QFileDialog.getOpenFileName(parent, title, folder, VIDEO_FILTER)
    return [Path(name)] if name else []


class CalibDialog(QDialog):
    def __init__(self, parent, session, root, camera: str, role: str):
        super().__init__(parent)
        self.session, self.root, self.camera, self.role = session, root, camera, role
        self.files_changed = False  # a video was added, replaced or removed: it has to be copied into the session
        self.setWindowTitle(f"{role.capitalize()} calibration for {camera}")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        what = ("the lens settings (zoom, resolution, focus mode) the trial was recorded with" if role == "intrinsic"
                else "the place the camera stood in during the trial")
        layout.addWidget(para(f"Tick, for every trial, the {role} video that matches {what}. Exactly one per trial; "
                              "the first column is the camera's main video."))
        self.table = QTableWidget(0, 0)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add videos ...")
        self.add_button.clicked.connect(self.add_videos)
        self.replace_button = QPushButton("Replace a video ...")
        self.remove_button = QPushButton("Remove a video ...")
        for b in (self.add_button, self.replace_button, self.remove_button):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.groups: dict[str, QButtonGroup] = {}
        self._fill({})

    @property
    def cam(self):
        return self.session.camera(self.camera)

    def videos(self) -> list[tuple[str, str]]:
        return self.cam.calib_videos(self.role)

    def choice(self) -> dict[str, str]:
        """trial label -> ticked video key, for the trials shown."""
        keys = [k for k, _ in self.videos()]
        return {label: keys[group.checkedId()] for label, group in self.groups.items() if group.checkedId() >= 0}

    def _fill(self, pending: dict[str, str]) -> None:
        videos = self.videos()
        trials = [t for t in self.session.trials if not t.withdrawn_at]
        table = self.table
        table.clear()
        table.setRowCount(len(trials))
        table.setColumnCount(len(videos))
        table.setHorizontalHeaderLabels([Path(f).name + ("\n(main)" if not k else "") for k, f in videos])
        for c, (_k, f) in enumerate(videos):
            table.horizontalHeaderItem(c).setToolTip(f)
        table.setVerticalHeaderLabels([f"Trial {t.label} ({t.code})" for t in trials])
        self.groups = {}
        keys = [k for k, _ in videos]
        for r, t in enumerate(trials):
            group = QButtonGroup(self)
            group.setExclusive(True)
            current = pending.get(t.label, self.cam.video_for(self.role, t.label))
            for c in range(len(videos)):
                box = QCheckBox()
                box.setEnabled(t.raw_present)
                if not t.raw_present:
                    box.setToolTip("Raw footage deleted: this trial keeps its calibration.")
                group.addButton(box, c)
                box.setChecked(keys[c] == current)
                cell = QWidget()
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cl.addWidget(box)
                table.setCellWidget(r, c, cell)
            self.groups[t.label] = group
        fit_rows(table, max_rows=10)
        extras = [(k, f) for k, f in videos if k]
        menu = QMenu(self.replace_button)
        for k, f in videos:
            menu.addAction(Path(f).name, lambda key=k: self.replace_video(key))
        self.replace_button.setMenu(menu)
        self.replace_button.setEnabled(bool(videos))
        menu = QMenu(self.remove_button)
        for k, f in extras:
            menu.addAction(Path(f).name, lambda key=k: self.remove_video(key))
        self.remove_button.setMenu(menu)
        self.remove_button.setEnabled(bool(extras))
        self.remove_button.setToolTip("The main video cannot be removed (replace it instead)." if not extras else
                                      "Its trials use the main video again.")

    def add_videos(self) -> None:
        files = pick_videos(self, f"More {self.role} videos for {self.camera}", self.cam.source_dir)
        if not files:
            return
        pending = self.choice()
        try:
            add_calib_videos(self.session, self.root, self.camera, self.role, files)
        except SetupError as exc:
            error_box(self, "Video not added", str(exc))
        self.files_changed = True
        self._fill(pending)

    def replace_video(self, key: str) -> None:
        files = pick_videos(self, f"Another {self.role} video for {self.camera}", self.cam.source_dir, many=False)
        if not files:
            return
        pending = self.choice()
        try:
            replace_calib_video(self.session, self.root, self.camera, self.role, key, files[0])
        except SetupError as exc:
            error_box(self, "Video not replaced", str(exc))
            return
        self.files_changed = True
        self._fill(pending)

    def remove_video(self, key: str) -> None:
        name = Path(self.cam.extra(self.role, key).file).name
        if not confirm(self, "Remove video", f"Remove {name} from {self.camera}'s {self.role} videos? Its trials use "
                       "the main video again; the file itself is not touched.", "Remove"):
            return
        pending = {t: k for t, k in self.choice().items() if k != key}
        try:
            remove_calib_video(self.session, self.root, self.camera, self.role, key)
        except SetupError as exc:
            error_box(self, "Video not removed", str(exc))
            return
        self.files_changed = True
        self._fill(pending)

    def save(self) -> None:
        choice = self.choice()
        changed = {t: k for t, k in choice.items() if k != self.cam.video_for(self.role, t)}
        if changed:
            try:
                set_calib_assignment(self.session, self.root, self.camera, self.role, changed)
            except SetupError as exc:
                error_box(self, "Not saved", str(exc))
                return
        self.accept()
