"""Shared GUI helpers: background jobs, issue lists, settings, opening files. No processing logic lives here."""

from __future__ import annotations

import html
import traceback
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox, QTextBrowser, QWidget

ORG, APP = "mocapapp", "mocapapp"
MAX_RECENT = 8

# never colour-only: every status carries a word too (PROJECT.md §7)
LEVEL_TEXT = {"error": "Problem", "action": "To do", "warning": "Check", "info": "Note", "ok": "OK"}
LEVEL_COLOR = {"error": "#c62828", "action": "#1565c0", "warning": "#b26a00", "info": "#555", "ok": "#2e7d32"}
STATUS_LEVEL = {"ok": "ok", "ready": "ok", "done": "ok", "warn": "warning", "fail": "error", "failed": "error",
                "pending": "info", "missing": "action", "needs_sync_fix": "action", "cancelled": "warning",
                "running": "info", "partial": "warning"}


def settings() -> QSettings:
    # defaultFormat() is the native store (registry/plist) unless changed: the tests switch it to INI files
    # in a temp folder, so they never touch the user's real settings.
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, ORG, APP)


def recent_sessions() -> list[Path]:
    paths = settings().value("recent_sessions", []) or []
    if isinstance(paths, str):
        paths = [paths]
    return [Path(p) for p in paths if (Path(p) / "session.json").is_file()]


def remember_session(root: Path) -> None:
    paths = [str(root)] + [str(p) for p in recent_sessions() if Path(p) != Path(root)]
    settings().setValue("recent_sessions", paths[:MAX_RECENT])


def forget_session(root: Path) -> None:
    settings().setValue("recent_sessions", [str(p) for p in recent_sessions() if Path(p) != Path(root)])


def last_output_root() -> str:
    """Help > Settings: the output folder for new sessions (until set there, the folder of the first session)."""
    return str(settings().value("output_root", "") or "")


def keep_screen_on() -> bool:
    """Help > Settings: keep the screen on and the computer awake while the app is working."""
    return settings().value("keep_screen_on", True, type=bool)


def badge(level: str, text: str | None = None) -> str:
    """HTML: coloured word, e.g. 'Problem'."""
    word = html.escape(text or LEVEL_TEXT.get(level, level))
    return f"<b style='color:{LEVEL_COLOR.get(level, '#555')}'>{word}</b>"


def status_badge(status: str) -> str:
    return badge(STATUS_LEVEL.get(status, "info"), status.replace("_", " "))


def issues_html(issues, empty: str = "No problems found.") -> str:
    if not issues:
        return f"<p>{badge('ok')} {html.escape(empty)}</p>"
    order = {"error": 0, "action": 1, "warning": 2, "info": 3}
    rows = []
    for i in sorted(issues, key=lambda i: order.get(i.level, 9)):
        fix = f"<br><span style='color:#444'>&rarr; {html.escape(i.fix)}</span>" if i.fix else ""
        rows.append(f"<li>{badge(i.level)} <b>{html.escape(i.where)}</b>: {html.escape(i.what)}{fix}</li>")
    return "<ul style='margin-left:-20px'>" + "".join(rows) + "</ul>"


class IssueView(QTextBrowser):
    def __init__(self, parent: QWidget | None = None, min_height: int = 90):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setMinimumHeight(min_height)

    def show_issues(self, issues, empty: str = "No problems found.") -> None:
        self.setHtml(issues_html(issues, empty))


def note(text: str, kind: str = "info") -> QLabel:
    """A framed, wrapped paragraph (e.g. the sync reminder)."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(label.textFormat())
    border = {"info": "#90a4ae", "important": "#1565c0", "warning": "#b26a00"}[kind]
    label.setStyleSheet(f"QLabel {{ border-left: 4px solid {border}; background: palette(alternate-base); "
                        "padding: 8px; }")
    label.setFrameShape(QFrame.Shape.NoFrame)
    return label


def heading(text: str, size: int = 15) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(size)
    font.setBold(True)
    label.setFont(font)
    label.setWordWrap(True)
    return label


def open_path(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def error_box(parent: QWidget | None, title: str, text: str, details: str = "") -> None:
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    if details:
        box.setDetailedText(details)
    box.exec()


def confirm(parent: QWidget | None, title: str, text: str, ok: str = "Continue") -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


# ---------- background jobs ----------

class _Job(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str, str)  # message, details

    def __init__(self, fn: Callable, args: tuple, kwargs: dict):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # shown to the user in plain words, details folded away
            self.failed.emit(str(exc) or exc.__class__.__name__, traceback.format_exc())
        else:
            self.done.emit(result)


class Background(QObject):
    """Run fn in a worker thread. fn may take a `progress` callable (pass progress=True)."""

    finished = Signal()

    def __init__(self, parent: QObject, fn: Callable, *args, on_done: Callable | None = None,
                 on_error: Callable | None = None, on_progress: Callable | None = None, progress: bool = False,
                 **kwargs):
        super().__init__(parent)
        self.thread = QThread()
        self.job = _Job(fn, args, kwargs)
        if progress:
            kwargs["progress"] = self.job.progress.emit
        self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run)
        # Connect to slots of this object (it lives in the GUI thread) so the callbacks run there: a plain Python
        # callable would get a direct connection and touch widgets from the worker thread.
        self.on_done, self.on_error, self.on_progress = on_done, on_error, on_progress
        self.job.progress.connect(self._progress)
        self.job.done.connect(self._done)
        self.job.failed.connect(self._failed)
        self.thread.finished.connect(self._cleanup)
        self.thread.start()

    def _progress(self, text: str) -> None:
        if self.on_progress:
            self.on_progress(text)

    def _done(self, result) -> None:
        self.thread.quit()
        if self.on_done:
            self.on_done(result)

    def _failed(self, message: str, details: str) -> None:
        self.thread.quit()
        if self.on_error:
            self.on_error(message, details)

    def _cleanup(self) -> None:
        self.job.deleteLater()
        self.thread.deleteLater()
        self.finished.emit()
        self.deleteLater()

    def wait(self, ms: int = 600000) -> bool:
        return self.thread.wait(ms)


def readonly(text: str):
    """A non-editable table cell."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def fit_rows(table, min_rows: int = 1) -> None:
    """Size a small table to its rows (no large empty area below them)."""
    table.resizeRowsToContents()
    rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
    rows = max(rows, min_rows * table.verticalHeader().defaultSectionSize())
    scroll = table.horizontalScrollBar().sizeHint().height()
    table.setFixedHeight(table.horizontalHeader().sizeHint().height() + rows + 2 * table.frameWidth() + scroll)


def para(text: str):
    """A wrapped paragraph label (an unwrapped long label forces the whole page wider than the window)."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def clear_table(table) -> None:
    """Drop all rows, including cell widgets from an earlier fill."""
    table.clearContents()
    table.setRowCount(0)
