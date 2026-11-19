"""GUI entry point: greeting dialog -> tutorial or main window (§6.0)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mocapapp.gui.common import APP, ORG


def application() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    font = app.font()
    if font.pointSize() < 10:
        font.setPointSize(10)
        app.setFont(font)
    return app


def start_flow() -> bool:
    """Greeting <-> tutorial loop. True when the user chose to start a session."""
    from mocapapp.gui.greeting import GreetingDialog, TutorialDialog

    while True:
        greeting = GreetingDialog()
        greeting.exec()
        if greeting.choice == "start":
            return True
        if greeting.choice != "tutorial":
            return False
        tut = TutorialDialog()
        tut.exec()
        if tut.choice == "start":
            return True
        if tut.choice != "back":
            return False


def run_gui(session: Path | None = None) -> int:
    """`session`: a session folder, its session.json, or a project file (.mocapproj)."""
    from mocapapp.gui.firstrun import first_start
    from mocapapp.gui.main_window import MainWindow

    app = application()
    first_start()
    if session is None and not start_flow():
        return 0
    win = MainWindow()
    if session is not None:
        win.open_path(session)
    win.show()
    return app.exec()


def main() -> None:
    sys.exit(run_gui(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
