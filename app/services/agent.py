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
from app.services.packet import generate_packet
from app.services.planner import (
    build_trip_plan,
    get_active_plan,
    get_latest_event,
    persist_plan,
)
from app.services.verification import (
    get_latest_requirement,
    seed_fallback_requirements,
    upsert_requirement,
    verify_requirement,
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

    requirements, changes = _refresh_requirements(session, documents, event)
    draft = build_trip_plan(documents, requirements, event)
    plan = persist_plan(session, user_id, event, draft)

    holder = next((doc.holder_name for doc in documents if doc.holder_name), "Alex Morgan")
    packet_path = generate_packet(plan, _tasks_for(session, plan.id), documents, requirements, holder)
    _replace_packet_record(session, plan.id, packet_path.name)

    run.summary = (
        f"Checked {len(requirements)} official requirements, rebuilt "
        f"{len(draft.tasks)} tasks, and generated the renewal packet."
    )
    run.changes_json = _json_changes(changes)
    run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()

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
        f"Plan rebuilt for {event.title}: {len(draft.tasks)} tasks, ${draft.risk_total_cents / 100:,.0f} at risk.",
    )
    _log(session, user_id, run.id, "packet", f"Packet generated: {packet_path.name}.")
    for change in changes:
        _log(session, user_id, run.id, "change", change)


def _refresh_requirements(
    session: Session,
    documents: list[Document],
    event: Event,
) -> tuple[dict[str, Requirement], list[str]]:
    doc_types = {doc.doc_type for doc in documents}
    doc_types.update({"passport", "driver_license", "professional_license", "renters_insurance"})
    if _needs_visa(event.destination):
        doc_types.add("india_evisa")
    doc_types.discard("other")

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


def _needs_visa(destination: str | None) -> bool:
    text = (destination or "").lower()
    return any(country in text for country in ("india", "china", "brazil", "vietnam"))


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
