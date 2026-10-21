from datetime import date
from pathlib import Path

import pytest

from app.services.extraction import extract_document_from_bytes, parse_date

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples() -> None:
    if not (SAMPLES / "passport_alex_morgan.pdf").exists():
        from scripts.generate_sample_pdfs import main

        main()


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    """Keep tests deterministic and token-free."""

    class NoModel:
        available = False

    monkeypatch.setattr("app.services.extraction.NebiusClient", lambda: NoModel())


def test_parse_date_formats() -> None:
    assert parse_date("2027-06-14") == date(2027, 6, 14)
    assert parse_date("14 June 2027") == date(2027, 6, 14)
    assert parse_date("June 14, 2027") == date(2027, 6, 14)
    assert parse_date("06/14/2027") == date(2027, 6, 14)
    assert parse_date("not a date") is None


def test_sample_passport_extraction() -> None:
    data = (SAMPLES / "passport_alex_morgan.pdf").read_bytes()
    document = extract_document_from_bytes(data, "passport_alex_morgan.pdf")
    assert document.doc_type == "passport"
    assert document.expiry_date == date(2027, 6, 14)
    assert document.issue_date == date(2017, 6, 15)
    assert document.holder_name == "ALEX MORGAN"
    assert document.document_number == "••••5678"
    assert document.confidence >= 0.8


def test_sample_documents_are_classified() -> None:
    expected = {
        "drivers_license_alex_morgan.pdf": "driver_license",
        "renters_insurance_alex_morgan.pdf": "renters_insurance",
        "professional_license_alex_morgan.pdf": "professional_license",
    }
    for filename, doc_type in expected.items():
        data = (SAMPLES / filename).read_bytes()
        document = extract_document_from_bytes(data, filename)
        assert document.doc_type == doc_type, filename
        assert document.expiry_date is not None, filename


def test_low_text_pdf_is_rejected() -> None:
    from reportlab.pdfgen import canvas
    from io import BytesIO

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(100, 700, " ")
    pdf.save()

    with pytest.raises(Exception):
        extract_document_from_bytes(buffer.getvalue(), "blank.pdf")
