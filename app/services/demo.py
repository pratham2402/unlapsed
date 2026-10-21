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
    TravelRule,
)
from app.services.agent import run_agent
from app.services.extraction import extract_document_from_bytes
from app.services.packet import generate_packet
from app.services.planner import get_active_plan, get_latest_event, required_doc_types
from app.services.verification import (
    get_latest_requirement,
    get_latest_travel_rule,
    seed_fallback_requirements,
)


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
        event_type="auto",
        title="Trip to India",
        destination="India",
        destination_country="India",
        home_location="United States",
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
    event = get_latest_event(session, settings.demo_user_email)
    doc_types = required_doc_types(event, documents) if event else {doc.doc_type for doc in documents}
    requirements = {}
    for doc_type in sorted(doc_types):
        requirement = get_latest_requirement(session, doc_type)
        if requirement is not None:
            requirements[doc_type] = requirement

    holder = next((doc.holder_name for doc in documents if doc.holder_name), "Alex Morgan")
    travel_rule = (
        get_latest_travel_rule(
            session, event.destination_country or event.destination
        )
        if event is not None
        and event.event_type == "trip"
        and (event.destination_country or event.destination)
        else None
    )
    path = generate_packet(
        plan, tasks, documents, requirements, holder, travel_rule=travel_rule
    )

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
        TravelRule,
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
