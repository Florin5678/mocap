"""Participant data and deletion (PROJECT.md §8.4): every deletion shows its exact list first.

Thin wrapper: plans, safeguards and the deletion itself live in core/deletion.py.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import deletion
from mocapapp.core.session import Session
from mocapapp.gui.common import badge, clear_table, error_box, fit_rows, para, readonly


def plan_html(plan: deletion.DeletionPlan) -> str:
    parts = []
    for b in plan.blockers:
        parts.append(f"<p>{badge('error', 'Cannot delete yet')} {html.escape(b)}</p>")
    for w in plan.warnings:
        parts.append(f"<p>{badge('warning', 'Warning')} {html.escape(w)}</p>")
    if not plan.items:
        parts.append("<p>Nothing left to delete.</p>")
    for g in plan.groups():
        items = [i for i in plan.items if i.group == g]
        parts.append(f"<h4>{html.escape(deletion.GROUP_TITLES[g])} - {len(items)} item(s), "
                     f"{deletion.human_size(plan.size(g))}</h4><table cellspacing=2>")
        for i in items:
            parts.append(f"<tr><td align=right>{deletion.human_size(i.size)}</td>"
                         f"<td>{html.escape(str(i.path))}{'/' if i.is_dir else ''}</td></tr>")
        parts.append("</table>")
    parts.append(f"<p><b>Total: {deletion.human_size(plan.size())}.</b> Files are deleted permanently, not moved "
                 "to the Recycle Bin / Trash.</p>")
    parts += [f"<p>{badge('info')} {html.escape(n)}</p>" for n in plan.notes]
    return "".join(parts)


class DeleteDialog(QDialog):
    """Shows one deletion plan; deletes only after the exact list was shown and (if needed) the code typed."""

    def __init__(self, parent: QWidget, session: Session, root: Path, scope: str, label: str | None = None,
                 after_run: bool = False):
        super().__init__(parent)
        self.session, self.root, self.scope, self.label, self.after_run = session, root, scope, label, after_run
        self.result_: deletion.DeletionResult | None = None
        trial = session.trial(label) if label else None
        titles = {"raw": f"Delete raw footage of trial {label} ({trial.code if trial else ''})",
                  "trial": f"Delete trial {label} ({trial.code if trial else ''}) with its data",
                  "participant": f"Delete all data of participant {trial.participant if trial else ''} "
                                 f"({trial.code if trial else ''})",
                  "session": f"Delete the whole session {root.name}"}
        self.setWindowTitle(titles[scope])
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        intro = {"raw": "The working copies of the videos and everything derived from them that shows or traces "
                        "the person are deleted. The 3D results are kept.",
                 "trial": "The trial is removed from the session with everything made from it: working copies of "
                          "its videos, keypoints, 3D results, joint angles, QA and exported files. The "
                          "participant's other trials are not affected.",
                 "participant": "Use this when a participant withdraws consent: the raw footage of every trial of "
                                "theirs and every result computed from it are deleted. Other participants are not "
                                "affected.",
                 "session": "Everything in the session folder is deleted, for every participant."}[scope]
        layout.addWidget(para(f"<b>{html.escape(intro)}</b>"))
        self.originals = QCheckBox("Also delete the original recordings in the camera folders"
                                   + (" (you will type the pseudonym)" if scope != "session" else ""))
        self.originals.toggled.connect(self._replan)
        layout.addWidget(self.originals)
        self.view = QTextBrowser()
        layout.addWidget(self.view, 1)
        self.confirm_row = QWidget()
        cr = QHBoxLayout(self.confirm_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.confirm_label = QLabel()
        self.confirm_edit = QLineEdit()
        self.confirm_edit.textChanged.connect(self._update_button)
        cr.addWidget(self.confirm_label)
        cr.addWidget(self.confirm_edit, 1)
        layout.addWidget(self.confirm_row)
        buttons = QDialogButtonBox()
        self.delete_button = buttons.addButton("Delete permanently", QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.delete_button.clicked.connect(self._delete)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.plan: deletion.DeletionPlan | None = None
        self._replan()

    def _replan(self) -> None:
        orig = self.originals.isChecked()
        try:
            if self.scope == "raw":
                self.plan = deletion.plan_raw(self.session, self.root, self.label, orig, after_run=self.after_run)
            elif self.scope == "trial":
                self.plan = deletion.plan_trial(self.session, self.root, self.label, orig)
            elif self.scope == "participant":
                self.plan = deletion.plan_participant(self.session, self.root, self.label, orig)
            else:
                self.plan = deletion.plan_session(self.session, self.root, orig)
        except deletion.DeletionError as exc:
            self.plan = None
            self.view.setHtml(f"<p>{html.escape(str(exc))}</p>")
            self._update_button()
            return
        self.view.setHtml(plan_html(self.plan))
        need = self.plan.confirm_text
        self.confirm_row.setVisible(need is not None)
        if need:
            what = "the session folder name" if self.scope == "session" else "the pseudonym"
            self.confirm_label.setText(f"To confirm, type {what} <b>{html.escape(need)}</b>:")
        self._update_button()

    def _update_button(self) -> None:
        p = self.plan
        ok = p is not None and not p.blockers and (bool(p.items) or p.scope == "trial") and (
            p.confirm_text is None or self.confirm_edit.text().strip().casefold() == p.confirm_text.casefold())
        self.delete_button.setEnabled(ok)

    def _delete(self) -> None:
        if self.plan is None:
            return
        if self.plan.confirm_text is None and QMessageBox.question(
                self, "Delete permanently?", f"Delete {len(self.plan.items)} item(s), "
                f"{deletion.human_size(self.plan.size())}? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.result_ = deletion.execute(self.plan, self.session, self.confirm_edit.text())
        except deletion.DeletionError as exc:
            error_box(self, "Not deleted", str(exc))
            return
        r = self.result_
        text = f"Deleted {r.deleted} item(s), {deletion.human_size(r.bytes)}."
        if r.failures:
            text += "\n\nCould not delete:\n" + "\n".join(r.failures)
        text += "\n\nThe app cannot do these for you - please check:\n" + "\n".join(f"[ ] {c}" for c in r.checklist)
        (QMessageBox.warning if r.failures else QMessageBox.information)(self, "Deletion finished", text)
        self.accept()


class ParticipantDataDialog(QDialog):
    """The session's participant-data page: consent, retention and deletion for every trial (§8.3-8.4)."""

    def __init__(self, win, session: Session, root: Path):
        super().__init__(win)
        self.win, self.session, self.root = win, session, root
        self.session_deleted = False
        self.setWindowTitle(f"Participant data - {root.name}")
        self.resize(980, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(para(
            "Raw footage can be deleted at any time, also long after the analysis, for example when a participant "
            "asks for it. Every deletion first shows the exact files and sizes. Deleted files are gone permanently."))
        self.reminder = QLabel()
        self.reminder.setWordWrap(True)
        layout.addWidget(self.reminder)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Participant", "Storage consent", "Planned", "Raw footage", "", ""])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        layout.addStretch(1)
        row = QHBoxLayout()
        whole = QPushButton("Delete the whole session ...")
        whole.clicked.connect(lambda: self._open("session"))
        row.addWidget(whole)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        notes = deletion.reminders(self.session)
        self.reminder.setText("".join(f"<p>{badge('action', 'Reminder')} {html.escape(n)}</p>" for n in notes))
        self.reminder.setVisible(bool(notes))
        clear_table(self.table)
        trials = self.session.trials
        self.table.setRowCount(len(trials))
        self.table.setVerticalHeaderLabels([f"Trial {t.label}" for t in trials])
        for r, t in enumerate(trials):
            self.table.setItem(r, 0, readonly(t.code))
            self.table.setItem(r, 1, readonly("yes" if t.consent.raw_footage_storage else "no"))
            planned = "delete after export" if t.delete_raw else f"keep until {t.retention_until or 'deleted'}"
            self.table.setItem(r, 2, readonly(planned))
            if t.withdrawn_at:
                state = f"all data deleted {t.withdrawn_at[:10]}"
            elif t.raw_deleted_at:
                state = f"deleted {t.raw_deleted_at[:10]}"
            else:
                plan = deletion.plan_raw(self.session, self.root, t.label)
                state = f"stored ({deletion.human_size(plan.size())})"
            self.table.setItem(r, 3, readonly(state))
            if t.withdrawn_at:
                continue
            raw = QPushButton("Delete raw footage ...")
            raw.clicked.connect(lambda _=False, lbl=t.label: self._open("raw", lbl))
            everything = QPushButton("Delete all data of this participant ...")
            everything.clicked.connect(lambda _=False, lbl=t.label: self._open("participant", lbl))
            self.table.setCellWidget(r, 4, raw)
            self.table.setCellWidget(r, 5, everything)
        self.table.resizeColumnsToContents()
        fit_rows(self.table)

    def _open(self, scope: str, label: str | None = None) -> None:
        dlg = DeleteDialog(self, self.session, self.root, scope, label)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_ is None:
            return
        if scope == "session":
            self.session_deleted = True
            self.accept()
            return
        self.refresh()
