"""Help > Settings: options of the app as a whole, on this computer. Options of a project are in its Session
settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.validate import is_ascii_path
from mocapapp.gui.common import error_box, keep_screen_on, last_output_root, note, settings


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.addWidget(note("These settings apply to the whole app on this computer. The settings of a project "
                              "(pose detection, smoothing, deletion of raw footage) are in its Session settings; the "
                              "calibration board is in Cameras."))

        folders = QGroupBox("Folders")
        fl = QVBoxLayout(folders)
        fl.addWidget(note("Output folder for new sessions: each new session gets its own folder in it. Its path "
                          "must use only plain letters, digits and spaces (no æ/ø/å or other accents). An existing "
                          "session is moved with File > Move session to another output folder."))
        row = QHBoxLayout()
        self.out_edit = QLineEdit(last_output_root())
        self.out_edit.setPlaceholderText("not set: choose a folder")
        browse = QPushButton("Browse ...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.out_edit)
        row.addWidget(browse)
        fl.addLayout(row)
        layout.addWidget(folders)

        running = QGroupBox("Running")
        rl = QVBoxLayout(running)
        self.screen_box = QCheckBox("Keep the screen on while the app is working")
        self.screen_box.setChecked(keep_screen_on())
        rl.addWidget(self.screen_box)
        rl.addWidget(note("While a run, copying videos or a calibration is going. This also keeps the computer from "
                          "going to sleep, which would pause a run. Back to normal as soon as the app is idle."))
        layout.addWidget(running)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        start = self.out_edit.text().strip() or str(Path.home())
        name = QFileDialog.getExistingDirectory(self, "Output folder for new sessions", start)
        if name:
            self.out_edit.setText(str(Path(name)))

    def accept(self) -> None:
        out = self.out_edit.text().strip()
        if out and not Path(out).is_dir():
            error_box(self, "Folder not found", f"{out} does not exist. Choose an existing folder.")
            return
        if out and not is_ascii_path(Path(out)):
            error_box(self, "Folder not usable", "The 3D software cannot save in a folder whose path has accents or "
                      "letters such as æ/ø/å. Choose a folder with only plain letters, digits and spaces.")
            return
        settings().setValue("output_root", str(Path(out)) if out else "")
        settings().setValue("keep_screen_on", self.screen_box.isChecked())
        super().accept()
