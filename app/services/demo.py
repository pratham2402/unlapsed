from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlmodel import Session, select

from app.config import get_settings
from app.models import (
    Activity,
    AgentRun,
    Document,
    Event,
    Fact,
    Packet,
    Plan,
    PlanTask,
    Requirement,
)
from app.services.agent import run_agent
from app.services.extraction import extract_document_from_bytes
from app.services.verification import seed_fallback_requirements


def seed_demo(session: Session, reset: bool = True) -> Plan | None:
    settings = get_settings()
    settings.ensure_dirs()

    if reset:
        _clear(session)

    _ensure_samples(settings.samples_dir)

    user_id = settings.demo_user_email
    documents = []
    for filename in (
        "passport_alex_morgan.pdf",
        "drivers_license_alex_morgan.pdf",
        "renters_insurance_alex_morgan.pdf",
        "professional_license_alex_morgan.pdf",
    ):
        path = settings.samples_dir / filename
        extracted = extract_document_from_bytes(path.read_bytes(), filename)
        document = Document(
            user_id=user_id,
            doc_type=extracted.doc_type,
            title=extracted.title,
            issuer=extracted.issuer,
            holder_name=extracted.holder_name,
            document_number_last4=extracted.document_number,
            issue_date=extracted.issue_date,
            expiry_date=extracted.expiry_date,
            renewal_window_days=extracted.renewal_window_days,
            constraints_json=json.dumps(extracted.constraints),
            source_filename=filename,
            extraction_confidence=extracted.confidence,
            extraction_mode=extracted.mode,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        documents.append(document)

        for fact in extracted.facts:
            session.add(
                Fact(
                    document_id=document.id,
                    key=fact.key,
                    value=fact.value,
                    source_page=fact.source_page,
                    source_excerpt=fact.source_excerpt,
                    confidence=fact.confidence,
                )
            )
        session.commit()

    event = Event(
        user_id=user_id,
        event_type="trip",
        title="Trip to India",
        destination="India",
        depart_date=date(2027, 1, 10),
        return_date=date(2027, 1, 24),
        notes="Two-week trip. Non-refundable flights and hotels.",
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    if not settings.live_tavily:
        seed_fallback_requirements(session)
    run_agent(session, user_id, run_type="manual")

    statement = (
        select(Plan)
        .where(Plan.user_id == user_id)
        .order_by(Plan.updated_at.desc(), Plan.id.desc())
    )
    return session.exec(statement).first()


def _clear(session: Session) -> None:
    for model in (
        Packet,
        PlanTask,
        Plan,
        Activity,
        AgentRun,
        Requirement,
        Fact,
        Document,
        Event,
    ):
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


def _ensure_samples(samples_dir: Path) -> None:
    required = {
        "passport_alex_morgan.pdf",
        "drivers_license_alex_morgan.pdf",
        "renters_insurance_alex_morgan.pdf",
        "professional_license_alex_morgan.pdf",
    }
    present = {path.name for path in samples_dir.glob("*.pdf")}
    if not required.issubset(present):
        from scripts.generate_sample_pdfs import main as generate

        generate()
