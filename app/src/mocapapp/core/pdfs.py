"""Printable calibration board and fillable consent form (PROJECT.md §6.2, §8.2)."""

from __future__ import annotations

import io
import re
from importlib import resources
from pathlib import Path

import cv2
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer

from mocapapp.core.calibration.intrinsics import BoardSpec, charuco_board

CONSENT_TEMPLATE_VERSION = "2"
MARGIN_MM = 15
PRINT_DICT_SIZE = 50  # smallest 4x4 dictionary; the detector also tries larger ones


class PdfError(ValueError):
    pass


# ---------- calibration board ----------

def board_squares(spec: BoardSpec) -> tuple[int, int]:
    """(columns, rows) of squares: internal corners + 1."""
    return spec.corners_nb[1] + 1, spec.corners_nb[0] + 1


def board_image(spec: BoardSpec, px_per_square: int = 120) -> np.ndarray:
    cols, rows = board_squares(spec)
    size = (cols * px_per_square, rows * px_per_square)
    if spec.board_type == "charuco":
        board = charuco_board(spec, PRINT_DICT_SIZE, legacy=False)
        return board.generateImage(size, marginSize=0, borderBits=1)
    img = np.full((size[1], size[0]), 255, np.uint8)
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                img[r * px_per_square:(r + 1) * px_per_square, c * px_per_square:(c + 1) * px_per_square] = 0
    return img


def dict_name(spec: BoardSpec) -> str:
    return f"DICT_{spec.marker_resolution}X{spec.marker_resolution}_{PRINT_DICT_SIZE}"


def board_pdf(out: Path, spec: BoardSpec) -> Path:
    cols, rows = board_squares(spec)
    w_mm, h_mm = cols * spec.square_size_mm, rows * spec.square_size_mm
    page = None
    for size in (landscape(A4), landscape(A3)):
        if w_mm + 2 * MARGIN_MM <= size[0] / mm and h_mm + 2 * MARGIN_MM + 40 <= size[1] / mm:
            page = size
            break
    if page is None:
        raise PdfError(f"A {w_mm:.0f} x {h_mm:.0f} mm board does not fit on A3. Use smaller squares.")
    ok, png = cv2.imencode(".png", board_image(spec))
    if not ok:
        raise PdfError("Could not render the board image.")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=page)
    c.setTitle("Calibration board")
    pw, ph = page
    x0, y0 = (pw - w_mm * mm) / 2, (ph - h_mm * mm) / 2 - 5 * mm
    c.drawImage(ImageReader(io.BytesIO(png.tobytes())), x0, y0, w_mm * mm, h_mm * mm)
    name = "ChArUco" if spec.board_type == "charuco" else "Chessboard"
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN_MM * mm, ph - 12 * mm,
                 f"{name} {cols} x {rows} squares ({spec.corners_nb[0]} x {spec.corners_nb[1]} internal corners), "
                 f"square {spec.square_size_mm:g} mm" + (f", markers {dict_name(spec)}" if spec.board_type == "charuco"
                                                         else ""))
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN_MM * mm, ph - 18 * mm, "Print at 100 % / actual size (no 'fit to page'). Check the scale bar "
                 "with a ruler: it must be exactly 5 cm. Glue the sheet flat on a rigid board.")
    # 5 cm scale bar
    c.setLineWidth(1)
    bx, by = MARGIN_MM * mm, 15 * mm
    c.rect(bx, by, 50 * mm, 3 * mm, fill=1)
    for k in range(6):
        c.line(bx + k * 10 * mm, by - 1.5 * mm, bx + k * 10 * mm, by)
    c.drawString(bx + 52 * mm, by, "5 cm")
    c.drawRightString(pw - MARGIN_MM * mm, by, "mocapapp - research use only")
    c.showPage()
    c.save()
    return out


# ---------- consent form ----------

class _Field(Flowable):
    """An AcroForm field with a label, laid out by platypus."""

    def __init__(self, kind: str, name: str, label: str, value: str = "", width_mm: float = 80, lines: int = 1):
        super().__init__()
        self.kind, self.name, self.label, self.value, self.lines = kind, name, label, value, lines
        self.width = 170 * mm
        self.box_h = (7 + 5 * (lines - 1)) * mm
        self.height = self.box_h + 2 * mm if kind == "text" else 8 * mm
        self.field_w = width_mm * mm

    def draw(self):
        c = self.canv
        form = c.acroForm
        c.setFont("Helvetica", 10)
        if self.kind == "checkbox":
            form.checkbox(name=self.name, x=0, y=1 * mm, size=5 * mm, buttonStyle="check", borderColor=colors.black,
                          fillColor=colors.white, forceBorder=True, relative=True)
            c.drawString(8 * mm, 2 * mm, self.label)
        else:
            c.drawString(0, self.box_h - 4 * mm, self.label)
            form.textfield(name=self.name, value=self.value, x=55 * mm, y=0, width=self.field_w, height=self.box_h,
                           borderColor=colors.black, fillColor=colors.white, fontSize=10, forceBorder=True,
                           relative=True, fieldFlags="multiline" if self.lines > 1 else "")


def _template_blocks(text: str):
    """Tiny Markdown subset: '# ', '## ', '- ' and paragraphs."""
    para: list[str] = []
    for line in text.splitlines() + [""]:
        if not line.strip():
            if para:
                yield "p", " ".join(para)
                para = []
        elif line.startswith("# "):
            yield "h1", line[2:]
        elif line.startswith("## "):
            yield "h2", line[3:]
        elif line.startswith("- "):
            yield "li", line[2:]
        else:
            para.append(line.strip())


def consent_text() -> str:
    return resources.files("mocapapp.resources").joinpath("consent_template.md").read_text(encoding="utf-8")


BLANK = "____"  # in the template: something the researcher fills in
MULTILINE = {"Purpose", "Researcher(s)", "Risks"}  # labelled blanks that get a taller box
CHARS_PER_LINE = 60  # in a 115 mm box at 10 pt


def study_values(info) -> dict[str, str]:
    """The consent dialog's answers (StudyInfo) by template label; empty answers stay boxes to fill by hand."""
    if info is None:
        return {}
    people = [f"{name} ({contact})" if contact.strip() else name for name, contact in info.researchers
              if name.strip() or contact.strip()]
    return {"Study name": info.study_name, "Purpose": info.purpose, "Researcher(s)": "\n".join(people),
            "Place of storage": info.storage_place, "Access": info.access, "Usage": info.usage,
            "Retention": info.retention, "Risks": info.risks, "Compensation": info.compensation}


def _box_lines(label: str, value: str) -> int:
    needed = sum(max(1, -(-len(line) // CHARS_PER_LINE)) for line in value.splitlines()) if value.strip() else 1
    return max(3 if label in MULTILINE else 1, needed)


def consent_pdf(out: Path, code: str = "", text: str | None = None, info=None) -> Path:
    """The participation consent form. A paragraph 'Label: ____' becomes a fillable box (also fine on paper),
    pre-filled from `info` (the session's StudyInfo); a blank inside a sentence becomes a line to write on."""
    styles = getSampleStyleSheet()
    values = study_values(info)
    story = []
    for kind, body in _template_blocks(text or consent_text()):
        field = re.fullmatch(r"([^:]{1,40}):\s*" + re.escape(BLANK), body) if kind == "p" else None
        if field:
            label = field.group(1)
            value = values.get(label, "").strip()
            story.append(_Field("text", re.sub(r"\W+", "_", label.lower()).strip("_"), label + ":", value, 115,
                                lines=_box_lines(label, value)))
            story.append(Spacer(1, 1 * mm))
            continue
        body = body.replace(BLANK, "_" * 30)
        if kind == "h1":
            story.append(Paragraph(body, styles["Title"]))
        elif kind == "h2":
            story.append(Paragraph(body, styles["Heading3"]))
        elif kind == "li":
            story.append(Paragraph(body, styles["Normal"], bulletText="•"))
        else:
            story.append(Paragraph(body, styles["Normal"]))
            story.append(Spacer(1, 2 * mm))
    story += [Spacer(1, 6 * mm), Paragraph("Consent", styles["Heading3"]),
              _Field("text", "pseudonym", "Pseudonym", code, 50),
              _Field("checkbox", "consent", "I have read this information and consent to taking part."),
              _Field("checkbox", "raw_storage", "(Optional) I consent to my raw video footage being stored until "
                                                "the study is finished."),
              Spacer(1, 3 * mm),
              _Field("text", "name", "Name", "", 100),
              _Field("text", "date", "Date", "", 50),
              Spacer(1, 10 * mm),
              Paragraph("Signature: ______________________________________", styles["Normal"])]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title="Participation consent form" + (f" {code}" if code else ""))
    doc.build(story)
    return out
