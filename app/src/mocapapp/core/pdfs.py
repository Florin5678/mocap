"""Printable calibration board and fillable consent form (PROJECT.md §6.2, §8.2)."""

from __future__ import annotations

import html
import io
import re
from importlib import resources
from pathlib import Path

import cv2
import numpy as np
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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

class _Tickbox(Flowable):
    """A printed square to tick by hand, with its sentence."""

    def __init__(self, label: str):
        super().__init__()
        self.label = label
        self.width, self.height = 170 * mm, 8 * mm

    def draw(self):
        c = self.canv
        c.setLineWidth(0.8)
        c.rect(0, 1.5 * mm, 4.5 * mm, 4.5 * mm)
        c.setFont("Helvetica", 10)
        c.drawString(8 * mm, 2.5 * mm, self.label)


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
MULTILINE = {"Purpose", "Researcher(s)", "Risks"}  # blanks that get more lines to write on
WRITE_LINE = "_" * 62  # a line to write on by hand, across the text column


def study_values(info) -> dict[str, str]:
    """The consent dialog's answers (StudyInfo) by template label; empty answers become lines to write on."""
    if info is None:
        return {}
    people = [f"{name} ({contact})" if contact.strip() else name for name, contact in info.researchers
              if name.strip() or contact.strip()]
    return {"Study name": info.study_name, "Purpose": info.purpose, "Researcher(s)": "\n".join(people),
            "Place of storage": info.storage_place, "Access": info.access, "Usage": info.usage,
            "Retention": info.retention, "Risks": info.risks, "Compensation": info.compensation}


def _detail(label: str, value: str, style) -> Table:
    """'Label:' and its text, wrapped over as many lines as needed; a blank gets lines to write on."""
    if value:
        text = html.escape(value).replace("\n", "<br/>")
    else:
        text = "<br/><br/>".join([WRITE_LINE] * (3 if label in MULTILINE else 1))
    table = Table([[Paragraph(f"<b>{html.escape(label)}:</b>", style), Paragraph(text, style)]],
                  colWidths=[40 * mm, 130 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    table.hAlign = "LEFT"
    return table


def consent_pdf(out: Path, code: str = "", text: str | None = None, info=None,
                codes: list[str] | None = None) -> Path:
    """The participation consent form, a plain printable document (no form fields). A paragraph 'Label: ____'
    shows the detail entered in the app (`info`, the session's StudyInfo) or, when blank, lines to write on.
    With `codes`: one form per pseudonym in a single PDF, each starting on a new page."""
    styles = getSampleStyleSheet()
    story = []
    for i, c in enumerate(codes if codes else [code]):
        if i:
            story.append(PageBreak())
        story += _consent_story(c, text, info, styles)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    title = "Participation consent forms" if codes else "Participation consent form" + (f" {code}" if code else "")
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=title)
    doc.build(story)
    return out


def _consent_story(code: str, text: str | None, info, styles) -> list:
    normal = styles["Normal"]
    values = study_values(info)
    story = []
    for kind, body in _template_blocks(text or consent_text()):
        field = re.fullmatch(r"([^:]{1,40}):\s*" + re.escape(BLANK), body) if kind == "p" else None
        if field:
            story.append(_detail(field.group(1), values.get(field.group(1), "").strip(), normal))
            story.append(Spacer(1, 1.5 * mm))
            continue
        body = body.replace(BLANK, "_" * 30)
        if kind == "h1":
            story.append(Paragraph(body, styles["Title"]))
        elif kind == "h2":
            story.append(Paragraph(body, styles["Heading3"]))
        elif kind == "li":
            story.append(Paragraph(body, normal, bulletText="•"))
        else:
            story.append(Paragraph(body, normal))
            story.append(Spacer(1, 2 * mm))
    story += [Spacer(1, 6 * mm), Paragraph("Consent", styles["Heading3"]),
              _detail("Pseudonym", code, normal), Spacer(1, 3 * mm),
              _Tickbox("I have read this information and consent to taking part."),
              _Tickbox("(Optional) I consent to my raw video footage being stored until the study is finished."),
              Spacer(1, 6 * mm),
              Paragraph("Name: ______________________________________________", normal), Spacer(1, 6 * mm),
              Paragraph("Date: ______________________", normal), Spacer(1, 10 * mm),
              Paragraph("Signature: ______________________________________", normal)]
    return story
