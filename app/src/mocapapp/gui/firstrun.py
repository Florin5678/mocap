"""First start: environment check with fix-it messages (FR-1.1) and the one-time model download (FR-1.2)."""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from mocapapp.gui.common import Background, error_box, settings


def problems() -> list:
    """Failed checks other than the models (offered separately) - never a stack trace."""
    from mocapapp.core.env import run_checks

    return [c for c in run_checks(None) if not c.ok and c.name != "Pose models"]


def show_problems(parent: QWidget | None, failed: list) -> None:
    text = "\n\n".join(f"{c.name}: {c.detail}\n-> {c.fix}" for c in failed)
    QMessageBox.warning(parent, "Some things need attention",
                        "The app found problems with this computer's setup:\n\n" + text +
                        "\n\nYou can still look around; processing may fail until these are fixed. "
                        "Help > Environment check shows this again.")


def offer_models(parent: QWidget | None, ask: bool = True, on_finished=None) -> bool:
    """Download the missing pose models with a progress dialog. Returns True if a download was started."""
    from mocapapp.core import models

    gone = models.missing(("balanced",))
    if not gone:
        if not ask:
            QMessageBox.information(parent, "Pose models", "The pose models are already downloaded.")
        return False
    if ask and QMessageBox.question(
            parent, "Download pose models?",
            f"Processing needs two pose-estimation models (about {models.SIZES_MB['balanced']} MB), downloaded "
            f"once into {models.cache_dir()}.\n\nDownload them now? Afterwards processing works offline. "
            "(Later: Help > Download pose models.)") != QMessageBox.StandardButton.Yes:
        return False
    dlg = QProgressDialog("Downloading pose models ...", None, 0, 0, parent)
    dlg.setWindowTitle("Pose models")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.show()

    def done(_):
        dlg.close()
        QMessageBox.information(parent, "Pose models", "The pose models are ready; processing now works offline.")

    def failed(msg, details):
        dlg.close()
        error_box(parent, "Download failed", msg, details)

    # parented to something that outlives this function, so the worker thread is never destroyed while running
    job = Background(parent or QApplication.instance(), models.download, ("balanced",), on_done=done,
                     on_error=failed, on_progress=dlg.setLabelText, progress=True)
    job.dialog = dlg
    if on_finished:
        job.finished.connect(on_finished)  # after the thread has really stopped
    return True


def copy_diagnostics(parent: QWidget | None) -> None:
    from mocapapp.core.diagnostics import diagnostics_text

    QApplication.clipboard().setText(diagnostics_text())
    QMessageBox.information(parent, "Diagnostics copied",
                            "Versions and environment checks were copied to the clipboard (your user name and "
                            "home folder are replaced; no session or participant data is included). Paste them "
                            "into your bug report.")


def first_start(parent: QWidget | None = None) -> None:
    """Run at every start; asks about the models once per version unless they are present."""
    from mocapapp import __version__

    failed = problems()
    if failed:
        show_problems(parent, failed)
    key = f"models_offered/{__version__}"
    if not settings().value(key, False, type=bool):
        settings().setValue(key, True)
        loop = QEventLoop()
        if offer_models(parent, on_finished=loop.quit):
            loop.exec()  # finish before the greeting opens
