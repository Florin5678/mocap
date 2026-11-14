"""Greeting dialog (§6.0, D15) and the step-by-step tutorial (§6.2)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core.texts import s, tutorial
from mocapapp.gui.common import error_box, heading, open_path


class GreetingDialog(QDialog):
    """Exactly two buttons. Result: 'tutorial' or 'start' in self.choice (None if closed)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.choice: str | None = None
        self.setWindowTitle(s("app_name"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(heading(s("greeting_title"), 17))
        text = QLabel(s("greeting_text"))
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addSpacing(8)
        self.tutorial_button = QPushButton(f"{s('greeting_tutorial')}\n({s('greeting_tutorial_note')})")
        self.start_button = QPushButton(s("greeting_start"))
        for b in (self.tutorial_button, self.start_button):
            b.setMinimumHeight(56)
            layout.addWidget(b)
        self.tutorial_button.setDefault(True)
        self.tutorial_button.clicked.connect(lambda: self._choose("tutorial"))
        self.start_button.clicked.connect(lambda: self._choose("start"))
        small = QLabel(s("research_only"))
        small.setStyleSheet("color: palette(placeholder-text)")
        layout.addWidget(small, alignment=Qt.AlignmentFlag.AlignRight)
        self.setMinimumWidth(420)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


def save_pdf(parent: QWidget, kind: str, code: str = "", board_spec=None) -> Path | None:
    """Ask where to save the board or consent PDF, write it, and open it."""
    from mocapapp.core import pdfs

    consent = f"participation_consent_{code}.pdf" if code else "participation_consent_form.pdf"
    default = "calibration_board.pdf" if kind == "board" else consent
    name, _ = QFileDialog.getSaveFileName(parent, "Save PDF", str(Path.home() / default), "PDF (*.pdf)")
    if not name:
        return None
    try:
        if kind == "board":
            from mocapapp.core.calibration.intrinsics import BoardSpec
            from mocapapp.core.session import Settings

            out = pdfs.board_pdf(Path(name), board_spec or BoardSpec.from_settings(Settings()))
        else:
            out = pdfs.consent_pdf(Path(name), code)
    except (OSError, pdfs.PdfError) as exc:
        error_box(parent, "PDF not saved", f"The PDF could not be written: {exc}")
        return None
    open_path(out)
    return out


class TutorialDialog(QDialog):
    """Pages with Previous/Next; the last page offers Back (to the greeting) and Start session.
    Result in self.choice: 'back' | 'start' | None. With `standalone=True` (Help menu) the last page shows Close."""

    def __init__(self, parent: QWidget | None = None, standalone: bool = False):
        super().__init__(parent)
        self.choice: str | None = None
        self.pages = tutorial()
        self.index = 0
        self.standalone = standalone
        self.setWindowTitle("How it works")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        self.step = QLabel()
        self.step.setStyleSheet("color: palette(placeholder-text)")
        self.title = heading("", 16)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        layout.addWidget(self.step)
        layout.addWidget(self.title)
        layout.addWidget(self.body, 1)
        self.downloads = QHBoxLayout()
        self.board_button = QPushButton("Save the board PDF ...")
        self.consent_button = QPushButton("Save the participation consent form PDF ...")
        self.board_button.clicked.connect(lambda: save_pdf(self, "board"))
        self.consent_button.clicked.connect(lambda: save_pdf(self, "consent"))
        self.downloads.addWidget(self.board_button)
        self.downloads.addWidget(self.consent_button)
        self.downloads.addStretch()
        layout.addLayout(self.downloads)
        nav = QHBoxLayout()
        self.prev_button = QPushButton("< Previous")
        self.next_button = QPushButton("Next >")
        self.back_button = QPushButton("Back")
        self.start_button = QPushButton("Close" if standalone else "Start session")
        self.prev_button.clicked.connect(lambda: self.show_page(self.index - 1))
        self.next_button.clicked.connect(lambda: self.show_page(self.index + 1))
        self.back_button.clicked.connect(lambda: self._finish("back"))
        self.start_button.clicked.connect(lambda: self._finish("close" if standalone else "start"))
        nav.addWidget(self.prev_button)
        nav.addStretch()
        for b in (self.back_button, self.next_button, self.start_button):
            nav.addWidget(b)
        layout.addLayout(nav)
        self.show_page(0)

    def show_page(self, index: int) -> None:
        self.index = max(0, min(index, len(self.pages) - 1))
        page = self.pages[self.index]
        self.step.setText(f"Page {self.index + 1} of {len(self.pages)}")
        self.title.setText(page.title)
        self.body.setMarkdown(page.markdown)
        self.board_button.setVisible("board" in page.downloads)
        self.consent_button.setVisible("consent" in page.downloads)
        last = self.index == len(self.pages) - 1
        self.prev_button.setEnabled(self.index > 0)
        self.next_button.setVisible(not last)
        self.back_button.setVisible(last and not self.standalone)
        self.start_button.setVisible(last)
        (self.start_button if last else self.next_button).setDefault(True)

    def _finish(self, choice: str) -> None:
        self.choice = choice
        self.accept()
