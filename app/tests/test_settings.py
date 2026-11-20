"""Help > Settings: app-wide options (output folder for new sessions, screen on while working)."""

import pytest

from mocapapp.gui import common

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_settings_dialog_saves_and_checks(qtbot, tmp_path, monkeypatch):
    from mocapapp.gui import settings_dialog
    from mocapapp.gui.settings_dialog import SettingsDialog

    errors = []
    monkeypatch.setattr(settings_dialog, "error_box", lambda _p, title, _t: errors.append(title))
    assert common.keep_screen_on() and common.last_output_root() == ""  # defaults
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.out_edit.setText(str(tmp_path / "missing"))
    dlg.accept()
    assert errors == ["Folder not found"] and common.last_output_root() == ""
    dlg.out_edit.setText(str(tmp_path))
    dlg.screen_box.setChecked(False)
    dlg.accept()
    assert common.last_output_root() == str(tmp_path) and not common.keep_screen_on()


def test_settings_in_main_window(qtbot, tmp_path, monkeypatch):
    from mocapapp.gui import power
    from mocapapp.gui.main_window import MainWindow
    from tests.m3_fakes import make_session

    calls = []
    monkeypatch.setattr(power.KeepAwake, "start", lambda self: calls.append("start"))
    monkeypatch.setattr(power.KeepAwake, "stop", lambda self: calls.append("stop"))
    win = MainWindow()
    qtbot.addWidget(win)
    help_menu = [m for m in win.menuBar().actions() if m.text() == "&Help"][0].menu()
    assert help_menu.actions()[0].text() == "&Settings ..."

    win.set_busy(True)
    win.set_busy(False)
    common.settings().setValue("keep_screen_on", False)
    win.set_busy(True)
    win.set_busy(False)
    assert calls == ["start", "stop", "stop"]  # off in Settings: never started

    # the output folder chosen in Settings stays when another session is opened
    chosen = tmp_path / "out"
    chosen.mkdir()
    common.settings().setValue("output_root", str(chosen))
    root = tmp_path / "elsewhere" / "S"
    root.mkdir(parents=True)
    win.set_session(root, make_session(root))
    assert common.last_output_root() == str(chosen)
    win.set_session(None, None)
    win.pages[0].refresh()
    assert win.pages[0].out_edit.text() == str(chosen)


def test_keep_awake_is_idempotent(monkeypatch):
    from mocapapp.gui.power import KeepAwake

    k = KeepAwake()
    k.start()
    k.start()
    assert k.active
    k.stop()
    k.stop()
    assert not k.active
