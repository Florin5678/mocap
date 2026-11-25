"""GUI tests (pytest-qt): greeting and tutorial, the reference-points dialog, and the sample data with the real
Pose2Sim run (slow). The project window itself is tested in test_window_*.py.

Set MOCAP_SCREENSHOTS=<folder> to save a PNG of the screens.
"""

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

from mocapapp.core.session import Layout

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
SHOTS = os.environ.get("MOCAP_SCREENSHOTS")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "qsettings"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    def no_modal(box):  # a message box in a test means something went wrong: fail instead of hanging
        raise AssertionError(f"message box: {box.text()} {box.detailedText()}")

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", no_modal)


def shot(widget, name: str) -> None:
    if SHOTS:
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(Path(SHOTS) / f"{name}.png"))


def test_greeting_has_exactly_two_buttons(qtbot):
    from mocapapp.gui.greeting import GreetingDialog

    dlg = GreetingDialog()
    qtbot.addWidget(dlg)
    buttons = dlg.findChildren(QPushButton)
    assert len(buttons) == 2
    assert "How it works" in buttons[0].text() and "recommended" in buttons[0].text()
    assert buttons[1].text() == "Start"
    shot(dlg, "00_greeting")
    buttons[1].click()
    assert dlg.choice == "start"


def test_tutorial_pages_and_end_buttons(qtbot):
    from mocapapp.gui.greeting import TutorialDialog

    dlg = TutorialDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    assert len(dlg.pages) == 8 and dlg.title.text().startswith("1.")
    assert not dlg.back_button.isVisible() and not dlg.start_button.isVisible()
    for _ in range(10):
        dlg.next_button.click()
    assert dlg.index == 7 and dlg.back_button.isVisible() and dlg.start_button.isVisible()
    assert dlg.consent_button.isVisible() and not dlg.board_button.isVisible()
    shot(dlg, "01_tutorial_last")
    dlg.show_page(6)
    assert dlg.board_button.isVisible()
    dlg.show_page(5)
    assert "sync" in dlg.body.toPlainText().lower()
    dlg.show_page(7)
    dlg.back_button.click()
    assert dlg.choice == "back"


@pytest.mark.slow
def test_sample_data_in_gui(qtbot, tmp_path, monkeypatch):
    """The sample data on the Session hub, with the real Pose2Sim run (~4 min)."""
    from mocapapp.core.sample import make_sample_session
    from mocapapp.gui.main_window import MainWindow
    from mocapapp.gui.sections import run_bar

    monkeypatch.setattr(run_bar, "confirm", lambda *a, **k: True)
    root, _ = make_sample_session(tmp_path / "sessions", progress=lambda m: None)
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    win.open_session(root)
    hub = win.section("session")
    assert win.current_page() is hub and win.badges()["cameras"] == win.badges()["layouts"] == "✓"
    assert "consent" in hub.cells["1"][0].text().lower() and not hub.cells["1"][1].isEnabled()
    win.go("participants")
    people = win.section("participants")
    headers = [people.table.horizontalHeaderItem(c).text() for c in range(people.table.columnCount())]
    box = people.table.cellWidget(0, headers.index("Consent"))
    box.setCurrentIndex(box.findData("delete"))
    qtbot.waitUntil(lambda: win.session.participant("1").consent.attested)
    win.go("session")
    assert hub.cells["1"][1].isEnabled(), hub.cells["1"][0].text()
    shot(win, "80_sample_run")
    hub.cells["1"][1].click()  # Run this trial
    qtbot.waitUntil(lambda: not win.busy, timeout=1200000)
    assert win.session.run_state.trial_status["1"] == "done", win.session.run_state
    win.go("results")
    res = win.section("results")
    assert res.results[0].light in ("green", "amber")
    res.do_export()
    qtbot.waitUntil(lambda: not win.busy, timeout=120000)
    shot(win, "81_sample_results")
    assert (Layout(root).export / "manifest.json").is_file()


def test_scene_dialog_unit(qtbot):
    from mocapapp.core.calibration.scene_points import Scene, ScenePoint
    from mocapapp.gui.scene_dialog import SceneDialog

    typed = Scene(points=[ScenePoint(label="table", x=40, y=-95, z=71.5)])  # centimetres, saved as metres
    dlg = SceneDialog(None, typed)
    qtbot.addWidget(dlg)
    assert dlg.table.horizontalHeaderItem(1).text() == "X (m)"
    dlg.unit_box.setCurrentIndex(dlg.unit_box.findData("cm"))  # the numbers stay; their meaning changes
    assert dlg.table.horizontalHeaderItem(3).text() == "Z (cm) up" and dlg.table.item(0, 3).text() == "71.5"
    fixed = dlg.scene()
    assert fixed.unit == "cm" and fixed.points[0].xyz == pytest.approx([0.4, -0.95, 0.715])
    again = SceneDialog(None, fixed)  # reopened: shown in centimetres as typed
    qtbot.addWidget(again)
    assert again.unit == "cm" and again.table.item(0, 3).text() == "71.5"
