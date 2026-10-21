from __future__ import annotations

import html
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import get_settings
from app.models import Document, Plan, PlanTask, Requirement, TravelRule

INK = colors.HexColor("#141414")
PAPER = colors.HexColor("#F4F1EA")
SIGNAL = colors.HexColor("#D64B2A")
VERIFIED = colors.HexColor("#1F6F4A")
MUTED = colors.HexColor("#6B665D")
RULE = colors.HexColor("#D8D2C4")


def generate_packet(
    plan: Plan,
    tasks: list[PlanTask],
    documents: list[Document],
    requirements: dict[str, Requirement],
    holder_name: str = "Alex Morgan",
    travel_rule: TravelRule | None = None,
) -> Path:
    settings = get_settings()
    settings.ensure_dirs()
    path = settings.generated_dir / f"plan_{plan.id}_packet.pdf"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=f"Unlapsed packet: {plan.title}",
        author="Unlapsed",
    )

    styles = _styles()
    story = []
    story.append(Paragraph("UNLAPSED", styles["brand"]))
    story.append(Paragraph("Renewal packet", styles["title"]))
    story.append(Paragraph(_text(plan.title), styles["subtitle"]))
    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')} "
            f"for {_text(holder_name)}. This packet is a planning aid, not legal advice.",
            styles["small"],
        )
    )
    story.append(Spacer(1, 16))

    story.append(Paragraph("What is at stake", styles["h2"]))
    story.append(Paragraph(_text(plan.summary), styles["body"]))
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            "Official renewal fees are shown in the evidence table. "
            "This packet does not estimate trip losses or other personal costs.",
            styles["small"],
        )
    )
    story.append(PageBreak())

    story.append(Paragraph("Plan at a glance", styles["h2"]))
    critical_rows = [["Task", "Start", "Due", "Blocking"]]
    for task in tasks:
        critical_rows.append(
            [
                Paragraph(_text(task.title), styles["cell"]),
                _date(task.start_date),
                _date(task.due_date),
                "Yes" if task.blocking else "No",
            ]
        )
    story.append(_table(critical_rows, styles, widths=[3.2 * inch, 1.2 * inch, 1.2 * inch, 1.0 * inch]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Official evidence", styles["h2"]))
    ordered_requirements = sorted(requirements.values(), key=_requirement_order)
    evidence_rows = [["Requirement", "Fee", "Processing", "Source", "Checked"]]
    for requirement in ordered_requirements:
        evidence_rows.append(
            [
                Paragraph(_text(requirement.title), styles["cell"]),
                Paragraph(_text(requirement.fee or "Not stated"), styles["cell"]),
                Paragraph(_text(requirement.processing_time or "Not stated"), styles["cell"]),
                Paragraph(
                    f'<link href="{html.escape(requirement.official_url, quote=True)}" color="#1F6F4A">{_text(_short_url(requirement.official_url))}</link>'
                    if requirement.official_url
                    else "Manual verification required",
                    styles["cell"],
                ),
                _date(requirement.checked_at.date()),
            ]
        )
    story.append(_table(evidence_rows, styles, widths=[1.4 * inch, 1.2 * inch, 1.5 * inch, 1.7 * inch, 0.95 * inch]))
    if travel_rule is not None:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Passport validity rule", styles["h3"]))
        if travel_rule.passport_validity_months is None:
            rule_value = "No verified period. Verify it manually."
        elif travel_rule.passport_validity_months == 0:
            rule_value = "Valid through the return date. No extra months."
        else:
            rule_value = f"{travel_rule.passport_validity_months} months beyond the return date."
        if travel_rule.status != "verified":
            rule_value += " Needs manual confirmation."
        story.append(
            Paragraph(
                f"{_text(travel_rule.rule_text)} {_text(rule_value)}",
                styles["body"],
            )
        )
        if travel_rule.visa_required is True:
            visa_value = travel_rule.visa_type or "Visa required"
        elif travel_rule.visa_required is False:
            visa_value = "No visa required according to the source."
        else:
            visa_value = "Visa requirement not stated in the source."
        story.append(
            Paragraph(
                f"Visa: {_text(visa_value)} {_text(travel_rule.visa_rule_text)}",
                styles["small"],
            )
        )
        if travel_rule.source_url:
            story.append(
                Paragraph(
                    f'Source: <link href="{html.escape(travel_rule.source_url, quote=True)}" color="#1F6F4A">'
                    f"{_text(_short_url(travel_rule.source_url))}</link>, checked "
                    f"{_date(travel_rule.checked_at.date())}.",
                    styles["small"],
                )
            )
        else:
            story.append(
                Paragraph(
                    "No official source stored. Verify this rule manually.",
                    styles["small"],
                )
            )
    story.append(PageBreak())

    story.append(Paragraph("Form-ready data", styles["h2"]))
    document_rows = [["Document", "Holder", "Issuer", "Expires", "Number"]]
    for document in documents:
        document_rows.append(
            [
                Paragraph(_text(document.title), styles["cell"]),
                Paragraph(_text(document.holder_name or holder_name), styles["cell"]),
                Paragraph(_text(document.issuer or ""), styles["cell"]),
                _date(document.expiry_date),
                (document.document_number_last4 or "").replace("•", "*"),
            ]
        )
    story.append(_table(document_rows, styles, widths=[1.3 * inch, 1.3 * inch, 2.1 * inch, 0.95 * inch, 0.95 * inch]))
    story.append(Spacer(1, 12))

    renewal_document_ids = {task.document_id for task in tasks if task.document_id is not None}
    cover_documents = [document for document in documents if document.id in renewal_document_ids]
    if cover_documents:
        story.append(PageBreak())
        story.append(Paragraph("Cover letters", styles["h2"]))
        story.append(
            Paragraph(
                "Each renewal goes to a different office. Use the matching letter below. "
                "Online applications do not need a mailed cover letter.",
                styles["small"],
            )
        )
        story.append(Spacer(1, 8))
        for document in cover_documents:
            issuer = document.issuer or "the issuing office"
            number = (document.document_number_last4 or "").replace("•", "*")
            number_text = f" (last four: {number})" if number else ""
            story.append(
                KeepTogether(
                    [
                        Paragraph(_text(document.title), styles["h3"]),
                        Paragraph(
                            f"To: {_text(issuer)}<br/><br/>"
                            f"I am submitting my renewal application for my {_text(document.title)}"
                            f"{_text(number_text)}. I have attached the required supporting documents "
                            f"and the current fee. Please contact me if any item is missing or needs "
                            f"correction.<br/><br/>"
                            f"Sincerely,<br/>{_text(holder_name)}",
                            styles["body"],
                        ),
                        Spacer(1, 8),
                    ]
                )
            )
    story.append(PageBreak())

    story.append(Paragraph("Checklist", styles["h2"]))
    for index, task in enumerate(tasks, start=1):
        story.append(
            KeepTogether(
                [
                    Paragraph(f"{index}. {_text(task.title)}", styles["h3"]),
                    Paragraph(_text(task.description), styles["body"]),
                    Paragraph(
                        f"{task.phase} · due {_date(task.due_date)} · "
                        f"{'blocking' if task.blocking else 'parallel'}",
                        styles["small"],
                    ),
                    Spacer(1, 8),
                ]
            )
        )

    story.append(Spacer(1, 12))
    story.append(Paragraph("Sources", styles["h2"]))
    for requirement in ordered_requirements:
        if requirement.official_url:
            story.append(
                Paragraph(
                    f'<link href="{html.escape(requirement.official_url, quote=True)}" color="#1F6F4A">{_text(requirement.title)}</link> '
                    f"({_text(_short_url(requirement.official_url))}), checked {_date(requirement.checked_at.date())}.",
                    styles["small"],
                )
            )
        else:
            story.append(
                Paragraph(
                    f"{_text(requirement.title)}: no official public source found. Verify manually.",
                    styles["small"],
                )
            )

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path


def generate_calendar(plan: Plan, tasks: list[PlanTask]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Unlapsed//Document Renewal Planner//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape_text(plan.title)}",
    ]
    for task in tasks:
        if not task.due_date:
            continue
        start = task.start_date or task.due_date
        end = task.due_date
        description = (
            f"{task.description} Priority: {task.priority}. "
            f"{'Blocks the event' if task.blocking else 'Parallel risk'}."
        )
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:unlapsed-{plan.id}-{task.id}@unlapsed.local",
                f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(end).strftime('%Y%m%d')}",
                f"SUMMARY:{_escape_text(task.title)}",
                f"DESCRIPTION:{_escape_text(description)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "Brand",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=SIGNAL,
            spaceAfter=2,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=27,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=15,
            textColor=MUTED,
            spaceAfter=2,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=INK,
            spaceBefore=6,
            spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=INK,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.6,
            leading=13.5,
            textColor=INK,
            spaceAfter=4,
        ),
        "cell": ParagraphStyle(
            "Cell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.8,
            leading=10,
            textColor=MUTED,
        ),
    }


def _table(rows: list[list], styles: dict[str, ParagraphStyle], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PAPER),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7.5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("TOPPADDING", (0, 0), (-1, 0), 5),
                ("GRID", (0, 0), (-1, -1), 0.35, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
            ]
        )
    )
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.7 * inch, 0.4 * inch, "Unlapsed · planning aid, not legal advice")
    canvas.drawRightString(letter[0] - 0.7 * inch, 0.4 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _text(value: object) -> str:
    return html.escape(str(value or ""))


def _date(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value else "Not set"


def _short_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    display = (parsed.netloc + parsed.path).rstrip("/")
    if len(display) > 42:
        return parsed.netloc
    return display


def _requirement_order(requirement: Requirement) -> int:
    order = {
        "passport": 0,
        "india_evisa": 1,
        "driver_license": 2,
        "professional_license": 3,
        "renters_insurance": 4,
    }
    return order.get(requirement.doc_type, 99)


def _escape_text(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
