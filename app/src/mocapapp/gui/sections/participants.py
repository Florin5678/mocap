"""Participants section (PROJECT.md §16.3, R11/R12): pseudonym, height, the other details asked for every trial,
and consent as one drop-down (not signed yet / signed, delete footage / signed, keep footage). What happens to the
raw footage follows the consent (§6.2), or Session settings when it is deleted for everyone (R13)."""

from __future__ import annotations

import html

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mocapapp.core import workflow
from mocapapp.core.session import format_height
from mocapapp.core.validate import Issue
from mocapapp.core.workflow import CONSENT_STATES, SetupError
from mocapapp.gui.columns_dialog import ColumnsDialog, detail_title
from mocapapp.gui.common import (
    IssueView,
    NoWheelCombo,
    clear_table,
    confirm,
    error_box,
    fit_rows,
    for_each,
    note,
    readonly,
    scroll,
    selected_rows,
)
from mocapapp.gui.main_window import Section


def footage_text(session, person) -> str:
    if person.withdrawn_at:
        return "all data deleted"
    if not person.consent.attested:
        return "waiting for consent"
    if not person.delete_raw:
        return "kept (storage consent)"
    from mocapapp.core.deletion import export_needed

    when = "after export" if export_needed(session) else "once processed"
    return f"deleted {when}" + (" (all participants)" if session.settings.delete_raw_all else "")


class ParticipantsSection(Section):
    key, title = "participants", "Participants"
    purpose = ("Pseudonyms, heights and consent. Each participant has one or more trials; a value entered here "
               "is kept with each of their trials.")

    def __init__(self, win):
        super().__init__(win)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(note("Never type a participant's real name: a pseudonym is generated for each participant. "
                              "Print each participant's consent form (Consent form); once you hold the signed form, "
                              "choose what was agreed under Consent. Changes are saved straight away."))
        row = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, text, slot, tip in (
                ("add", "Add participant", self.add, "A new pseudonym and the usual number of trials (Session "
                                                     "settings)."),
                ("random", "New pseudonym", self.new_code, "A fresh random pseudonym for each selected "
                                                           "participant."),
                ("remove", "Remove", self.remove, "The selected participants and their trials; only without videos "
                                                  "or results (for example someone who did not come)."),
                ("columns", "Columns ...", self.columns, "Which details are asked for every participant, and your "
                                                         "own columns."),
                ("forms", "Consent forms ...", self.forms,
                 "One PDF with the consent form of each selected participant (none selected: of every "
                 "participant), ready to print."),
                ("data", "Participant data and deletion ...", lambda: self.win.show_participant_data(),
                 "What is stored per participant; deletion and withdrawal (§8).")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.buttons[key] = b
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Ctrl/Shift: several
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._enable)
        layout.addWidget(self.table)
        self.issues = IssueView(min_height=40)
        self.issues.setMaximumHeight(140)
        layout.addWidget(self.issues)
        layout.addStretch()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))
        self.rows: list[str] = []  # participant label per table row
        self.cells: dict[tuple[str, str], QWidget] = {}

    # ---------- view ----------

    def details(self) -> list:
        return [d for d in self.session.asked_details if d.key != "height"]

    def refresh(self) -> None:
        if self.session is None:
            return
        ses = self.session
        details = self.details()
        headers = ["#", "Pseudonym", "Height"] + [detail_title(d) for d in details] + [
            "Consent", "Raw footage", "Trials", "Consent form"]
        clear_table(self.table)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.rows, self.cells = [], {}
        for person in ses.participants:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.rows.append(person.label)
            active = person.withdrawn_at is None
            self.table.setItem(r, 0, readonly(person.label))
            code = QLineEdit(person.code)
            code.setMaxLength(24)
            former = f" Earlier pseudonyms: {', '.join(person.former_codes)}." if person.former_codes else ""
            code.setToolTip("Letters, digits, - or _; never the participant's real name." + former)
            code.editingFinished.connect(lambda p=person.label, w=code: self._code_typed(p, w))
            height = QLineEdit(format_height(person.height_m) if person.height_m else "")
            height.setPlaceholderText("e.g. 1.75 m or 175 cm")
            height.editingFinished.connect(lambda p=person.label, w=height: self._value(p, "height", w.text()))
            cells = [code, height]
            trials = ses.trials_of(person.label)
            for d in details:
                value = trials[0].details.get(d.key, "") if trials else ""
                if d.kind == "choice":
                    w = NoWheelCombo()
                    w.addItem("", "")
                    for option in d.choices:
                        w.addItem(option, option)
                    w.setCurrentIndex(max(0, w.findData(value)))
                    w.currentIndexChanged.connect(lambda _i, p=person.label, k=d.key, w=w: self._value(
                        p, k, w.currentData()))
                else:
                    w = QLineEdit(value)
                    w.setPlaceholderText(d.unit)
                    w.editingFinished.connect(lambda p=person.label, k=d.key, w=w: self._value(p, k, w.text()))
                w.setEnabled(bool(trials))
                if not trials:
                    w.setToolTip("Kept with the participant's trials: add a trial first.")
                cells.append(w)
            consent = NoWheelCombo()
            for key, text in CONSENT_STATES.items():
                consent.addItem(text, key)
            consent.setCurrentIndex(consent.findData(workflow.consent_state(person)))
            consent.currentIndexChanged.connect(lambda _i, p=person.label, w=consent: self._consent(p, w.currentData()))
            cells.append(consent)
            for c, w in enumerate(cells, 1):
                w.setEnabled(w.isEnabled() and active)
                self.table.setCellWidget(r, c, w)
                self.cells[(person.label, headers[c])] = w
            base = 3 + len(details)
            self.table.setItem(r, base + 1, readonly(footage_text(ses, person)))
            self.table.setItem(r, base + 2, readonly(", ".join(t.label for t in trials) or "-"))
            if active:
                form = QPushButton("Generate ...")
                form.clicked.connect(lambda _=False, code=person.code: self.win.save_consent_pdf(code))
                self.table.setCellWidget(r, base + 3, form)
            else:
                self.table.setItem(r, base + 3, readonly("withdrawn"))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        fit_rows(self.table, max_rows=15)
        self._check()
        self._enable()

    def _check(self) -> None:
        issues = []
        for p in self.session.active_participants:
            where = f"{p.label} ({p.code})"
            if not p.consent.attested:
                issues.append(Issue("action", where, "Signed consent not confirmed yet (needed before a trial runs).",
                                    ""))
            if p.height_m is None:
                issues.append(Issue("action", where, "No height yet (needed before a trial runs).", ""))
        empty = "No participants yet: add them here, or with their first trial." if not self.session.participants \
            else "Every participant has a height and confirmed consent."
        self.issues.show_issues(issues, empty)

    def selected(self) -> list[str]:
        """Labels of the selected participants who did not withdraw (nothing of theirs can change)."""
        if self.session is None:
            return []
        active = {p.label for p in self.session.active_participants}  # rows may be from before a removal
        return [self.rows[r] for r in selected_rows(self.table) if r < len(self.rows) and self.rows[r] in active]

    def _enable(self) -> None:
        n = len(self.selected())
        self.buttons["random"].setEnabled(n > 0)
        self.buttons["remove"].setEnabled(n > 0)
        self.buttons["forms"].setEnabled(bool(self.session and self.session.active_participants))
        self.buttons["forms"].setText(f"Consent forms ({n}) ..." if n > 1 else "Consent forms ...")

    def _changed(self) -> None:
        """Rebuild once the current signal is handled (its widgets are replaced); the sidebar follows."""
        def later():
            if self.session is not None:
                selected = self.selected()
                self.refresh()
                model, flags = self.table.selectionModel(), self.table.selectionModel().SelectionFlag
                for label in selected:
                    if label in self.rows:
                        model.select(self.table.model().index(self.rows.index(label), 0), flags.Select | flags.Rows)
            self.completeChanged.emit()
            self.win.changed()

        QTimer.singleShot(0, later)

    # ---------- edits ----------

    def _value(self, label: str, key: str, value) -> None:
        person = self.session.participant(label)
        if key == "height":
            shown = format_height(person.height_m) if person.height_m else ""
            if str(value).strip() == shown:
                return
        try:
            workflow.set_participant_value(self.session, self.root, label, key, value)
        except (SetupError, ValueError) as exc:
            error_box(self, "Not saved", str(exc))
        self._changed()

    def _consent(self, label: str, state: str) -> None:
        try:
            workflow.set_consent(self.session, self.root, label, state)
        except SetupError as exc:
            error_box(self, "Consent not changed", str(exc))
        self._changed()

    def _code_typed(self, label: str, edit: QLineEdit) -> None:
        code = edit.text().strip()
        if code != self.session.participant(label).code:
            self.rename(label, code)

    def _confirmed(self, labels: list[str]) -> bool | None:
        """Ask once for all these participants when a new pseudonym affects more than session.json. None: the
        researcher said no."""
        parts = []
        for label in labels:
            consequences = workflow.rename_consequences(self.session, label)
            if consequences:
                head = f"{self.session.participant(label).code}: " if len(labels) > 1 else ""
                parts.append(head + " ".join(consequences))
        if not parts:
            return False
        if not confirm(self, "Change the pseudonym?" if len(labels) == 1 else "Change the pseudonyms?",
                       html.escape("\n\n".join(parts)).replace("\n", "<br>"), "Change"):
            return None
        return True

    def rename(self, label: str, code: str) -> None:
        confirmed = self._confirmed([label])
        if confirmed is not None:
            try:
                workflow.rename_participant(self.session, self.root, label, code, confirmed)
            except (SetupError, ValueError) as exc:
                error_box(self, "Pseudonym not changed", str(exc))
        self._changed()

    def new_code(self) -> None:
        labels = self.selected()
        confirmed = self._confirmed(labels) if labels else None
        if confirmed is None:
            return
        for_each(self, "Pseudonym not changed", labels,
                 lambda label: workflow.new_pseudonym(self.session, self.root, label, confirmed))
        self._changed()

    def add(self) -> None:
        person = workflow.add_participant(self.session, self.root)
        self.win.statusBar().showMessage(f"Participant {person.label} added: {person.code}.", 6000)
        self._changed()

    def remove(self) -> None:
        labels = self.selected()
        if not labels:
            return
        codes = ", ".join(html.escape(self.session.participant(label).code) for label in labels)
        trials = [t.label for label in labels for t in self.session.trials_of(label)]
        also = f" and their trial(s) {', '.join(trials)}" if trials else ""
        what = "the participant" if len(labels) == 1 else f"{len(labels)} participants"
        if not confirm(self, f"Remove {what}?", f"Remove {codes}{also} from the project?", "Remove"):
            return
        for_each(self, "Not removed", labels, lambda label: workflow.remove_participant(self.session, self.root, label))
        self._changed()

    def forms(self) -> None:
        labels = self.selected()
        if len(labels) == 1:
            self.win.save_consent_pdf(self.session.participant(labels[0]).code)
        else:
            self.win.save_all_consent_pdfs([self.session.participant(label).code for label in labels] or None)

    def columns(self) -> None:
        ColumnsDialog(self, self.session, self.root).exec()
        self._changed()
