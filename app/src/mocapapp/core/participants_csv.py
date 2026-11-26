"""Participants from a CSV file (0.2.0rc3): Participants > Import CSV and `mocapapp session import`.

One row per participant. The first row names the columns: `Pseudonym` (optional), `Height`, and any detail of the
project by its name, with or without its unit ("Weight" or "Weight (kg)"). A row whose pseudonym is already in the
project updates that participant; any other row adds a participant (with the usual number of trials, so that their
details can be kept), with the pseudonym given or a random one. Columns the project does not know are reported and
left out. Every row is checked first: one wrong value and nothing is imported. Consent is never imported: it is
confirmed in the app, by the researcher holding the signed form."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from mocapapp.core.session import Session, parse_height, save_session
from mocapapp.core.workflow import (
    PSEUDONYM_RE,
    SetupError,
    add_participant,
    detail_value,
    existing_codes,
    rename_participant,
)

PSEUDONYM_NAMES = ("pseudonym", "code", "participant", "participant code", "id")
UNIT_RE = re.compile(r"\s*[\(\[].*?[\)\]]\s*$")  # "Weight (kg)" -> "Weight"
MAX_ROWS = 1000


@dataclass
class ImportResult:
    added: list[str] = field(default_factory=list)  # pseudonyms
    updated: list[str] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.added)} participant(s) added, {len(self.updated)} updated."]
        if self.ignored_columns:
            parts.append(f"Columns left out (not details of this project): {', '.join(self.ignored_columns)}. Add "
                         "them in Columns first to import them.")
        return " ".join(parts)


def _norm(name: str) -> str:
    return " ".join(UNIT_RE.sub("", name).split()).casefold()


def read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    """The header and the rows of a CSV file: comma, semicolon or tab separated (Excel's 'CSV UTF-8' too)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # Excel's plain 'CSV' on Windows
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise SetupError(f"{Path(path).name} is empty.")
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO("\n".join(lines)), dialect)]
    if len(rows) - 1 > MAX_ROWS:
        raise SetupError(f"{Path(path).name} has more than {MAX_ROWS} rows.")
    return rows[0], rows[1:]


def import_participants(session: Session, root: Path, path: Path) -> ImportResult:
    header, rows = read_rows(path)
    by_name = {}
    for d in session.details:
        by_name[_norm(d.label)] = d
        by_name[_norm(d.key)] = d
    columns: dict[int, object] = {}  # column index -> "code" or a DetailField
    result = ImportResult()
    for i, name in enumerate(header):
        key = _norm(name)
        if key in PSEUDONYM_NAMES and "code" not in columns.values():
            columns[i] = "code"
        elif key in by_name and by_name[key] not in columns.values():
            columns[i] = by_name[key]
        elif name.strip():
            result.ignored_columns.append(name.strip())
    if not any(c != "code" for c in columns.values()):
        raise SetupError("No column of the file is a detail of this project. The first row names the columns, for "
                         "example: Pseudonym, Height, Weight (kg).")

    # check every row first: nothing changes when one value is wrong
    by_code = {p.code.upper(): p for p in session.participants}
    taken = {c.upper() for c in existing_codes(Path(root).parent)}
    plan, problems, seen = [], [], set()
    for n, row in enumerate(rows, 2):
        code, values = "", {}
        for i, col in columns.items():
            cell = row[i] if i < len(row) else ""
            if col == "code":
                code = cell
                continue
            try:
                if col.key == "height":
                    values["height"] = parse_height(cell) if cell else None
                else:
                    values[col.key] = detail_value(col, cell)
            except (SetupError, ValueError) as exc:
                problems.append(f"row {n}: {str(exc).strip(chr(39))}")
        if not code and not any(v for v in values.values()):
            continue  # an empty row
        person = by_code.get(code.upper()) if code else None
        if code and person is None:
            if not PSEUDONYM_RE.match(code):
                problems.append(f"row {n}: '{code}' is not a pseudonym (1-24 letters, digits, - or _; never a real "
                                "name)")
            elif code.upper() in taken:
                problems.append(f"row {n}: the pseudonym {code} is used by another project in this output folder")
        if code.upper() in seen and code:
            problems.append(f"row {n}: {code} is in the file twice")
        seen.add(code.upper())
        if person is not None and person.withdrawn_at:
            problems.append(f"row {n}: {code} withdrew; nothing of theirs can change")
        plan.append((code, person, values))
    if problems:
        raise SetupError("Nothing was imported. " + "; ".join(problems[:8]) + (" ..." if len(problems) > 8 else ""))

    for code, person, values in plan:
        if person is None:
            person = add_participant(session, root)
            if code:
                rename_participant(session, root, person.label, code)
            result.added.append(person.code)
        else:
            result.updated.append(person.code)
        for key, value in values.items():
            if key == "height":
                if value is not None:
                    person.height_m = value
                    for t in session.trials_of(person.label):
                        t.details.pop("height", None)
                continue
            if not value:
                continue
            for t in session.trials_of(person.label):
                if not t.withdrawn_at:
                    t.details[key] = value
    save_session(session, root)
    return result


def example_header(session: Session) -> str:
    """The first row of a CSV file for this project, for the help text."""
    names = ["Pseudonym"] + [f"{d.label} ({d.unit})" if d.unit else d.label for d in session.asked_details]
    return ", ".join(names)

