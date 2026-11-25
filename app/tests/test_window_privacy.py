"""Consent and deletion in the window (§8, P4): the Participants section's consent drop-down, the participant-data
dialog, deletion previews and the Results section's deletion action."""

import pytest
from PySide6.QtWidgets import QMessageBox

from mocapapp.core import readiness, workflow
from mocapapp.core.session import Layout, load_session
from mocapapp.gui import privacy_dialog
from mocapapp.gui.main_window import MainWindow
from mocapapp.gui.sections import participants as participants_mod
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
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(root, session)
    win.go("results")
    results = win.section("results").results_page
    assert win.current_page().key == "results" and results.delete_now.isEnabled()
    assert "still stored" in results.deletion.text() and "kept with consent" in results.deletion.text()
    shown = auto_delete(monkeypatch)
    results.delete_now.click()
    assert [d.label for d in shown] == ["1"] and shown[0].after_run  # only the trial marked 'delete'
    assert "SD cards" in boxes[-1]
    assert not Layout(root).trial_input("1", "cam01").exists()
    assert Layout(root).trial_input("2", "cam01").exists()
    assert "deleted on" in results.deletion.text() and not results.delete_now.isEnabled()
    # R20: only that trial is frozen; it stays done, the other trial is untouched
    assert [readiness.trial_status(session, root, t) for t in ("1", "2")] == ["done", "done"]
    assert not session.trial("1").raw_present and session.trial("2").raw_present
    win.go("participants")
    people = win.section("participants").table
    assert people.item(1, _col(people, "Raw footage")).text() == "kept (storage consent)"


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


def _col(table, title: str) -> int:
    return [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())].index(title)


def test_participants_section_consent_drop_down(qtbot, tmp_path, monkeypatch, boxes):
    """R11: one consent drop-down; raw footage follows it (§6.2), or Session settings for everyone (R13)."""
    root, ses = workflow.create_project(tmp_path / "projects", "T1")
    win = MainWindow()
    qtbot.addWidget(win)
    win.set_session(root, ses)
    win.go("participants")
    sec = win.section("participants")
    assert sec.table.rowCount() == 0 and "No participants yet" in sec.issues.toPlainText()
    sec.buttons["add"].click()
    sec.buttons["add"].click()
    qtbot.waitUntil(lambda: sec.table.rowCount() == 2)
    assert [len(ses.trials_of(p.label)) for p in ses.participants] == [1, 1]  # one trial each (Session settings)
    assert win.badges()["participants"] == "2 to do"
    consent, footage = _col(sec.table, "Consent"), _col(sec.table, "Raw footage")

    def choose(row: int, state: str):
        box = sec.table.cellWidget(row, consent)
        box.setCurrentIndex(box.findData(state))
        qtbot.waitUntil(lambda: sec.table.cellWidget(row, consent) is not box)  # rebuilt after saving

    assert sec.table.item(0, footage).text() == "waiting for consent"
    choose(0, "keep")
    one, two = ses.participants
    assert one.consent.attested and one.consent.raw_footage_storage and not one.delete_raw
    assert sec.table.item(0, footage).text() == "kept (storage consent)"
    choose(1, "delete")
    assert two.consent.attested and not two.consent.raw_footage_storage and two.delete_raw
    assert sec.table.item(1, footage).text() == "deleted after export"
    for row in (0, 1):
        height = sec.table.cellWidget(row, _col(sec.table, "Height"))
        height.setText("175 cm")
        height.editingFinished.emit()
        qtbot.waitUntil(lambda r=row, h=height: sec.table.cellWidget(r, _col(sec.table, "Height")) is not h)
    assert one.height_m == two.height_m == 1.75
    assert win.badges()["participants"] == "✓"
    assert "Every participant has a height" in sec.issues.toPlainText()
    saved = load_session(root)
    assert [workflow.consent_state(p) for p in saved.participants] == ["keep", "delete"]

    # R13: raw footage deleted for everyone, even with storage consent; off again, consent decides
    workflow.set_delete_raw_all(ses, root, True)
    sec.refresh()
    assert one.delete_raw and sec.table.item(0, footage).text() == "deleted after export (all participants)"
    choose(0, "delete")
    choose(0, "keep")  # storage consent given again: still deleted, as set for everyone
    assert one.delete_raw and one.consent.raw_footage_storage
    workflow.set_delete_raw_all(ses, root, False)
    assert not one.delete_raw and two.delete_raw
    sec.refresh()

    # back to 'not signed yet': consent no longer confirmed, the footage would be deleted
    choose(1, "not-signed")
    assert not two.consent.attested and two.consent.attested_at is None and two.delete_raw
    assert win.badges()["participants"] == "1 to do"

    # a new pseudonym after consent: the researcher confirms first (the signed form carries the old one)
    asked = []
    monkeypatch.setattr(participants_mod, "confirm", lambda *a: asked.append(a[2]) or True)
    old = one.code
    code = sec.table.cellWidget(0, _col(sec.table, "Pseudonym"))
    code.setText("T1-NEWNAME")
    code.editingFinished.emit()
    qtbot.waitUntil(lambda: one.code == "T1-NEWNAME")
    assert "consent form carries" in asked[0] and one.former_codes == [old]

    # a participant without videos or results can be removed, with their trial
    sec.table.selectRow(1)
    sec.buttons["remove"].click()
    qtbot.waitUntil(lambda: sec.table.rowCount() == 1)
    assert [p.label for p in ses.participants] == ["1"] and [t.participant for t in ses.trials] == ["1"]

    # withdrawn: nothing can change any more
    one.withdrawn_at = "2026-11-25T10:00:00"
    sec.refresh()
    assert not sec.table.cellWidget(0, consent).isEnabled()
    assert sec.table.item(0, footage).text() == "all data deleted"
    with pytest.raises(workflow.SetupError, match="withdrew"):
        workflow.set_consent(ses, root, "1", "keep")
