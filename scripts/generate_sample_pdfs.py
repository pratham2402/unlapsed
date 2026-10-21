"""Generate the fictional, text-based PDFs used by the demo.

These are deliberately clean text PDFs, not photographed documents. The demo
uses the same pypdf path a real upload would use, but avoids OCR entirely.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import get_settings

INK = colors.HexColor("#141414")
MUTED = colors.HexColor("#6B665D")
RULE = colors.HexColor("#D8D2C4")
PAPER = colors.HexColor("#F4F1EA")


def main() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    samples = settings.samples_dir

    _write_document(
        samples / "passport_alex_morgan.pdf",
        "UNITED STATES OF AMERICA",
        "PASSPORT",
        [
            ("Type", "P"),
            ("Code", "USA"),
            ("Passport No", "512345678"),
            ("Surname", "MORGAN"),
            ("Given Names", "ALEX"),
            ("Nationality", "UNITED STATES OF AMERICA"),
            ("Date of Birth", "1990-04-12"),
            ("Place of Birth", "AUSTIN, TX"),
            ("Date of Issue", "2017-06-15"),
            ("Date of Expiration", "2027-06-14"),
            ("Authority", "U.S. DEPARTMENT OF STATE"),
        ],
        "This is a fictional sample document created for the Unlapsed demo. It is not a real passport.",
    )

    _write_document(
        samples / "drivers_license_alex_morgan.pdf",
        "STATE OF CALIFORNIA",
        "DRIVER LICENSE",
        [
            ("Name", "Alex Morgan"),
            ("DL", "D1234567"),
            ("DOB", "1990-04-12"),
            ("Address", "482 Clement St, San Francisco, CA 94118"),
            ("Class", "C"),
            ("Issue Date", "2022-08-01"),
            ("Expiration Date", "2028-04-12"),
            ("Issuer", "California Department of Motor Vehicles"),
        ],
        "This is a fictional sample document created for the Unlapsed demo. It is not a real license.",
    )

    _write_document(
        samples / "renters_insurance_alex_morgan.pdf",
        "NORTHSTAR MUTUAL",
        "RENTERS INSURANCE POLICY",
        [
            ("Named Insured", "Alex Morgan"),
            ("Policy No", "RM-88421"),
            ("Property Address", "482 Clement St, San Francisco, CA 94118"),
            ("Effective Date", "2026-01-01"),
            ("Expiration Date", "2027-01-01"),
            ("Personal Liability", "$300,000"),
            ("Personal Property", "$30,000"),
            ("Deductible", "$500"),
            ("Issuer", "Northstar Mutual Insurance Company"),
        ],
        "This is a fictional sample policy created for the Unlapsed demo. It is not a real insurance policy.",
    )

    _write_document(
        samples / "professional_license_alex_morgan.pdf",
        "STATE OF CALIFORNIA",
        "PROFESSIONAL ENGINEER LICENSE",
        [
            ("Name", "Alex Morgan"),
            ("License No", "PE-12345"),
            ("License Type", "Professional Engineer, Mechanical"),
            ("Issue Date", "2020-04-01"),
            ("Expiration Date", "2027-03-31"),
            ("Continuing Education", "30 professional development hours per renewal"),
            ("Issuer", "California Board for Professional Engineers, Land Surveyors, and Geologists"),
        ],
        "This is a fictional sample license created for the Unlapsed demo. It is not a real license.",
    )

    print(f"Wrote sample documents to {samples}")


def _write_document(
    path: Path,
    header: str,
    title: str,
    fields: list[tuple[str, str]],
    disclaimer: str,
) -> None:
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "Header",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=INK,
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceAfter=18,
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=MUTED,
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=INK,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=7,
        leading=9,
        textColor=MUTED,
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.8 * inch,
        title=f"SAMPLE {title}",
        author="Unlapsed demo",
    )

    rows = [[Paragraph(label, label_style), Paragraph(value, value_style)] for label, value in fields]
    table = Table(rows, colWidths=[2.1 * inch, 4.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), PAPER),
                ("GRID", (0, 0), (-1, -1), 0.35, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    story = [
        Paragraph(header, header_style),
        Paragraph(title, title_style),
        table,
        Spacer(1, 22),
        Paragraph(disclaimer, disclaimer_style),
    ]
    doc.build(story)


if __name__ == "__main__":
    main()
