"""GUI for M6: first-start checks, model download offer, diagnostics and the in-app user guide."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from mocapapp.core import models
from mocapapp.gui import firstrun
from mocapapp.gui.main_window import MainWindow, user_guide_path
from tests.test_gui import isolated_settings  # noqa: F401 (autouse fixture)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def boxes(monkeypatch):
    seen = []
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, n=name: seen.append((n, a[1], a[2])))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return seen


def test_first_start_offers_models_once(qtbot, tmp_path, monkeypatch, boxes):
    cache = tmp_path / "checkpoints"
    cache.mkdir()
    monkeypatch.setattr(models, "cache_dir", lambda: cache)
    monkeypatch.setattr(firstrun, "problems", lambda: [])
    downloads = []

    def fake_download(modes, progress=print):
        progress("Downloading ...")
        for m in models.models(modes):
            m.onnx.write_bytes(b"onnx")
        downloads.append(modes)

    monkeypatch.setattr(models, "download", fake_download)
    firstrun.first_start()  # blocks in its own event loop until the download finished
    assert downloads == [("balanced",)] and not models.missing()
    assert any("ready" in text for _, _, text in boxes)
    firstrun.first_start()  # asked once per version
    assert len(downloads) == 1


def test_first_start_lists_problems_with_fixes(qtbot, monkeypatch, boxes):
    from mocapapp.core.env import Check

    monkeypatch.setattr(firstrun, "problems", lambda: [Check("ffmpeg", False, "not found", "Install ffmpeg.")])
    monkeypatch.setattr(firstrun, "offer_models", lambda *a, **k: False)
    firstrun.first_start()
    kind, title, text = boxes[0]
    assert kind == "warning" and "ffmpeg: not found" in text and "-> Install ffmpeg." in text


def test_help_menu_diagnostics_and_guide(qtbot, monkeypatch, boxes):
    win = MainWindow()
    qtbot.addWidget(win)
    titles = [a.text() for a in win.menuBar().actions()[1].menu().actions()]
    assert {"Download pose &models ...", "Copy &diagnostics", "&User guide"} <= set(titles)
    win.copy_diagnostics()
    text = QApplication.clipboard().text()
    assert text.startswith("mocapapp ") and str(Path.home()) not in text
    guide = user_guide_path()
    assert guide is not None and "## 4. Participant data and deletion" in guide.read_text(encoding="utf-8")
    shown = []
    monkeypatch.setattr(QDialog, "exec", lambda d: shown.append(d) or 0)
    win.show_user_guide()
    assert shown and "Participant data and deletion" in shown[0].findChild(
        __import__("PySide6.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser).toPlainText()
