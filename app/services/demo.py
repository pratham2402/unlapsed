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
from app.services.packet import generate_packet
from app.services.planner import get_active_plan
from app.services.verification import get_latest_requirement, seed_fallback_requirements


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


def ensure_packet(session: Session) -> None:
    """Regenerate the packet from the stored plan without any model calls.

    The public demo ships with a pre-seeded database and an empty generated
    directory. This keeps the download working after a container restart.
    """
    settings = get_settings()
    plan = get_active_plan(session, settings.demo_user_email)
    if plan is None:
        return

    tasks = session.exec(
        select(PlanTask).where(PlanTask.plan_id == plan.id).order_by(PlanTask.sort_order)
    ).all()
    documents = session.exec(
        select(Document).where(Document.user_id == settings.demo_user_email)
    ).all()
    requirements = {}
    for doc_type in (
        "passport",
        "india_evisa",
        "driver_license",
        "professional_license",
        "renters_insurance",
    ):
        requirement = get_latest_requirement(session, doc_type)
        if requirement is not None:
            requirements[doc_type] = requirement

    holder = next((doc.holder_name for doc in documents if doc.holder_name), "Alex Morgan")
    path = generate_packet(plan, tasks, documents, requirements, holder)

    existing = session.exec(select(Packet).where(Packet.plan_id == plan.id)).all()
    for packet in existing:
        session.delete(packet)
    session.add(Packet(plan_id=plan.id, filename=path.name))
    session.commit()


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
