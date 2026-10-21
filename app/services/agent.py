from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models import (
    Activity,
    AgentRun,
    Document,
    Event,
    Packet,
    PlanTask,
    Requirement,
)
from app.services.event_analysis import infer_event
from app.services.packet import generate_packet
from app.services.planner import (
    build_event_plan,
    get_active_plan,
    get_latest_event,
    persist_plan,
    required_doc_types,
)
from app.services.verification import (
    get_latest_requirement,
    get_latest_travel_rule,
    seed_fallback_requirements,
    upsert_requirement,
    upsert_travel_rule,
    verify_requirement,
    verify_travel_rule,
)
from app.services.tavily import TavilySearch


def run_agent(
    session: Session,
    user_id: str,
    run_type: str = "manual",
) -> AgentRun:
    run = AgentRun(user_id=user_id, run_type=run_type, status="running")
    session.add(run)
    session.commit()
    session.refresh(run)

    _log(session, user_id, run.id, "run", f"Agent run started ({run_type}).")

    try:
        _execute(session, user_id, run)
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        session.refresh(run)
        _log(session, user_id, run.id, "run", "Agent run finished.")
        return run
    except Exception as exc:
        run.status = "error"
        run.finished_at = datetime.now(timezone.utc)
        run.summary = f"Run failed: {exc}"
        session.add(run)
        session.commit()
        session.refresh(run)
        _log(session, user_id, run.id, "error", f"Run failed: {exc}")
        return run


def _execute(
    session: Session,
    user_id: str,
    run: AgentRun,
) -> None:
    documents = session.exec(select(Document).where(Document.user_id == user_id)).all()
    event = get_latest_event(session, user_id)

    if not documents:
        run.summary = "No documents yet. Upload a text PDF to start."
        plan = get_active_plan(session, user_id)
        if plan:
            plan.summary = run.summary
            plan.risk_total_cents = 0
            session.add(plan)
            for task in session.exec(
                select(PlanTask).where(PlanTask.plan_id == plan.id)
            ).all():
                session.delete(task)
            for packet in session.exec(
                select(Packet).where(Packet.plan_id == plan.id)
            ).all():
                session.delete(packet)
            session.commit()
        return

    if event is None:
        event = Event(
            user_id=user_id,
            event_type="watch",
            title="Document watch",
        )
        session.add(event)
        session.commit()
        session.refresh(event)

    analysis = None
    travel_rule = None
    if event.event_type != "watch":
        analysis = infer_event(documents, event)
        _apply_analysis(session, event, analysis)
        travel_destination = event.destination_country or event.destination
        if event.event_type == "trip" and travel_destination:
            previous_rule = get_latest_travel_rule(session, travel_destination)
            travel_data = verify_travel_rule(travel_destination, event.home_location)
            if (
                previous_rule is not None
                and previous_rule.status == "verified"
                and travel_data.status == "needs_manual_check"
            ):
                travel_rule = previous_rule
            else:
                travel_rule = upsert_travel_rule(session, travel_data)

    requirements, changes = _refresh_requirements(session, documents, event)
    draft = build_event_plan(
        documents,
        requirements,
        event,
        travel_rule=travel_rule,
    )
    plan = persist_plan(session, user_id, event, draft)

    holder = next((doc.holder_name for doc in documents if doc.holder_name), "Alex Morgan")
    packet_path = generate_packet(
        plan,
        _tasks_for(session, plan.id),
        documents,
        requirements,
        holder,
        travel_rule=travel_rule,
    )
    _replace_packet_record(session, plan.id, packet_path.name)

    if event.event_type == "watch":
        run.summary = (
            f"Built an expiry watch, checked {len(requirements)} official "
            f"requirements, rebuilt {len(draft.tasks)} tasks, and generated the packet."
        )
    else:
        run.summary = (
            f"Read the event as {event.event_type.replace('_', ' ')}, checked "
            f"{len(requirements)} official requirements, rebuilt "
            f"{len(draft.tasks)} tasks, and generated the renewal packet."
        )
    run.changes_json = _json_changes(changes)
    run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()

    if analysis is not None:
        _log(
            session,
            user_id,
            run.id,
            "event",
            f"Read the event as {analysis.event_type.replace('_', ' ')} "
            f"({analysis.confidence:.0%} confidence): {analysis.reason}",
        )
    if travel_rule is not None:
        _log(
            session,
            user_id,
            run.id,
            "travel_rule",
            f"Passport validity rule: {travel_rule.status} "
            f"via {travel_rule.source_url or 'manual verification'}.",
        )
    for doc_type, requirement in requirements.items():
        source = requirement.official_url or "manual verification"
        _log(
            session,
            user_id,
            run.id,
            "verification",
            f"{doc_type.replace('_', ' ').title()}: {requirement.status} via {source}.",
        )
    _log(
        session,
        user_id,
        run.id,
        "plan",
        f"Plan rebuilt for {event.title}: {len(draft.tasks)} tasks.",
    )
    _log(session, user_id, run.id, "packet", f"Packet generated: {packet_path.name}.")
    for change in changes:
        _log(session, user_id, run.id, "change", change)


def _refresh_requirements(
    session: Session,
    documents: list[Document],
    event: Event,
) -> tuple[dict[str, Requirement], list[str]]:
    doc_types = required_doc_types(event, documents)

    live_search = TavilySearch().available
    if not live_search:
        seed_fallback_requirements(session)

    requirements: dict[str, Requirement] = {}
    changes: list[str] = []

    for doc_type in sorted(doc_types):
        previous = get_latest_requirement(session, doc_type)
        if live_search:
            issuer = next(
                (doc.issuer for doc in documents if doc.doc_type == doc_type and doc.issuer),
                "",
            )
            data = verify_requirement(doc_type, issuer=issuer)
            if previous and data.status == "needs_manual_check" and previous.status == "verified":
                # Keep the last verified result rather than replacing it with a failed lookup.
                requirement = previous
            else:
                requirement = upsert_requirement(session, data)
            if previous and previous.id != requirement.id:
                changes.extend(_diff_requirements(previous, requirement))
        else:
            requirement = previous
            if requirement is None:
                requirement = get_latest_requirement(session, doc_type)
        if requirement is not None:
            requirements[doc_type] = requirement

    return requirements, changes


def _diff_requirements(previous: Requirement, current: Requirement) -> list[str]:
    changes: list[str] = []
    label = current.title
    if previous.fee and current.fee and previous.fee != current.fee:
        changes.append(f"{label}: fee changed from '{previous.fee}' to '{current.fee}'.")
    if (
        previous.processing_time
        and current.processing_time
        and previous.processing_time != current.processing_time
    ):
        changes.append(
            f"{label}: processing time changed from '{previous.processing_time}' "
            f"to '{current.processing_time}'."
        )
    if previous.official_url and current.official_url and previous.official_url != current.official_url:
        changes.append(f"{label}: official source changed.")
    return changes


def _tasks_for(session: Session, plan_id: int | None):
    from app.models import PlanTask

    if plan_id is None:
        return []
    return session.exec(
        select(PlanTask).where(PlanTask.plan_id == plan_id).order_by(PlanTask.sort_order)
    ).all()


def _replace_packet_record(session: Session, plan_id: int, filename: str) -> None:
    existing = session.exec(select(Packet).where(Packet.plan_id == plan_id)).all()
    for packet in existing:
        session.delete(packet)
    session.add(Packet(plan_id=plan_id, filename=filename))
    session.commit()


def _apply_analysis(session: Session, event: Event, analysis) -> None:
    import json

    event.event_type = analysis.event_type
    event.home_location = analysis.home_location
    event.destination_country = analysis.destination_country
    event.is_international = analysis.is_international
    event.event_type_confidence = analysis.confidence
    event.event_type_reason = analysis.reason
    event.relevant_docs_json = json.dumps(analysis.relevant_document_types)
    session.add(event)
    session.commit()
    session.refresh(event)


def _json_changes(changes: list[str]) -> str:
    import json

    return json.dumps(changes)


def _log(
    session: Session,
    user_id: str,
    run_id: int | None,
    kind: str,
    message: str,
) -> None:
    session.add(
        Activity(
            user_id=user_id,
            run_id=run_id,
            kind=kind,
            message=message,
        )
    )
    session.commit()
