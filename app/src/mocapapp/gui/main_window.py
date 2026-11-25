"""One window, five sections in a sidebar: Session, Participants, Cameras, Layouts, Results (PROJECT.md §16.3).

Sections open in any order; the sidebar shows what each still needs. Sections call core functions only."""

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
    keep_screen_on,
    last_output_root,
    recent_sessions,
    remember_session,
    settings,
)

TUTORIAL_PAGE = {"session": 0, "participants": 0, "cameras": 3, "layouts": 4, "results": 0}
# the step a 0.1.x project file was saved at -> the section that holds it now
OLD_STEPS = {"setup": "session", "videos": "session", "run": "session", "intrinsics": "cameras",
             "extrinsics": "layouts", "scene": "layouts", "viewer": "results"}


def user_guide_path() -> Path | None:
    """The guide ships inside the package (resources/) so installed copies have it too."""
    from importlib import resources

    path = Path(str(resources.files("mocapapp.resources") / "user-guide.md"))
    return path if path.is_file() else None


class Section(QWidget):
    """A section of the window. `refresh` re-reads the project; `completeChanged` recounts the sidebar badges."""

    key = ""
    title = ""
    purpose = ""
    optional = False
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

    # the 0.1.x pages still shown inside the Results section ask these
    def is_complete(self) -> bool:
        return True

    def step_ticked(self) -> bool:
        return self.is_complete() and not self.skipped()

    def skipped(self) -> bool:
        return False

    def gates_next(self) -> bool:
        return False


Page = Section  # the 0.1.x page modules import this name


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from mocapapp.gui.power import KeepAwake
        from mocapapp.gui.sections.cameras import CamerasSection
        from mocapapp.gui.sections.layouts import LayoutsSection
        from mocapapp.gui.sections.participants import ParticipantsSection
        from mocapapp.gui.sections.results import ResultsSection
        from mocapapp.gui.sections.session import SessionSection

        self.root: Path | None = None
        self.session: Session | None = None
        self.project: Path | None = None  # the .mocapproj file the project was opened from or saved to
        self.busy = False
        self.keep_awake = KeepAwake()  # Help > Settings: screen on while busy
        self.setWindowTitle(s("app_name"))
        self.resize(1180, 780)

        self.sections: list[Section] = [SessionSection(self), ParticipantsSection(self), CamerasSection(self),
                                        LayoutsSection(self), ResultsSection(self)]
        self.pages = self.sections
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(210)
        self.sidebar.setSpacing(3)
        self.sidebar.currentRowChanged.connect(self._sidebar_clicked)
        self.stack = QStackedWidget()
        for section in self.sections:
            self.sidebar.addItem(QListWidgetItem(section.title))
            self.stack.addWidget(section)
            section.completeChanged.connect(self.update_nav)

        self.purpose = QLabel()
        self.purpose.setWordWrap(True)
        self.purpose.setTextFormat(Qt.TextFormat.RichText)
        self.purpose.linkActivated.connect(lambda _: self.show_tutorial(TUTORIAL_PAGE[self.current_page().key]))
        right = QVBoxLayout()
        self.page_title = heading("", 16)
        right.addWidget(self.page_title)
        right.addWidget(self.purpose)
        right.addWidget(self.stack, 1)
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.sidebar)
        layout.addLayout(right, 1)
        self.setCentralWidget(central)
        self.session_label = QLabel()
        self.session_label.setStyleSheet("color: palette(placeholder-text)")
        self.statusBar().addPermanentWidget(self.session_label)
        self._menus()
        self.set_session(None, None)

    # ---------- menus ----------

    def _menus(self) -> None:
        file = self.menuBar().addMenu("&File")
        self._action(file, "&New project", self.new_session, QKeySequence.StandardKey.New)
        self._action(file, "&Open project ...", self.open_project_dialog, QKeySequence.StandardKey.Open)
        self._action(file, "Open project &folder ...", self.open_session_dialog)
        self.recent_menu = file.addMenu("Recent projects")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.save_action = self._action(file, "&Save project", self.save_project, QKeySequence.StandardKey.Save)
        self.save_as_action = self._action(file, "Save project &as ...", self.save_project_as,
                                           QKeySequence.StandardKey.SaveAs)
        self._action(file, "Try with &sample data ...", self.make_sample)
        file.addSeparator()
        self.settings_action = self._action(file, "Session se&ttings ...", self.show_session_settings)
        self.import_action = self._action(file, "&Import from another project ...", self.import_dialog)
        self.data_action = self._action(file, "&Participant data and deletion ...", self.show_participant_data)
        self.move_action = self._action(file, "&Move project to another output folder ...", self.move_session_dialog)
        file.addSeparator()
        self._action(file, "&Quit", self.close, QKeySequence.StandardKey.Quit)
        help_menu = self.menuBar().addMenu("&Help")
        self._action(help_menu, "&Settings ...", self.show_settings)
        help_menu.addSeparator()
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

    # ---------- project ----------

    def set_session(self, root: Path | None, session: Session | None) -> None:
        self.root, self.session = root, session
        if root is not None:
            remember_session(root)
            if not last_output_root():  # the output folder is chosen in Help > Settings; until then, the first one
                settings().setValue("output_root", str(Path(root).parent))
        if root is None:
            self.project = None
        for action in (self.data_action, self.move_action, self.save_action, self.save_as_action,
                       self.settings_action, self.import_action):
            action.setEnabled(session is not None)
        self._show_name()
        for section in self.sections:
            section.refresh()
        if session is None:
            self.go("session")
        self.update_nav()

    def _show_name(self) -> None:
        root = self.root
        name = self.project.stem if self.project else root.name if root else ""
        self.session_label.setText(f"Project: {name}  ({root})" if root else "")
        self.setWindowTitle(f"{s('app_name')} - {name}" if root else s("app_name"))

    def save(self) -> None:
        if self.session is not None and self.root is not None:
            save_session(self.session, self.root)

    def changed(self) -> None:
        """A section changed the project: the other sections and the badges follow."""
        for section in self.sections:
            if section is not self.current_page():
                section.refresh()
        self.update_nav()

    # ---------- project files ----------

    def save_project(self) -> Path | None:
        """Save the project and its project file (folder + open section). Asks where the first time."""
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
                                              f"mocapapp project (*{SUFFIX});;Project file (session.json)")
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path) -> None:
        """A project file, a session.json or a project folder."""
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
        self.go(OLD_STEPS.get(project.step, project.step))

    def new_session(self) -> None:
        if self.busy:
            return
        self.set_session(None, None)

    def open_session_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open a project folder", str(Path.home()))
        if folder:
            self.open_session(Path(folder))

    def open_session(self, root: Path) -> bool:
        """Open a project folder. A 0.1.x session folder is upgraded first (its old file is kept as a backup)."""
        if self.busy:
            return False
        try:
            session = load_session(root)
        except (OSError, ValueError) as exc:
            error_box(self, "Cannot open project", f"{root} is not a readable project folder: {exc}")
            return False
        self.project = None
        self.set_session(root, session)
        self.go("session")
        return True

    def make_sample(self) -> None:
        from mocapapp.core.sample import make_sample_session

        if self.busy:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Where should the sample project be created? (ASCII-only path)", str(Path.home()))
        if not folder:
            return
        dlg = QProgressDialog("Preparing the sample project ...", None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()

        def done(result):
            dlg.close()
            self.project = None
            self.set_session(*result)
            self.go("session")

        def failed(msg, details):
            dlg.close()
            error_box(self, "Sample project not created", msg, details)

        Background(self, make_sample_session, Path(folder), on_done=done, on_error=failed,
                   on_progress=dlg.setLabelText, progress=True)

    def show_session_settings(self) -> None:
        from mocapapp.gui.session_settings import SessionSettingsDialog

        if self.session is None or self.busy:
            return
        dlg = SessionSettingsDialog(self, self.session, self.root)
        dlg.exec()
        self.changed()
        self.current_page().refresh()
        if dlg.move_requested:
            self.move_session_dialog()

    def import_dialog(self) -> None:
        from mocapapp.gui.import_dialog import ImportDialog

        if self.session is None or self.busy:
            return
        dlg = ImportDialog(self, self.session, self.root)
        dlg.exec()
        if dlg.imported is not None:
            self.changed()
            self.current_page().refresh()

    def move_session_dialog(self) -> None:
        """Move the project folder into another output folder (Session settings and the File menu)."""
        from mocapapp.core.workflow import SetupError, plan_move

        if self.session is None or self.busy:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the new output folder (the project folder moves into it)", str(Path(self.root).parent))
        if not folder:
            return
        self.save()
        try:
            plan = plan_move(self.session, self.root, Path(folder))
        except SetupError as exc:
            error_box(self, "Project not moved", str(exc))
            return
        answer = QMessageBox.question(self, "Move the project?", plan.describe())
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.move_session(plan)

    def move_session(self, plan) -> None:
        from mocapapp.core.workflow import move_session

        old = self.root
        dlg = QProgressDialog("Moving the project ...", None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        self.set_busy(True)

        def done(result):
            dest, warnings = result
            dlg.close()
            self.set_busy(False)
            forget_session(old)
            key = self.current_page().key
            self.set_session(dest, self.session)  # remembers the new place and output folder
            self.go(key)
            if self.project is not None:
                self._write_project(self.project)  # the project file follows the project folder
            self.statusBar().showMessage(f"Project moved to {dest}", 8000)
            if warnings:
                QMessageBox.warning(self, "Old folder not removed", "\n\n".join(warnings))

        def failed(msg, details):
            dlg.close()
            self.set_busy(False)
            error_box(self, "Project not moved", msg, details)

        self.mover = Background(self, move_session, plan, on_done=done, on_error=failed,
                                on_progress=dlg.setLabelText, progress=True)

    def show_participant_data(self) -> None:
        """Consent, retention and deletion for every trial; reachable at any time (§8.4)."""
        from mocapapp.gui.privacy_dialog import ParticipantDataDialog

        if self.session is None or self.busy:
            return
        root, key = self.root, self.current_page().key
        dlg = ParticipantDataDialog(self, self.session, root)
        dlg.exec()
        if dlg.session_deleted:
            forget_session(root)
            self.set_session(None, None)
            return
        self.set_session(root, self.session)  # sections show deleted/withdrawn trials
        self.go(key)

    # ---------- navigation ----------

    def current_page(self) -> Section:
        return self.sections[self.stack.currentIndex()]

    def section(self, key: str) -> Section:
        return next(sec for sec in self.sections if sec.key == key)

    def reachable(self, index: int) -> bool:
        return index == 0 or self.session is not None

    def first_incomplete(self) -> int:
        return 0

    def go(self, where: int | str) -> None:
        keys = [sec.key for sec in self.sections]
        index = keys.index(where) if isinstance(where, str) and where in keys else where
        if not isinstance(index, int) or not 0 <= index < len(self.sections) or self.busy:
            return
        if not self.reachable(index):
            index = 0
        self.stack.setCurrentIndex(index)
        section = self.sections[index]
        section.refresh()
        self.page_title.setText(section.title)
        self.purpose.setText(f"{section.purpose} <a href='why'>How it works</a>")
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(index)
        self.sidebar.blockSignals(False)
        self.update_nav()

    def _sidebar_clicked(self, row: int) -> None:
        if self.reachable(row) and not self.busy:
            self.go(row)
        else:
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentRow(self.stack.currentIndex())
            self.sidebar.blockSignals(False)

    def badges(self) -> dict[str, str]:
        """Sidebar badge per section: a tick, 'N to do', or nothing."""
        from mocapapp.core import readiness

        if self.session is None:
            return {}
        try:
            todo = readiness.section_todo(self.session, self.root)
        except Exception:  # a badge must never break the window (an unreadable file is reported in its section)
            return {}
        return {key: readiness.badge(n) for key, n in todo.items()}

    def update_nav(self) -> None:
        badges = self.badges()
        for k, section in enumerate(self.sections):
            item = self.sidebar.item(k)
            badge = badges.get(section.key, "")
            item.setText(f"{section.title}    {badge}" if badge else section.title)
            item.setToolTip({"✓": "Nothing to do here"}.get(badge, badge))
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            item.setFlags(flags if self.reachable(k) else Qt.ItemFlag.NoItemFlags)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy and keep_screen_on():
            self.keep_awake.start()
        elif not busy:
            self.keep_awake.stop()
        self.sidebar.setEnabled(not busy)
        self.menuBar().setEnabled(not busy)
        self.update_nav()

    # ---------- help ----------

    def show_settings(self) -> None:
        from mocapapp.gui.settings_dialog import SettingsDialog

        if SettingsDialog(self).exec() and self.session is None:
            self.section("session").out_edit.setText(last_output_root())  # the new-project form follows

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
        """For a participant: ask the study details first (kept for the project's next forms). From the Help
        menu (no pseudonym): a form with the open project's details, or a blank one."""
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

    def save_all_consent_pdfs(self, codes: list[str] | None = None) -> None:
        """These participants' forms (None: every participant's) in one PDF, each on new pages, for printing."""
        from mocapapp.gui.consent_dialog import ConsentInfoDialog
        from mocapapp.gui.greeting import save_pdf

        if self.session is None:
            return
        codes = codes or [p.code for p in self.session.active_participants]
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
