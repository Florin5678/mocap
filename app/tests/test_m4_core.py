"""M4 core: PDFs, texts, workflow helpers shared by CLI and GUI."""

from pathlib import Path

import cv2
import pypdf
import pytest

from mocapapp.cli.main import main
from mocapapp.core import pdfs
from mocapapp.core.calibration.intrinsics import BoardDetector, BoardSpec
from mocapapp.core.session import Layout, load_session
from mocapapp.core.texts import s, strings, tutorial
from mocapapp.core.workflow import (
    NewSession,
    SetupError,
    check_new_session,
    create_session,
    inputs_ready,
    pick_video,
    prepare_session,
    set_mapping,
)
from tests.conftest import make_video


@pytest.mark.parametrize("kind", ["charuco", "chess"])
def test_printed_board_is_detected_by_the_app(kind):
    spec = BoardSpec(kind, (5, 8), 25.0)
    img = cv2.copyMakeBorder(pdfs.board_image(spec), 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=255)
    found = BoardDetector(spec).detect(img)
    assert found is not None and len(found[0]) == spec.n_corners


def test_board_pdf(tmp_path):
    out = pdfs.board_pdf(tmp_path / "b.pdf", BoardSpec("charuco", (5, 8), 25.0))
    page = pypdf.PdfReader(out).pages[0]
    text = page.extract_text()
    assert "100 %" in text and "5 cm" in text and "DICT_4X4_50" in text
    assert float(page.mediabox.width) > float(page.mediabox.height)  # landscape A4
    with pytest.raises(pdfs.PdfError, match="does not fit on A3"):
        pdfs.board_pdf(tmp_path / "big.pdf", BoardSpec("chess", (9, 14), 60.0))


def test_consent_pdf_fields_and_placeholders(tmp_path):
    out = pdfs.consent_pdf(tmp_path / "c.pdf", "PA26-K7M2QX4")
    reader = pypdf.PdfReader(out)
    fields = reader.get_fields()
    assert fields["pseudonym"]["/V"] == "PA26-K7M2QX4"
    assert {"title", "purpose", "data_controller", "researcher_s", "contact", "benefits_or_compensation",
            "consent", "raw_storage", "name", "date"} <= set(fields)
    assert not {"height_m", "trial_code"} & set(fields)
    assert "mass" not in " ".join(fields).lower()
    text = " ".join(" ".join(p.extract_text().split()) for p in reader.pages)
    assert text.startswith("Participation consent form") and "pseudonym" in text and "withdraw" in text
    for gone in ("TEMPLATE", "Your rights", "[", "Template version", "(printed)", "Height"):
        assert gone not in text, gone
    assert "(Optional) I consent" in text and "Data already published can no longer be removed." in text


def test_cli_pdf(tmp_path, capsys):
    assert main(["pdf", "board", "--out", str(tmp_path / "b.pdf"), "--type", "chess"]) == 0
    assert main(["pdf", "consent", "--out", str(tmp_path / "c.pdf"), "--code", "X-2345672"]) == 0
    assert (tmp_path / "b.pdf").is_file() and (tmp_path / "c.pdf").is_file()


def test_texts():
    pages = tutorial()
    assert len(pages) == 8
    assert pages[6].downloads == ("board",) and pages[7].downloads == ("consent",)
    assert "[[download" not in pages[7].markdown
    assert "60 frames per second" in pages[1].markdown and "Z points up" in pages[4].markdown
    assert "sync cue" in s("sync_reminder") and set(strings()["steps"]) >= {"setup", "run", "results"}


def _cams(tmp_path, n=2):
    dirs = []
    for i in range(1, n + 1):
        d = tmp_path / "raw" / f"cam{i}"
        make_video(d / "intrinsic.mp4", seconds=0.3 + 0.05 * i)
        make_video(d / "extrinsic.mp4", seconds=0.6 + 0.05 * i)
        make_video(d / "run1.mp4", seconds=1.0 + 0.05 * i)
        dirs.append(d)
    return dirs


def test_new_session_checks(tmp_path):
    spec = NewSession(out_root=tmp_path / "s", n_trials=2, camera_dirs=[tmp_path / "nope"], heights=["1.8", "x"])
    levels = {(i.where, i.level) for i in check_new_session(spec)}
    assert ("cameras", "error") in levels and ("cam01", "error") in levels and ("trial 2", "error") in levels
    with pytest.raises(SetupError):
        create_session(spec)


def test_changed_sources_are_prepared_again(tmp_path):
    dirs = _cams(tmp_path)
    root, session = create_session(NewSession(out_root=tmp_path / "s", n_trials=1, camera_dirs=dirs,
                                              heights=["1.7"]))
    report, plan = prepare_session(session, root, progress=lambda m: None)
    assert report.ok and plan is not None and inputs_ready(session, root)
    layout = Layout(root)

    # another extrinsic video: the old copy and anything computed from it are dropped
    other = tmp_path / "elsewhere" / "ext.mp4"
    make_video(other, seconds=0.8)
    (layout.calibration_work).mkdir(exist_ok=True)
    (layout.calibration_work / "extrinsics_cam01.json").write_text("{}")
    layout.calib_file.write_text("x")
    pick_video(session, "cam01", "extrinsic", other, root)
    assert not (layout.inputs("cam01") / "extrinsic.mp4").exists()
    assert not (layout.calibration_work / "extrinsics_cam01.json").exists() and not layout.calib_file.exists()
    assert not inputs_ready(session, root)
    prepare_session(session, root, progress=lambda m: None)
    assert inputs_ready(session, root)
    assert session.camera("cam01").extrinsic.origin == "manual"

    # remapping a trial file replaces its copy, even if a stale copy is still there
    new_trial = dirs[1] / "take9.mp4"
    make_video(new_trial, seconds=0.9)
    set_mapping(session, {("cam01", "1"): dirs[0] / "run1.mp4", ("cam02", "1"): new_trial}, root)
    assert not (layout.inputs("cam02") / "trial_1.mp4").exists()
    prepare_session(session, root, progress=lambda m: None)
    stamp = (layout.inputs("cam02") / "trial_1.mp4.source").read_text()
    assert "take9.mp4" in stamp and load_session(root).trials[0].mapping_method == "manual"


def test_sample_session_layout(tmp_path, monkeypatch):
    """Without the 30 s conversion: the sample session is marked imported and assembled."""
    from mocapapp.core import sample

    def fake_convert(work: Path) -> Path:
        work.mkdir(parents=True, exist_ok=True)
        (work / "Calib.toml").write_text("[cam_1]\n")
        return work / "Calib.toml"

    monkeypatch.setattr(sample, "convert_demo_calibration", fake_convert)
    root, session = sample.make_sample_session(tmp_path, progress=lambda m: None)
    assert session.calibration_source == "imported" and session.trials[0].height_m == 1.72
    assert inputs_ready(session, root)
    from mocapapp.core.calibration.service import calibration_state

    assert set(calibration_state(session, root).values()) == {"ready"}
    assert (Layout(root).trial("1") / "calibration" / "Calib_scene.toml").is_file()
