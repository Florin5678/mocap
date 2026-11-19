"""Changing the output folder of a session later (0.1.0rc12): the session folder moves, nothing else changes."""

import json
import shutil
from pathlib import Path

import pytest

from mocapapp.cli.main import main
from mocapapp.core import workflow
from mocapapp.core.project import load_project, save_project
from mocapapp.core.session import load_session
from mocapapp.core.workflow import SetupError, move_session, plan_move
from tests.m3_fakes import make_session

INSTALLER = Path(__file__).resolve().parents[1] / "installer" / "windows"


def _session(tmp_path):
    root = tmp_path / "out" / "T1_session"
    return root, make_session(root)


def test_plan_refuses_what_cannot_work(tmp_path):
    root, session = _session(tmp_path)
    with pytest.raises(SetupError, match="already in"):
        plan_move(session, root, tmp_path / "out")
    with pytest.raises(SetupError, match="outside the session folder"):
        plan_move(session, root, root / "trials")
    with pytest.raises(SetupError, match="æ"):
        plan_move(session, root, tmp_path / "målinger")
    (tmp_path / "busy" / "T1_session").mkdir(parents=True)
    with pytest.raises(SetupError, match="already exists"):
        plan_move(session, root, tmp_path / "busy")
    make_session(tmp_path / "other" / "older_session")  # the fake sessions share pseudonyms
    with pytest.raises(SetupError, match="T1-ABCDEF1, T1-ABCDEF2 are already used"):
        plan_move(session, root, tmp_path / "other")
    assert load_session(root).participants[0].code == "T1-ABCDEF1"  # nothing changed


def test_move_on_the_same_drive_renames(tmp_path):
    root, session = _session(tmp_path)
    plan = plan_move(session, root, tmp_path / "new" / "place")  # created when missing
    assert not plan.copy and "same drive" in plan.describe()
    dest, warnings = move_session(plan)
    assert dest == (tmp_path / "new" / "place" / "T1_session").resolve() and warnings == []
    assert not root.exists() and load_session(dest).trials[0].code == "T1-ABCDEF1"
    old = str(root.resolve())
    for f in dest.rglob("*"):  # nothing the app wrote refers to the old place (also as escaped in JSON)
        if f.is_file() and f.suffix in (".json", ".toml"):
            text = f.read_text(encoding="utf-8")
            assert old not in text and json.dumps(old)[1:-1] not in text, f
    moved = load_session(dest)  # this fixture keeps the camera folders inside the session folder
    assert {c.source_dir for c in moved.cameras} == {str(dest / "raw")}


def test_move_to_another_drive_copies_checks_and_removes(tmp_path, monkeypatch):
    root, session = _session(tmp_path)
    files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    plan = plan_move(session, root, tmp_path / "usb")
    plan.copy, plan.size = True, 1  # as if the folder were on another drive
    assert "copied" in plan.describe()

    real_copy = shutil.copy2
    calls = []

    def broken(a, b, **k):  # the drive is pulled out half-way
        calls.append(a)
        if len(calls) == 3:
            raise OSError("device not ready")
        return real_copy(a, b, **k)

    monkeypatch.setattr(workflow.shutil, "copy2", broken)
    with pytest.raises(SetupError, match="Nothing was changed"):
        move_session(plan)
    assert not plan.dest.exists() and sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()) == files

    monkeypatch.setattr(workflow.shutil, "copy2", real_copy)
    messages = []
    dest, warnings = move_session(plan, progress=messages.append)
    assert warnings == [] and not root.exists() and messages[0].startswith("Copying")
    assert sorted(p.relative_to(dest) for p in dest.rglob("*") if p.is_file()) == files


def test_old_folder_left_behind_is_reported(tmp_path, monkeypatch):
    from mocapapp.core import deletion

    root, session = _session(tmp_path)
    plan = plan_move(session, root, tmp_path / "usb")
    plan.copy = True

    def locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(deletion, "_remove", locked)
    dest, warnings = move_session(plan)
    assert load_session(dest) and root.exists()
    assert "could not be removed" in warnings[0] and "participant data" in warnings[0]


def test_cli_session_move(tmp_path, capsys):
    root, _ = _session(tmp_path)
    assert main(["session", "move", "--session", str(root), "--to", str(tmp_path / "b"), "--dry-run"]) == 0
    assert root.exists() and "To: " in capsys.readouterr().out
    assert main(["session", "move", "--session", str(root), "--to", str(tmp_path / "b")]) == 0
    assert "Session moved to" in capsys.readouterr().out and (tmp_path / "b" / "T1_session" / "session.json").is_file()
    assert main(["session", "move", "--session", str(tmp_path / "b" / "T1_session"),
                 "--to", str(tmp_path / "b")]) == 1


def test_gui_moves_session_and_its_project_file(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from mocapapp.gui.common import recent_sessions
    from mocapapp.gui.main_window import MainWindow

    root, _ = _session(tmp_path)
    project = save_project(tmp_path / "study", root, "setup")
    win = MainWindow()
    qtbot.addWidget(win)
    win.open_path(project)
    assert win.pages[0].move_button.isVisibleTo(win.pages[0]) and win.move_action.isEnabled()

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "out"))
    shown = []
    monkeypatch.setattr("mocapapp.gui.main_window.error_box", lambda _w, title, text, *a: shown.append(text))
    win.move_session_dialog()  # the same folder: explained, nothing moved
    assert "already in" in shown[0] and win.root == root

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "lab"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.pages[0].move_button.click()
    qtbot.waitUntil(lambda: not win.busy and win.root != root, timeout=10000)
    dest = (tmp_path / "lab" / "T1_session").resolve()
    assert win.root == dest and not root.exists() and str(dest) in win.pages[0].info.text()
    assert load_project(project).session == dest  # the project file follows
    assert dest in [p.resolve() for p in recent_sessions()] and root not in recent_sessions()


def test_installer_registers_the_project_file_type():
    ps1 = (INSTALLER / "install.ps1").read_text(encoding="ascii")
    assert '$FileType = "mocapapp.project"' in ps1 and '"$classes\\.mocapproj" -Value $FileType' in ps1
    assert '-Value "`"$gui`" `"%1`""' in ps1  # double-click passes the file to mocapapp-gui
    un = (INSTALLER / "uninstall.ps1").read_text(encoding="ascii")
    assert '"$classes\\mocapapp.project"' in un and "'(default)' -eq \"mocapapp.project\"" in un
    assert "ChangesAssociations=yes" in (INSTALLER / "mocapapp.iss").read_text(encoding="utf-8")
