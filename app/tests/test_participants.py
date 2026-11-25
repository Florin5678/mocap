"""Participants with one or more trials each (0.1.0rc9): model, migration, setup, mapping, deletion, consent dialog."""

import json

import pytest

from mocapapp.core import deletion
from mocapapp.core.session import Layout, StudyInfo, load_session, save_session
from mocapapp.core.workflow import (
    NewSession,
    SetupError,
    add_participant,
    create_session,
    new_pseudonym,
    remove_participant,
    rename_participant,
    set_trial_participants,
)
from tests.m3_fakes import make_session


def _two_trials_one_person(root):
    """make_session's two trials, both recorded with participant 1."""
    session = make_session(root, n_trials=2)
    session.trials[1].participant = "1"
    session.participants = session.participants[:1]
    session.trials_per_participant = 2
    session.link()
    save_session(session, root)
    return session


def test_old_sessions_get_one_participant_per_trial(tmp_path):
    old = {"n_trials": 2, "cameras": [],
           "trials": [{"label": "1", "code": "A-2345672", "height_m": 1.8, "consent": {"attested": True},
                       "delete_raw": False, "raw_deleted_at": None},
                      {"label": "2", "code": "B-2345672", "withdrawn_at": "2026-11-01T10:00:00"}]}
    (tmp_path / "session.json").write_text(json.dumps(old), encoding="utf-8")
    s = load_session(tmp_path)
    assert [(p.label, p.code) for p in s.participants] == [("1", "A-2345672"), ("2", "B-2345672")]
    assert s.trials[0].height_m == 1.8 and s.trials[0].consent.attested and not s.trials[0].delete_raw
    assert s.trials[1].withdrawn_at and [t.label for t in s.active_trials] == ["1"]
    save_session(s, tmp_path)
    data = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert "code" not in data["trials"][0] and data["participants"][0]["height_m"] == 1.8
    assert load_session(tmp_path).trials[0].code == "A-2345672"


def test_trials_share_their_participant(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    cams = [tmp_path / f"cam{i}" for i in (1, 2)]
    for c in cams:
        c.mkdir()
    out, s = create_session(NewSession(out_root=root, n_participants=2, camera_dirs=cams, heights=["180", ""],
                                       trials_per_participant=2))
    assert s.n_trials == 4 and [t.participant for t in s.trials] == ["1", "1", "2", "2"]
    assert s.trials[0].code == s.trials[1].code != s.trials[2].code
    s.trials[1].height_m = 1.7  # a trial writes its participant's height
    assert s.trials[0].height_m == 1.7 and s.participant("1").height_m == 1.7

    person = add_participant(s, out, "1.65")
    assert person.label == "3" and [t.label for t in s.trials_of("3")] == ["5", "6"] and s.n_trials == 6
    assert person.code not in {p.code for p in s.participants[:2]}
    remove_participant(s, out, "2")
    assert [p.label for p in s.participants] == ["1", "3"] and [t.label for t in s.trials] == ["1", "2", "5", "6"]
    assert load_session(out).n_trials == 4

    set_trial_participants(s, {"2": "3"})
    assert s.trial("2").code == person.code
    with pytest.raises(SetupError, match="no participant 9"):
        set_trial_participants(s, {"1": "9"})
    Layout(out).trial("1").mkdir(parents=True)
    with pytest.raises(SetupError, match="already has videos"):
        remove_participant(s, out, "1")
    with pytest.raises(SetupError, match="at most 10 per session"):
        create_session(NewSession(out_root=root, n_participants=4, camera_dirs=cams, trials_per_participant=3))


def test_pseudonyms_can_be_changed_confirmed_after_consent_or_results(tmp_path, capsys):
    from mocapapp.cli.main import main
    from mocapapp.core.privacy import is_valid_code

    root = tmp_path / "out" / "s1"
    session = make_session(root, n_trials=2, ready=False)
    save_session(session, root)
    rename_participant(session, root, "1", " P01 ")
    assert load_session(root).participant("1").code == "P01" and session.trials[0].code == "P01"
    for bad, msg in (("Anna Berg", "no spaces"), ("-x", "starting with"), ("p01", "already used")):
        with pytest.raises(SetupError, match=msg):
            rename_participant(session, root, "2", bad)
    other = tmp_path / "out" / "s0"
    old = make_session(other, n_trials=1)
    old.participants[0].code = "TAKEN"
    save_session(old, other)
    with pytest.raises(SetupError, match="already used"):
        rename_participant(session, root, "2", "taken")
    person = new_pseudonym(session, root, "1")
    assert person.code != "P01" and is_valid_code(person.code)

    # after consent or results the change needs confirming (0.1.0rc16); the old pseudonym is remembered
    session.participant("2").consent.attested = True
    with pytest.raises(SetupError, match="needs confirming.*signed consent form"):
        new_pseudonym(session, root, "2")
    old = session.participant("2").code
    new_pseudonym(session, root, "2", confirmed=True)
    assert load_session(root).participant("2").former_codes == [old]
    session.participant("2").consent.attested = False

    # results and exports exist: the exported files, manifest.json and trials.csv follow the new pseudonym
    import json

    code = session.participant("2").code
    session.run_state.trial_status["2"] = "done"
    save_session(session, root)
    out = root / "export"
    out.mkdir()
    (out / f"{code}_trial2_trc.trc").write_text("x")
    (out / "P01_trial1_trc.trc").write_text("x")
    (out / "manifest.json").write_text(json.dumps({"files": [
        {"file": f"{code}_trial2_trc.trc", "trial": "2", "code": code},
        {"file": "P01_trial1_trc.trc", "trial": "1", "code": "P01"}], "trials": [{"trial": "2", "code": code}]}))
    (out / "trials.csv").write_text(f"trial,code,height_m\n1,P01,\n2,{code},1.80\n")
    with pytest.raises(SetupError, match="needs confirming.*Results exist"):
        rename_participant(session, root, "2", "P2B")
    rename_participant(session, root, "2", "P2B", confirmed=True)
    data = json.loads((out / "manifest.json").read_text())
    assert [f["file"] for f in data["files"]] == ["P2B_trial2_trc.trc", "P01_trial1_trc.trc"]
    assert data["trials"] == [{"trial": "2", "code": "P2B"}] and (out / "P2B_trial2_trc.trc").is_file()
    assert not (out / f"{code}_trial2_trc.trc").exists() and (out / "P01_trial1_trc.trc").is_file()
    assert (out / "trials.csv").read_text() == "trial,code,height_m\n1,P01,\n2,P2B,1.80\n"
    assert load_session(root).participant("2").former_codes == [old, code]
    capsys.readouterr()
    assert main(["session", "pseudonym", "--session", str(root), "--participant", "2", "--set", "P2C"]) == 1
    assert "--confirm" in capsys.readouterr().out
    session.run_state.trial_status.clear()
    save_session(session, root)

    assert main(["session", "pseudonym", "--session", str(root), "--participant", "2", "--set", "P02"]) == 0
    assert load_session(root).participant("2").code == "P02"
    assert main(["session", "pseudonym", "--session", str(root), "--participant", "2", "--random"]) == 0
    assert is_valid_code(load_session(root).participant("2").code)
    assert main(["session", "pseudonym", "--session", str(root), "--participant", "7", "--random"]) == 1
    assert "no participant 7" in capsys.readouterr().out


def test_withdrawal_deletes_every_trial_of_the_participant(tmp_path):
    session = _two_trials_one_person(tmp_path)
    plan = deletion.plan_participant(session, tmp_path, "2")
    assert plan.labels == ["1", "2"] and plan.confirm_text == "T1-ABCDEF1"
    assert "participant T1-ABCDEF1 (trials 1, 2)" in plan.describe()
    deletion.execute(plan, session, typed="T1-ABCDEF1")
    assert not Layout(tmp_path).trial("1").exists() and not Layout(tmp_path).trial("2").exists()
    s = load_session(tmp_path)
    assert s.participants[0].withdrawn_at and s.participants[0].height_m is None and not s.active_trials
    assert deletion.deletion_log(tmp_path)[-1]["trials"] == ["1", "2"]


def test_study_info_round_trip(tmp_path):
    session = make_session(tmp_path, n_trials=1)
    session.study_info = StudyInfo(study_name="Gait", researchers=[("A", "a@x"), ("B", "")])
    save_session(session, tmp_path)
    assert load_session(tmp_path).study_info.researchers == [("A", "a@x"), ("B", "")]


# ---------- GUI ----------

@pytest.fixture
def no_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def fail(box):
        raise AssertionError(f"message box: {box.text()}")

    monkeypatch.setattr(QMessageBox, "exec", fail)


def test_consent_dialog_keeps_the_study_details(qtbot, tmp_path, monkeypatch, no_boxes):
    import mocapapp.gui.greeting
    from mocapapp.gui.consent_dialog import ConsentInfoDialog
    from mocapapp.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    session = make_session(tmp_path, n_trials=2)
    win.set_session(tmp_path, session)
    saved = []
    monkeypatch.setattr(mocapapp.gui.greeting, "save_pdf", lambda *a, **k: saved.append(k))
    seen = []

    def fill(dlg):
        seen.append((dlg.pseudonym.text(), dlg.study_name.text(), dlg.researchers.rowCount()))
        if len(seen) == 1:
            dlg.study_name.setText("Gait 2026")
            dlg.add_researcher("B. Second", "b@x")
            dlg.researchers.item(0, 0).setText("A. First")
        return ConsentInfoDialog.DialogCode.Accepted

    monkeypatch.setattr(ConsentInfoDialog, "exec", fill)
    win.save_consent_pdf("T1-ABCDEF1")
    win.save_consent_pdf("T1-ABCDEF2")
    assert seen == [("T1-ABCDEF1", "", 1), ("T1-ABCDEF2", "Gait 2026", 2)]
    win.save_all_consent_pdfs()
    assert seen[-1][1:] == ("Gait 2026", 2) and saved[-1]["codes"] == ["T1-ABCDEF1", "T1-ABCDEF2"]
    assert saved[1]["code"] == "T1-ABCDEF2" and saved[1]["info"].study_name == "Gait 2026"
    assert load_session(tmp_path).study_info.researchers == [("A. First", ""), ("B. Second", "b@x")]

