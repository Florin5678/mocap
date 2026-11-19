"""GUI for M5: participant-data dialog, deletion previews and the Results page's deletion action."""

import pytest
from PySide6.QtWidgets import QMessageBox

from mocapapp.core.session import Layout, load_session
from mocapapp.gui import privacy_dialog
from mocapapp.gui.main_window import MainWindow
from tests.test_deletion import _finished
from tests.test_gui import isolated_settings, shot  # noqa: F401 (autouse fixture)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def boxes(monkeypatch):
    """Record message boxes instead of showing them; answer questions with Yes."""
    seen = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: seen.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: seen.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return seen


def auto_delete(monkeypatch, typed: dict | None = None, originals: bool = False):
    """Replace DeleteDialog.exec: tick originals if asked, type the confirmation, press Delete."""
    shown = []

    def fake_exec(dlg):
        dlg.originals.setChecked(originals)
        shown.append(dlg)
        if dlg.plan and dlg.plan.confirm_text:
            dlg.confirm_edit.setText((typed or {}).get(dlg.scope, dlg.plan.confirm_text))
        if dlg.delete_button.isEnabled():
            dlg.delete_button.click()
        return dlg.result()

    monkeypatch.setattr(privacy_dialog.DeleteDialog, "exec", fake_exec)
    return shown


def test_results_page_deletes_marked_footage_after_export(qtbot, tmp_path, monkeypatch, boxes):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    session.trials[1].consent.raw_footage_storage, session.trials[1].delete_raw = True, False
    session.calibration_source = "imported"  # skip the calibration steps of the wizard
    Layout(root).calibration_work.mkdir(exist_ok=True)
    Layout(root).calib_file.write_text("[cam_1]\n")
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(root, session)
    win.go(6)
    results = win.pages[6]
    assert win.current_page() is results and results.delete_now.isEnabled()
    assert "still stored" in results.deletion.text() and "kept with consent" in results.deletion.text()
    shown = auto_delete(monkeypatch)
    results.delete_now.click()
    assert [d.label for d in shown] == ["1"] and shown[0].after_run  # only the trial marked 'delete'
    assert "SD cards" in boxes[-1]
    assert not (Layout(root).inputs("cam01") / "trial_1.mp4").exists()
    assert (Layout(root).inputs("cam01") / "trial_2.mp4").exists()
    assert "deleted on" in results.deletion.text() and not results.delete_now.isEnabled()
    assert win.pages[5].is_complete()  # Run stays complete
    videos = win.pages[1]
    videos.refresh()
    assert not videos.check_button.isEnabled() and "Start a new session" in videos.status.text()


def test_delete_dialog_needs_code_for_originals(qtbot, tmp_path, monkeypatch, boxes):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    dlg = privacy_dialog.DeleteDialog(None, session, root, "raw", "2")
    qtbot.addWidget(dlg)
    assert dlg.delete_button.isEnabled() and not dlg.confirm_row.isVisibleTo(dlg)
    dlg.originals.setChecked(True)
    assert dlg.confirm_row.isVisibleTo(dlg) and not dlg.delete_button.isEnabled()
    assert "(c) Original recordings" in dlg.view.toPlainText()
    dlg.confirm_edit.setText("T1-ABCDEF1")
    assert not dlg.delete_button.isEnabled()
    dlg.confirm_edit.setText("t1-abcdef2")
    assert dlg.delete_button.isEnabled()
    shot(dlg, "m5_delete_raw_with_originals")
    blocked = privacy_dialog.DeleteDialog(None, _finished(tmp_path / "U", monkeypatch, exported=False),
                                          tmp_path / "U", "raw", "1", after_run=True)
    qtbot.addWidget(blocked)
    assert not blocked.delete_button.isEnabled() and "Cannot delete yet" in blocked.view.toPlainText()


def test_participant_data_dialog_withdrawal_and_session(qtbot, tmp_path, monkeypatch, boxes):
    root = tmp_path / "S"
    session = _finished(root, monkeypatch)
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(root, session)
    dlg = privacy_dialog.ParticipantDataDialog(win, session, root)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 2 and "stored" in dlg.table.item(0, 3).text()
    shot(dlg, "m5_participant_data")
    auto_delete(monkeypatch, typed={"participant": "wrong"})
    dlg._open("participant", "2")  # wrong code: nothing happens
    assert Layout(root).trial("2").exists()
    auto_delete(monkeypatch)
    dlg._open("participant", "2")
    assert not Layout(root).trial("2").exists() and "all data deleted" in dlg.table.item(1, 3).text()
    assert dlg.table.cellWidget(1, 4) is None  # no more buttons for a withdrawn participant
    assert load_session(root).trial("2").withdrawn_at

    # the File-menu entry: whole session, then the window forgets it
    def fake_data_exec(d):
        d._open("session")
        return 1

    monkeypatch.setattr(privacy_dialog.ParticipantDataDialog, "exec", fake_data_exec)
    assert win.data_action.isEnabled()
    win.data_action.trigger()
    assert not root.exists() and win.session is None and not win.data_action.isEnabled()
