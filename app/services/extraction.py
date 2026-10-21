from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from app.services.nebius import NebiusClient, NebiusError

ALLOWED_DOC_TYPES = {
    "passport",
    "driver_license",
    "renters_insurance",
    "professional_license",
    "other",
}

DEFAULT_RENEWAL_WINDOWS = {
    "passport": 180,
    "driver_license": 90,
    "renters_insurance": 30,
    "professional_license": 120,
    "other": 90,
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)

LABELED_DATE = re.compile(
    r"(?P<label>Date of Expiration|Expiration Date|Expiry Date|Expires|Valid Until|"
    r"Date of Issue|Issue Date|Issued|Date of Birth|DOB)\s*[:\-]?\s*"
    r"(?P<date>[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.IGNORECASE,
)

NAME_LABELS = ("Name", "Full Name", "Holder", "Named Insured")
ISSUER_LABELS = ("Issuer", "Issued By", "Authority")
NUMBER_LABELS = (
    "Passport No",
    "Passport Number",
    "Document No",
    "Policy No",
    "Policy Number",
    "License No",
    "License Number",
    "DL",
    "ID No",
)


class ExtractionError(RuntimeError):
    pass


class LowTextError(ExtractionError):
    pass


@dataclass
class ExtractedFact:
    key: str
    value: str
    source_page: int | None = None
    source_excerpt: str = ""
    confidence: float = 0.0


@dataclass
class ExtractedDocument:
    doc_type: str
    title: str
    issuer: str = ""
    holder_name: str = ""
    document_number: str = ""
    issue_date: date | None = None
    expiry_date: date | None = None
    renewal_window_days: int = 180
    constraints: dict[str, Any] = field(default_factory=dict)
    facts: list[ExtractedFact] = field(default_factory=list)
    confidence: float = 0.0
    mode: str = "fallback"


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip().strip(".,;")
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def extract_text_from_pdf_bytes(data: bytes) -> list[tuple[int, str]]:
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ExtractionError("This PDF is password protected") from exc

    pages: list[tuple[int, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append((index, text))

    total = sum(len(text.strip()) for _, text in pages)
    if total < 40:
        raise LowTextError(
            "This PDF has almost no selectable text. It looks scanned. "
            "Upload a text-based PDF or enter the details manually."
        )
    return pages


def extract_document_from_bytes(data: bytes, filename: str) -> ExtractedDocument:
    pages = extract_text_from_pdf_bytes(data)
    return extract_document(pages, filename)


def extract_document(pages: list[tuple[int, str]], filename: str) -> ExtractedDocument:
    full_text = "\n\n".join(f"[Page {page_number}]\n{text}" for page_number, text in pages)

    client = NebiusClient()
    if client.available:
        try:
            return _extract_with_model(client, full_text, filename)
        except (NebiusError, ValueError, KeyError):
            # The deterministic extractor is the safety net for the demo.
            pass

    return _extract_with_rules(full_text, filename)


def _extract_with_model(client: NebiusClient, text: str, filename: str) -> ExtractedDocument:
    truncated = text[:24000]
    system = (
        "You are a precise document extraction engine. You return strict JSON only. "
        "Never invent a date, number, or issuer. If a field is not present, use null. "
        "Dates must be ISO format YYYY-MM-DD."
    )
    prompt = f"""
Read the document text and return one JSON object with this shape:

{{
  "doc_type": "passport | driver_license | renters_insurance | professional_license | other",
  "title": "short human title",
  "issuer": "the authority or company that issued it",
  "holder_name": "name on the document",
  "document_number": "document, policy, or license number",
  "issue_date": "YYYY-MM-DD or null",
  "expiry_date": "YYYY-MM-DD or null",
  "renewal_window_days": 180,
  "constraints": {{"min_validity_months_for_entry": 6, "notes": "other constraints"}},
  "facts": [
    {{"key": "expiry_date", "value": "YYYY-MM-DD", "source_page": 1, "source_excerpt": "exact line", "confidence": 0.95}}
  ],
  "confidence": 0.0
}}

Rules:
- Every fact must come from the text. Put the exact supporting line in source_excerpt.
- If the document is a passport, note any six-month validity rule only if it is stated.
- For a professional license, capture continuing education hours if stated.
- renewal_window_days is how early a sensible person should start the renewal.

Filename: {filename}

Document text:
{truncated}
"""
    payload = client.chat_json(prompt, system=system, model=client.fast_model, max_tokens=2500)
    return _normalise_model_payload(payload, text, filename)


def _normalise_model_payload(
    payload: dict[str, Any], text: str, filename: str
) -> ExtractedDocument:
    doc_type = str(payload.get("doc_type") or "other").strip().lower()
    if doc_type not in ALLOWED_DOC_TYPES:
        doc_type = infer_doc_type(text, filename)

    expiry = parse_date(payload.get("expiry_date"))
    issue = parse_date(payload.get("issue_date"))
    if expiry is None:
        expiry = _fallback_labeled_dates(text).get("expiry_date")

    constraints = payload.get("constraints")
    if not isinstance(constraints, dict):
        constraints = {}

    facts: list[ExtractedFact] = []
    for item in payload.get("facts") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if not key or not value:
            continue
        facts.append(
            ExtractedFact(
                key=key,
                value=value,
                source_page=_as_int(item.get("source_page")),
                source_excerpt=str(item.get("source_excerpt") or ""),
                confidence=_clamp(item.get("confidence", 0.5)),
            )
        )

    confidence = _clamp(payload.get("confidence", 0.5))
    if confidence <= 0.0:
        confidence = 0.55 if expiry else 0.35
    if not facts:
        facts = _facts_from_rules(text, doc_type, expiry, issue)

    holder = _normalise_name(str(payload.get("holder_name") or "").strip())
    if not holder:
        holder = _normalise_name(_value_after_label(text, NAME_LABELS))
    issuer = str(payload.get("issuer") or "").strip() or _default_issuer(doc_type)

    return ExtractedDocument(
        doc_type=doc_type,
        title=str(payload.get("title") or _default_title(doc_type)).strip(),
        issuer=issuer,
        holder_name=holder,
        document_number=_redact_number(str(payload.get("document_number") or "")),
        issue_date=issue,
        expiry_date=expiry,
        renewal_window_days=_as_int(payload.get("renewal_window_days"))
        or DEFAULT_RENEWAL_WINDOWS.get(doc_type, 90),
        constraints=constraints,
        facts=facts,
        confidence=confidence,
        mode="live",
    )


def _extract_with_rules(text: str, filename: str) -> ExtractedDocument:
    doc_type = infer_doc_type(text, filename)
    dates = _fallback_labeled_dates(text)
    expiry = dates.get("expiry_date")
    issue = dates.get("issue_date")

    holder = _value_after_label(text, NAME_LABELS)
    if not holder and doc_type == "passport":
        given = _value_after_label(text, ("Given Names",))
        surname = _value_after_label(text, ("Surname",))
        holder = " ".join(part for part in (given, surname) if part).strip()
    holder = _normalise_name(holder)
    issuer = _value_after_label(text, ISSUER_LABELS) or _default_issuer(doc_type)
    number = _redact_number(_value_after_label(text, NUMBER_LABELS))

    constraints: dict[str, Any] = {}
    if doc_type == "passport":
        constraints["min_validity_months_for_entry"] = 6
        constraints["notes"] = "Many countries require six months of validity beyond your return date."
    elif doc_type == "professional_license":
        constraints["continuing_education_hours"] = 30
        constraints["notes"] = "Confirm the current continuing education requirement with the board."

    confidence = 0.35
    if expiry:
        confidence += 0.25
    if holder:
        confidence += 0.1
    if number:
        confidence += 0.1
    if doc_type != "other":
        confidence += 0.1

    facts = _facts_from_rules(text, doc_type, expiry, issue)
    return ExtractedDocument(
        doc_type=doc_type,
        title=_default_title(doc_type),
        issuer=issuer,
        holder_name=holder,
        document_number=number,
        issue_date=issue,
        expiry_date=expiry,
        renewal_window_days=DEFAULT_RENEWAL_WINDOWS.get(doc_type, 90),
        constraints=constraints,
        facts=facts,
        confidence=round(min(confidence, 0.85), 2),
        mode="fallback",
    )


def infer_doc_type(text: str, filename: str = "") -> str:
    haystack = f"{filename}\n{text}".lower()
    if "passport" in haystack:
        return "passport"
    if ("driver" in haystack and "license" in haystack) or "driver license" in haystack:
        return "driver_license"
    if "insurance" in haystack and ("policy" in haystack or "renters" in haystack):
        return "renters_insurance"
    if ("professional" in haystack and "license" in haystack) or (
        "board" in haystack and "license" in haystack
    ):
        return "professional_license"
    return "other"


def _fallback_labeled_dates(text: str) -> dict[str, date | None]:
    found: dict[str, date | None] = {"expiry_date": None, "issue_date": None, "date_of_birth": None}
    for match in LABELED_DATE.finditer(text):
        label = match.group("label").lower()
        value = parse_date(match.group("date"))
        if value is None:
            continue
        if "expir" in label or "valid until" in label:
            found["expiry_date"] = value
        elif "issue" in label or label == "issued":
            found["issue_date"] = value
        elif "birth" in label or label == "dob":
            found["date_of_birth"] = value
    return found


def _facts_from_rules(
    text: str, doc_type: str, expiry: date | None, issue: date | None
) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    if expiry:
        excerpt = _excerpt_for_value(text, expiry.isoformat()) or _excerpt_for_value(
            text, expiry.strftime("%B %d, %Y")
        )
        facts.append(
            ExtractedFact(
                key="expiry_date",
                value=expiry.isoformat(),
                source_page=1,
                source_excerpt=excerpt,
                confidence=0.8,
            )
        )
    if issue:
        facts.append(
            ExtractedFact(
                key="issue_date",
                value=issue.isoformat(),
                source_page=1,
                source_excerpt=_excerpt_for_value(text, issue.isoformat()),
                confidence=0.75,
            )
        )
    facts.append(
        ExtractedFact(
            key="doc_type",
            value=doc_type,
            source_page=1,
            source_excerpt=_line_containing(text, doc_type.replace("_", " ")),
            confidence=0.7,
        )
    )
    return facts


def _normalise_name(value: str) -> str:
    text = " ".join((value or "").split())
    if "," in text:
        surname, given = [part.strip() for part in text.split(",", 1)]
        if surname and given:
            return f"{given} {surname}"
    return text


def _value_after_label(text: str, labels: tuple[str, ...]) -> str:
    """Read a value that may be on the same line or the next line.

    PDF extraction often puts a table label and its value on separate lines.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        for label in labels:
            same_line = re.match(
                rf"^{re.escape(label)}\s*[:\-]\s*(.+)$", stripped, re.IGNORECASE
            )
            if same_line:
                return same_line.group(1).strip()
            if stripped.lower() == label.lower():
                parts: list[str] = []
                for next_index in range(index + 1, min(index + 4, len(lines))):
                    value = lines[next_index].strip()
                    if not value:
                        if parts:
                            break
                        continue
                    parts.append(value)
                    if not value.endswith(","):
                        break
                if parts:
                    return " ".join(parts)
    return ""


def _excerpt_for_value(text: str, value: str) -> str:
    """Include the label line when a table puts labels and values on separate rows."""
    if not value:
        return ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if value.lower() in line.lower():
            current = line.strip()
            if index > 0:
                previous = lines[index - 1].strip()
                if previous and len(previous) < 40 and not any(ch.isdigit() for ch in previous):
                    return f"{previous}: {current}"[:240]
            return current[:240]
    return ""


def _line_containing(text: str, needle: str) -> str:
    if not needle:
        return ""
    needle = needle.lower()
    for line in text.splitlines():
        if needle in line.lower():
            return line.strip()[:240]
    return ""


def _default_title(doc_type: str) -> str:
    return {
        "passport": "Passport",
        "driver_license": "Driver's license",
        "renters_insurance": "Renters insurance policy",
        "professional_license": "Professional license",
        "other": "Document",
    }.get(doc_type, "Document")


def _default_issuer(doc_type: str) -> str:
    return {
        "passport": "United States Department of State",
        "driver_license": "State DMV",
        "renters_insurance": "Insurance provider",
        "professional_license": "State licensing board",
    }.get(doc_type, "")


def _redact_number(value: str) -> str:
    cleaned = re.sub(r"\s+", "", value or "")
    if not cleaned:
        return ""
    return f"••••{cleaned[-4:]}"


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
