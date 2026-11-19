"""One window, a linear wizard with a step bar and a non-destructive Back (PROJECT.md §7)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp import CITATION, __version__
from mocapapp.core.session import Session, load_session, save_session
from mocapapp.core.texts import s
from mocapapp.gui.common import (
    Background,
    error_box,
    forget_session,
    heading,
    recent_sessions,
    remember_session,
    settings,
)

TUTORIAL_PAGE = {"setup": 0, "videos": 3, "intrinsics": 3, "scene": 4, "extrinsics": 3, "run": 5, "results": 0,
                 "viewer": 0}


def user_guide_path() -> Path | None:
    """The guide ships inside the package (resources/) so installed copies have it too."""
    from importlib import resources

    path = Path(str(resources.files("mocapapp.resources") / "user-guide.md"))
    return path if path.is_file() else None


class Page(QWidget):
    """A wizard step. Pages call core functions only; `refresh` re-reads the session."""

    key = ""
    title = ""
    optional = False  # a step to look at, never where a session resumes
    completeChanged = Signal()

    def __init__(self, win: MainWindow):
        super().__init__()
        self.win = win

    @property
    def session(self) -> Session | None:
        return self.win.session

    @property
    def root(self) -> Path | None:
        return self.win.root

    def refresh(self) -> None:
        pass

    def is_complete(self) -> bool:
        return True

    def skipped(self) -> bool:
        """True when this step does not apply to the session (e.g. calibration of the sample data)."""
        return False

    def gates_next(self) -> bool:
        """False when the next steps can be opened before this one is complete (export is not needed to view)."""
        return True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from mocapapp.gui.pages.calibration import ExtrinsicsPage, IntrinsicsPage, ScenePage
        from mocapapp.gui.pages.results import ResultsPage
        from mocapapp.gui.pages.run import RunPage
        from mocapapp.gui.pages.setup import SetupPage
        from mocapapp.gui.pages.videos import VideosPage
        from mocapapp.gui.pages.viewer import ViewerPage

        self.root: Path | None = None
        self.session: Session | None = None
        self.project: Path | None = None  # the .mocapproj file the session was opened from or saved to
        self.busy = False
        self.setWindowTitle(s("app_name"))
        self.resize(1180, 780)

        self.pages: list[Page] = [SetupPage(self), VideosPage(self), IntrinsicsPage(self), ScenePage(self),
                                  ExtrinsicsPage(self), RunPage(self), ResultsPage(self), ViewerPage(self)]
        self.steps = QListWidget()
        self.steps.setFixedWidth(230)
        self.steps.currentRowChanged.connect(self._step_clicked)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.steps.addItem(QListWidgetItem(page.title))
            self.stack.addWidget(page)
            page.completeChanged.connect(self.update_nav)

        self.purpose = QLabel()
        self.purpose.setWordWrap(True)
        self.purpose.setTextFormat(Qt.TextFormat.RichText)
        self.purpose.linkActivated.connect(lambda _: self.show_tutorial(TUTORIAL_PAGE[self.current_page().key]))
        self.back_button = QPushButton("< Back")
        self.next_button = QPushButton("Next >")
        self.back_button.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next_button.clicked.connect(self._next)

        right = QVBoxLayout()
        self.page_title = heading("", 16)
        right.addWidget(self.page_title)
        right.addWidget(self.purpose)
        right.addWidget(self.stack, 1)
        nav = QHBoxLayout()
        nav.addWidget(self.back_button)
        nav.addStretch()
        self.session_label = QLabel()
        self.session_label.setStyleSheet("color: palette(placeholder-text)")
        nav.addWidget(self.session_label)
        nav.addStretch()
        nav.addWidget(self.next_button)
        right.addLayout(nav)
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.steps)
        layout.addLayout(right, 1)
        self.setCentralWidget(central)
        self._menus()
        self.go(0)

    # ---------- menus ----------

    def _menus(self) -> None:
        file = self.menuBar().addMenu("&File")
        self._action(file, "&New session", self.new_session, QKeySequence.StandardKey.New)
        self._action(file, "&Open project ...", self.open_project_dialog, QKeySequence.StandardKey.Open)
        self._action(file, "Open session &folder ...", self.open_session_dialog)
        self.recent_menu = file.addMenu("Recent sessions")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.save_action = self._action(file, "&Save project", self.save_project, QKeySequence.StandardKey.Save)
        self.save_as_action = self._action(file, "Save project &as ...", self.save_project_as,
                                           QKeySequence.StandardKey.SaveAs)
        self._action(file, "Try with &sample data ...", self.make_sample)
        file.addSeparator()
        self.data_action = self._action(file, "&Participant data and deletion ...", self.show_participant_data)
        self.move_action = self._action(file, "&Move session to another output folder ...", self.move_session_dialog)
        file.addSeparator()
        self._action(file, "&Quit", self.close, QKeySequence.StandardKey.Quit)
        help_menu = self.menuBar().addMenu("&Help")
        self._action(help_menu, "&How it works", lambda: self.show_tutorial(0), QKeySequence.StandardKey.HelpContents)
        self._action(help_menu, "Calibration &board PDF ...", self.save_board_pdf)
        self._action(help_menu, "Participation &consent form PDF ...", lambda: self.save_consent_pdf(""))
        self._action(help_menu, "&Environment check", self.show_doctor)
        self._action(help_menu, "Download pose &models ...", self.download_models)
        self._action(help_menu, "Copy &diagnostics", self.copy_diagnostics)
        self._action(help_menu, "&User guide", self.show_user_guide)
        self._action(help_menu, "&About", self.show_about)

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        paths = recent_sessions()
        if not paths:
            self.recent_menu.addAction("(none)").setEnabled(False)
        from mocapapp.core.deletion import session_reminders

        for p in paths:
            notes = session_reminders(p)  # §8.4 item 6: footage still stored
            action = self.recent_menu.addAction(str(p) + ("   - raw footage to delete" if notes else ""),
                                                lambda p=p: self.open_session(p))
            action.setToolTip(" ".join(notes))
        self.recent_menu.setToolTipsVisible(True)

    # ---------- session ----------

    def set_session(self, root: Path | None, session: Session | None) -> None:
        self.root, self.session = root, session
        if root is not None:
            remember_session(root)
            settings().setValue("output_root", str(Path(root).parent))
        if root is None:
            self.project = None
        self.data_action.setEnabled(session is not None)
        self.move_action.setEnabled(session is not None)
        self.save_action.setEnabled(session is not None)
        self.save_as_action.setEnabled(session is not None)
        self._show_name()
        for page in self.pages:
            page.refresh()
        self.update_nav()

    def _show_name(self) -> None:
        root = self.root
        name = self.project.stem if self.project else root.name if root else ""
        self.session_label.setText((f"Project: {name}" if self.project else f"Session: {name}") if root else "")
        self.setWindowTitle(f"{s('app_name')} - {name}" if root else s("app_name"))

    def save(self) -> None:
        if self.session is not None and self.root is not None:
            save_session(self.session, self.root)

    # ---------- project files ----------

    def save_project(self) -> Path | None:
        """Save the session and the project file (session folder + current step). Asks where the first time."""
        if self.session is None or self.busy:
            return None
        if self.project is None:
            return self.save_project_as()
        return self._write_project(self.project)

    def save_project_as(self) -> Path | None:
        from mocapapp.core.project import SUFFIX

        if self.session is None or self.busy:
            return None
        start = self.project or Path(self.root).parent / f"{Path(self.root).name}{SUFFIX}"
        name, _ = QFileDialog.getSaveFileName(self, "Save project", str(start), f"mocapapp project (*{SUFFIX})")
        return self._write_project(Path(name)) if name else None

    def _write_project(self, path: Path) -> Path | None:
        from mocapapp.core.project import ProjectError, save_project

        self.save()
        try:
            self.project = save_project(path, self.root, self.current_page().key)
        except (OSError, ProjectError) as exc:
            error_box(self, "Project not saved", str(exc))
            return None
        settings().setValue("last_project", str(self.project))
        self._show_name()
        self.statusBar().showMessage(f"Project saved: {self.project}", 6000)
        return self.project

    def open_project_dialog(self) -> None:
        from mocapapp.core.project import SUFFIX

        last = settings().value("last_project", "")
        start = str(Path(last).parent) if last else str(Path.home())
        name, _ = QFileDialog.getOpenFileName(self, "Open project", start,
                                              f"mocapapp project (*{SUFFIX});;Session file (session.json)")
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path) -> None:
        """A project file, a session.json or a session folder."""
        from mocapapp.core.project import SUFFIX

        path = Path(path)
        if path.suffix.lower() == SUFFIX:
            self.open_project(path)
        else:
            self.open_session(path.parent if path.name == "session.json" else path)

    def open_project(self, path: Path) -> None:
        from mocapapp.core.project import ProjectError, load_project

        if self.busy:
            return
        try:
            project = load_project(path)
        except ProjectError as exc:
            error_box(self, "Cannot open project", str(exc))
            return
        if not self.open_session(project.session):
            return
        self.project = project.path
        settings().setValue("last_project", str(project.path))
        self._show_name()
        keys = [p.key for p in self.pages]
        index = keys.index(project.step) if project.step in keys else 0
        if self.reachable(index):  # otherwise stay at the first unfinished step (files changed since saving)
            self.go(index)

    def new_session(self) -> None:
        if self.busy:
            return
        self.set_session(None, None)
        self.go(0)

    def open_session_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open a session folder", str(Path.home()))
        if folder:
            self.open_session(Path(folder))

    def open_session(self, root: Path) -> bool:
        if self.busy:
            return False
        try:
            session = load_session(root)
        except (OSError, ValueError) as exc:
            error_box(self, "Cannot open session", f"{root} is not a readable session folder: {exc}")
            return False
        self.project = None
        self.set_session(root, session)
        self.go(self.first_incomplete())
        return True

    def make_sample(self) -> None:
        from mocapapp.core.sample import make_sample_session

        folder = QFileDialog.getExistingDirectory(
            self, "Where should the sample session be created? (ASCII-only path)", str(Path.home()))
        if not folder:
            return
        dlg = QProgressDialog("Preparing the sample session ...", None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()

        def done(result):
            dlg.close()
            self.project = None
            self.set_session(*result)
            self.go(self.first_incomplete())

        def failed(msg, details):
            dlg.close()
            error_box(self, "Sample session not created", msg, details)

        Background(self, make_sample_session, Path(folder), on_done=done, on_error=failed,
                   on_progress=dlg.setLabelText, progress=True)

    def move_session_dialog(self) -> None:
        """Move the session folder into another output folder (Setup step button and File menu)."""
        from mocapapp.core.workflow import SetupError, plan_move

        if self.session is None or self.busy:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the new output folder (the session folder moves into it)", str(Path(self.root).parent))
        if not folder:
            return
        self.save()
        try:
            plan = plan_move(self.session, self.root, Path(folder))
        except SetupError as exc:
            error_box(self, "Session not moved", str(exc))
            return
        answer = QMessageBox.question(self, "Move the session?", plan.describe())
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.move_session(plan)

    def move_session(self, plan) -> None:
        from mocapapp.core.workflow import move_session

        old = self.root
        dlg = QProgressDialog("Moving the session ...", None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        self.set_busy(True)

        def done(result):
            dest, warnings = result
            dlg.close()
            self.set_busy(False)
            forget_session(old)
            self.set_session(dest, self.session)  # remembers the new place and output folder
            if self.project is not None:
                self._write_project(self.project)  # the project file follows the session
            self.statusBar().showMessage(f"Session moved to {dest}", 8000)
            if warnings:
                QMessageBox.warning(self, "Old folder not removed", "\n\n".join(warnings))

        def failed(msg, details):
            dlg.close()
            self.set_busy(False)
            error_box(self, "Session not moved", msg, details)

        self.mover = Background(self, move_session, plan, on_done=done, on_error=failed,
                                on_progress=dlg.setLabelText, progress=True)

    def show_participant_data(self) -> None:
        """Consent, retention and deletion for every trial; reachable at any time (§8.4)."""
        from mocapapp.gui.privacy_dialog import ParticipantDataDialog

        if self.session is None or self.busy:
            return
        root = self.root
        dlg = ParticipantDataDialog(self, self.session, root)
        dlg.exec()
        if dlg.session_deleted:
            forget_session(root)
            self.set_session(None, None)
            self.go(0)
            return
        self.set_session(root, self.session)  # pages show deleted/withdrawn trials

    # ---------- navigation ----------

    def current_page(self) -> Page:
        return self.pages[self.stack.currentIndex()]

    def reachable(self, index: int) -> bool:
        if index == 0:
            return True
        if self.session is None:
            return False
        return all(p.is_complete() or p.skipped() or not p.gates_next() for p in self.pages[:index])

    def first_incomplete(self) -> int:
        """Where an opened session resumes. A finished session opens on Results (export, deletion reminders),
        not on the optional Viewer after it."""
        for i, page in enumerate(self.pages):
            if not (page.is_complete() or page.skipped()):
                return i
        return max(i for i, p in enumerate(self.pages) if not p.optional)

    def go(self, index: int) -> None:
        if not 0 <= index < len(self.pages) or self.busy:
            return
        # skip steps that do not apply, in the direction of travel
        step = 1 if index >= self.stack.currentIndex() else -1
        while 0 < index < len(self.pages) - 1 and self.pages[index].skipped():
            index += step
        if not self.reachable(index):
            return
        self.stack.setCurrentIndex(index)
        page = self.pages[index]
        page.refresh()
        self.page_title.setText(page.title)
        self.purpose.setText(f"{s('steps.' + page.key)} <a href='why'>Why?</a>")
        self.steps.blockSignals(True)
        self.steps.setCurrentRow(index)
        self.steps.blockSignals(False)
        self.update_nav()

    def _step_clicked(self, row: int) -> None:
        if self.reachable(row) and not self.busy:
            self.go(row)
        else:
            self.steps.blockSignals(True)
            self.steps.setCurrentRow(self.stack.currentIndex())
            self.steps.blockSignals(False)

    def update_nav(self) -> None:
        i = self.stack.currentIndex()
        for k, page in enumerate(self.pages):
            item = self.steps.item(k)
            mark = "✓ " if self.session is not None and page.is_complete() and not page.skipped() else (
                "- " if self.session is not None and page.skipped() else "   ")
            item.setText(f"{mark}{k + 1}. {page.title}" + (" (not needed)" if self.session and page.skipped()
                                                             else ""))
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            item.setFlags(flags if self.reachable(k) else Qt.ItemFlag.NoItemFlags)
        self.back_button.setEnabled(i > 0 and not self.busy)
        last = i == len(self.pages) - 1
        self.next_button.setText("Finished!" if last else "Next >")
        self.next_button.setToolTip("Close this session and start a new one" if last else "")
        self.next_button.setEnabled(not self.busy and self.session is not None
                                    and (self.pages[i].is_complete() or self.pages[i].skipped()
                                         or not self.pages[i].gates_next()))

    def _next(self) -> None:
        if self.stack.currentIndex() < len(self.pages) - 1:
            self.go(self.stack.currentIndex() + 1)
        else:
            self.finish()

    def finish(self) -> None:
        """Results exported: close the session and go back to step 1. Reminds about stored raw footage (§8.4)."""
        from mocapapp.core.deletion import reminders

        if self.session is None or self.busy:
            return
        self.save()
        notes = reminders(self.session)
        if notes:
            QMessageBox.information(self, "Session finished", "\n\n".join(notes))
        self.new_session()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.steps.setEnabled(not busy)
        self.menuBar().setEnabled(not busy)
        self.update_nav()

    # ---------- help ----------

    def show_tutorial(self, page: int = 0) -> None:
        from mocapapp.gui.greeting import TutorialDialog

        dlg = TutorialDialog(self, standalone=True)
        dlg.show_page(page)
        dlg.exec()

    def board_spec(self):
        from mocapapp.core.calibration.intrinsics import BoardSpec
        from mocapapp.core.session import Settings

        return BoardSpec.from_settings(self.session.settings if self.session else Settings())

    def save_board_pdf(self) -> None:
        from mocapapp.gui.greeting import save_pdf

        save_pdf(self, "board", board_spec=self.board_spec())

    def save_consent_pdf(self, code: str) -> None:
        """For a participant: ask the study details first (kept for the session's next forms). From the Help
        menu (no pseudonym): a form with the open session's details, or a blank one."""
        from mocapapp.gui.greeting import save_pdf

        info = self.session.study_info if self.session is not None else None
        if code and self.session is not None:
            from mocapapp.gui.consent_dialog import ConsentInfoDialog

            dlg = ConsentInfoDialog(self, info, code)
            if dlg.exec() != ConsentInfoDialog.DialogCode.Accepted:
                return
            self.session.study_info = info = dlg.info()
            self.save()
        save_pdf(self, "consent", code=code, info=info)

    def save_all_consent_pdfs(self) -> None:
        """Every participant's form in one PDF (one form per participant, each on new pages), for printing."""
        from mocapapp.gui.consent_dialog import ConsentInfoDialog
        from mocapapp.gui.greeting import save_pdf

        if self.session is None:
            return
        codes = [p.code for p in self.session.active_participants]
        dlg = ConsentInfoDialog(self, self.session.study_info, f"each participant's own ({len(codes)} forms)")
        if dlg.exec() != ConsentInfoDialog.DialogCode.Accepted:
            return
        self.session.study_info = dlg.info()
        self.save()
        save_pdf(self, "consent", info=self.session.study_info, codes=codes)

    def show_doctor(self) -> None:
        from mocapapp.core.env import run_checks

        checks = run_checks(Path(self.root).parent if self.root else None)
        lines = [f"{'OK' if c.ok else 'PROBLEM'} - {c.name}: {c.detail}" + (f"\n      -> {c.fix}" if not c.ok and
                                                                               c.fix else "") for c in checks]
        QMessageBox.information(self, "Environment check", "\n".join(lines))

    def download_models(self) -> None:
        from mocapapp.gui.firstrun import offer_models

        offer_models(self, ask=False)

    def copy_diagnostics(self) -> None:
        from mocapapp.gui.firstrun import copy_diagnostics

        copy_diagnostics(self)

    def show_user_guide(self) -> None:
        from PySide6.QtWidgets import QDialog, QTextBrowser

        guide = user_guide_path()
        if guide is None:
            error_box(self, "User guide not found", "The user guide is not installed with this copy of the app. "
                      "It is app/src/mocapapp/resources/user-guide.md in the project on GitHub.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("User guide")
        dlg.resize(900, 720)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(guide.read_text(encoding="utf-8"))
        QVBoxLayout(dlg).addWidget(view)
        dlg.exec()

    def show_about(self) -> None:
        QMessageBox.about(self, "About", f"<b>{s('app_name')}</b> {__version__}<br>{s('research_only')}<br><br>"
                          f"Built on Pose2Sim (BSD-3-Clause) and OpenSim.<br><br>Please cite:<br>{CITATION}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        if self.busy and not QMessageBox.question(
                self, "Work in progress", "Something is still running. Quit anyway? Finished stages are kept and "
                "can be resumed.") == QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self.save()
        if self.project is not None and self.session is not None:
            from mocapapp.core.project import ProjectError, save_project

            try:  # the project reopens where the researcher left off
                save_project(self.project, self.root, self.current_page().key)
            except (OSError, ProjectError):
                pass
        event.accept()
