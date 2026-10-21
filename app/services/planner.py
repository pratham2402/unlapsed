from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import Document, Event, Plan, PlanTask, Requirement

TRIP_COST_CENTS = 240_000
INSURANCE_GAP_CENTS = 150_000
LICENSE_LAPSE_CENTS = 380_000

PASSPORT_STANDARD_DAYS = 56
PASSPORT_EXPEDITED_DAYS = 21
E_VISA_BUFFER_DAYS = 14


@dataclass
class TaskDraft:
    key: str
    title: str
    description: str
    phase: str
    start_date: date | None
    due_date: date | None
    depends_on: list[str] = field(default_factory=list)
    blocking: bool = True
    cost_of_missing_cents: int = 0
    priority: str = "medium"
    evidence_url: str = ""
    document_id: int | None = None
    sort_order: int = 0


@dataclass
class PlanDraft:
    title: str
    summary: str
    tasks: list[TaskDraft]
    risk_total_cents: int
    critical_path: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_trip_plan(
    documents: list[Document],
    requirements: dict[str, Requirement],
    event: Event,
    trip_cost_cents: int = TRIP_COST_CENTS,
) -> PlanDraft:
    today = date.today()
    depart = event.depart_date or (today + timedelta(days=90))
    return_date = event.return_date or (depart + timedelta(days=14))
    required_validity = add_months(return_date, 6)

    passport = latest_doc(documents, "passport")
    insurance = latest_doc(documents, "renters_insurance")
    professional = latest_doc(documents, "professional_license")
    driver_license = latest_doc(documents, "driver_license")

    passport_req = requirements.get("passport")
    evisa_req = requirements.get("india_evisa")
    insurance_req = requirements.get("renters_insurance")
    professional_req = requirements.get("professional_license")

    warnings: list[str] = []
    tasks: list[TaskDraft] = []
    summary_lines: list[str] = []
    sort = 0

    passport_needs_renewal = (
        passport is None
        or passport.expiry_date is None
        or passport.expiry_date < required_validity
    )

    passport_done: date | None = None
    expedited_recommended = False

    if passport_needs_renewal:
        evisa_days = _processing_days(
            evisa_req.processing_time if evisa_req else "", default=7
        )
        standard_days, expedited_days = _passport_processing_days(passport_req)
        evisa_due = depart - timedelta(days=E_VISA_BUFFER_DAYS)
        evisa_start = evisa_due - timedelta(days=evisa_days)
        passport_due_standard = evisa_start - timedelta(days=1)
        passport_start_standard = passport_due_standard - timedelta(days=standard_days)
        passport_due_expedited = evisa_start - timedelta(days=1)
        passport_start_expedited = passport_due_expedited - timedelta(days=expedited_days)

        expedited_recommended = passport_start_standard <= today + timedelta(days=14)

        if expedited_recommended:
            start = max(today, passport_start_expedited)
            due = passport_due_expedited
            path = "expedited"
        else:
            start = max(today, passport_start_standard)
            due = passport_due_standard
            path = "standard"

        if expedited_recommended:
            if passport_start_standard < today:
                choice_description = (
                    "Standard processing can no longer finish in time. "
                    f"Choose expedited processing (start {_human(passport_start_expedited)}) "
                    "or the trip is at risk."
                )
            else:
                choice_description = (
                    f"Standard processing must start by {_human(passport_start_standard)} "
                    f"to finish in time. If you cannot start then, choose expedited processing "
                    f"(start {_human(passport_start_expedited)}). Expedited costs more, "
                    "but it protects the trip."
                )
            tasks.append(
                TaskDraft(
                    key="passport_processing_choice",
                    title="Decide how to renew your passport",
                    description=choice_description,
                    phase="Critical path",
                    start_date=today,
                    due_date=max(today, passport_start_standard),
                    blocking=True,
                    cost_of_missing_cents=6_000,
                    priority="critical",
                    evidence_url=passport_req.official_url if passport_req else "",
                    document_id=passport.id if passport else None,
                    sort_order=sort,
                )
            )
            sort += 1

        passport_done = due
        passport_req_url = passport_req.official_url if passport_req else ""
        if passport is None:
            expiry_text = "No passport on file"
        elif passport.expiry_date:
            expiry_text = _human(passport.expiry_date)
        else:
            expiry_text = "Expiry date not read"
        passport_intro = (
            f"Your passport expires on {expiry_text}."
            if passport is not None
            else "No passport is on file."
        )
        tasks.append(
            TaskDraft(
                key="passport_renewal",
                title="Renew your passport",
                description=(
                    f"{passport_intro} {event.destination or 'The destination'} "
                    f"requires validity through {_human(required_validity)} (six months beyond your "
                    f"return on {_human(return_date)}). The {path} path starts on "
                    f"{_human(start)} and must finish by {_human(due)}. "
                    + (
                        "Expedited processing is recommended because the standard start date is too close."
                        if expedited_recommended
                        else "Standard processing has enough margin."
                    )
                ),
                phase="Critical path",
                start_date=start,
                due_date=due,
                blocking=True,
                cost_of_missing_cents=trip_cost_cents,
                priority="critical",
                evidence_url=passport_req_url,
                document_id=passport.id if passport else None,
                sort_order=sort,
            )
        )
        sort += 1

        if passport is None:
            summary_lines.append(
                "No passport is on file. Add one before planning an international trip."
            )
        else:
            summary_lines.append(
                f"Your passport expires on {expiry_text}. Because the trip returns on "
                f"{_human(return_date)}, the destination requires validity through "
                f"{_human(required_validity)}. It must be renewed before you travel."
            )
        if expedited_recommended:
            if passport_start_standard < today:
                summary_lines.append(
                    "Standard processing can no longer finish in time. "
                    "Expedited processing is required to protect the trip."
                )
            else:
                summary_lines.append(
                    f"Standard processing would need to start on {_human(passport_start_standard)}, "
                    f"which is only {(passport_start_standard - today).days} days away. "
                    "Expedited processing is the safer path."
                )

    if _trip_needs_visa(event.destination) and evisa_req is not None:
        evisa_days = _processing_days(evisa_req.processing_time, default=7)
        evisa_due = depart - timedelta(days=E_VISA_BUFFER_DAYS)
        if passport_done:
            evisa_start = passport_done + timedelta(days=1)
        else:
            evisa_start = max(today, evisa_due - timedelta(days=evisa_days + 7))
        tasks.append(
            TaskDraft(
                key="india_evisa",
                title="Apply for the India e-Visa",
                description=(
                    f"Apply after the new passport arrives. The e-Visa normally takes about "
                    f"{evisa_days} days. Apply by {_human(evisa_due)} so there is a "
                    f"{E_VISA_BUFFER_DAYS}-day buffer before departure."
                ),
                phase="Critical path",
                start_date=evisa_start,
                due_date=evisa_due,
                depends_on=["passport_renewal"] if passport_done else [],
                blocking=True,
                cost_of_missing_cents=trip_cost_cents,
                priority="critical",
                evidence_url=evisa_req.official_url,
                sort_order=sort,
            )
        )
        sort += 1
        summary_lines.append(
            f"The e-Visa application opens only after the new passport arrives. "
            f"Leave a {E_VISA_BUFFER_DAYS}-day buffer before departure."
        )

    tasks.append(
        TaskDraft(
            key="trip_ready",
            title="Final travel document check",
            description=(
                "Confirm the new passport number, the approved e-Visa, the printed ETA, "
                "and the passport validity rule one last time before booking non-refundable items."
            ),
            phase="Critical path",
            start_date=max(today, depart - timedelta(days=21)),
            due_date=depart - timedelta(days=7),
            depends_on=[
                key
                for key in ("passport_processing_choice", "passport_renewal", "india_evisa")
                if any(task.key == key for task in tasks)
            ],
            blocking=True,
            cost_of_missing_cents=trip_cost_cents,
            priority="high",
            sort_order=sort,
        )
    )
    sort += 1

    if insurance and insurance.expiry_date and insurance.expiry_date <= return_date + timedelta(days=30):
        due = insurance.expiry_date - timedelta(days=14)
        start = max(today, due - timedelta(days=7))
        tasks.append(
            TaskDraft(
                key="insurance_renewal",
                title="Renew renters insurance before it lapses",
                description=(
                    f"Your policy expires on {_human(insurance.expiry_date)}, before you leave. "
                    f"Renew by {_human(due)}. A lapse leaves the home uninsured while you are away."
                ),
                phase="Parallel risk",
                start_date=start,
                due_date=due,
                blocking=False,
                cost_of_missing_cents=INSURANCE_GAP_CENTS,
                priority="high" if insurance.expiry_date < depart else "medium",
                evidence_url=insurance_req.official_url if insurance_req else "",
                document_id=insurance.id,
                sort_order=sort,
            )
        )
        sort += 1
        summary_lines.append(
            f"Your renters policy expires on {_human(insurance.expiry_date)}, before departure. "
            "It is a separate risk from the trip, but a lapse would leave the home uninsured."
        )

    if professional and professional.expiry_date and professional.expiry_date <= return_date + timedelta(days=90):
        ce_hours = professional.constraints.get("continuing_education_hours", 30)
        due = professional.expiry_date - timedelta(days=30)
        start = max(today, due - timedelta(days=60))
        tasks.append(
            TaskDraft(
                key="professional_renewal",
                title="Complete CE hours and renew your professional license",
                description=(
                    f"Your license expires on {_human(professional.expiry_date)}. "
                    f"Complete {ce_hours} continuing education hours, then renew by {_human(due)}. "
                    "A lapse affects your ability to work, not the trip."
                ),
                phase="Parallel risk",
                start_date=start,
                due_date=due,
                blocking=False,
                cost_of_missing_cents=LICENSE_LAPSE_CENTS,
                priority="high" if professional.expiry_date < return_date + timedelta(days=30) else "medium",
                evidence_url=professional_req.official_url if professional_req else "",
                document_id=professional.id,
                sort_order=sort,
            )
        )
        sort += 1
        summary_lines.append(
            f"Your professional license expires on {_human(professional.expiry_date)}. "
            f"You need {ce_hours} CE hours before you can renew."
        )

    if driver_license and driver_license.expiry_date:
        if driver_license.days_until_expiry is not None and driver_license.days_until_expiry <= driver_license.renewal_window_days:
            due = driver_license.expiry_date - timedelta(days=14)
            tasks.append(
                TaskDraft(
                    key="driver_license_renewal",
                    title="Renew your driver's license",
                    description=f"Your license expires on {_human(driver_license.expiry_date)}.",
                    phase="Parallel risk",
                    start_date=max(today, due - timedelta(days=7)),
                    due_date=due,
                    blocking=False,
                    cost_of_missing_cents=20_000,
                    priority="medium",
                    document_id=driver_license.id,
                    sort_order=sort,
                )
            )
            sort += 1
        else:
            warnings.append(
                f"Driver's license is valid until {_human(driver_license.expiry_date)}."
            )

    if not summary_lines:
        summary_lines.append(
            "No blocking document issue found for this event. Keep the watch items in the timeline."
        )

    blocking_costs = [task.cost_of_missing_cents for task in tasks if task.blocking]
    parallel_costs = [task.cost_of_missing_cents for task in tasks if not task.blocking]
    risk_total = max(blocking_costs, default=0) + sum(parallel_costs)

    critical_path = [task.key for task in tasks if task.blocking]

    return PlanDraft(
        title=f"Document plan for {event.title}",
        summary=" ".join(summary_lines),
        tasks=tasks,
        risk_total_cents=risk_total,
        critical_path=critical_path,
        warnings=warnings,
    )


def persist_plan(
    session: Session,
    user_id: str,
    event: Event,
    draft: PlanDraft,
) -> Plan:
    statement = select(Plan).where(Plan.user_id == user_id, Plan.event_id == event.id)
    plan = session.exec(statement).first()
    if plan is None:
        plan = Plan(
            user_id=user_id,
            event_id=event.id,
            title=draft.title,
            summary=draft.summary,
            risk_total_cents=draft.risk_total_cents,
        )
        session.add(plan)
        session.commit()
        session.refresh(plan)
    else:
        plan.title = draft.title
        plan.summary = draft.summary
        plan.risk_total_cents = draft.risk_total_cents
        plan.updated_at = _now()
        session.add(plan)
        session.commit()
        session.refresh(plan)

    existing_tasks = session.exec(select(PlanTask).where(PlanTask.plan_id == plan.id)).all()
    for task in existing_tasks:
        session.delete(task)
    session.commit()

    for draft_task in draft.tasks:
        session.add(
            PlanTask(
                plan_id=plan.id,
                document_id=draft_task.document_id,
                title=draft_task.title,
                description=draft_task.description,
                phase=draft_task.phase,
                start_date=draft_task.start_date,
                due_date=draft_task.due_date,
                depends_on_json=json.dumps(draft_task.depends_on),
                blocking=draft_task.blocking,
                cost_of_missing_cents=draft_task.cost_of_missing_cents,
                priority=draft_task.priority,
                evidence_url=draft_task.evidence_url,
                sort_order=draft_task.sort_order,
            )
        )
    session.commit()
    session.refresh(plan)
    return plan


def get_active_plan(session: Session, user_id: str) -> Plan | None:
    statement = (
        select(Plan)
        .where(Plan.user_id == user_id)
        .order_by(Plan.updated_at.desc(), Plan.id.desc())
    )
    return session.exec(statement).first()


def get_latest_event(session: Session, user_id: str) -> Event | None:
    statement = (
        select(Event)
        .where(Event.user_id == user_id)
        .order_by(Event.created_at.desc(), Event.id.desc())
    )
    return session.exec(statement).first()


def latest_doc(documents: list[Document], doc_type: str) -> Document | None:
    matches = [doc for doc in documents if doc.doc_type == doc_type]
    if not matches:
        return None
    return max(matches, key=lambda doc: (doc.expiry_date or date.min, doc.id or 0))


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _human(value: date | None) -> str:
    return value.strftime("%d %B %Y") if value else "not set"


def _passport_processing_days(requirement: Requirement | None) -> tuple[int, int]:
    text = (requirement.processing_time if requirement else "") or ""
    standard = _window_days(text, "standard", default=PASSPORT_STANDARD_DAYS)
    expedited = _window_days(text, "expedited", default=PASSPORT_EXPEDITED_DAYS)
    return standard, expedited


def _window_days(text: str, label: str, default: int) -> int:
    haystack = (text or "").lower()
    label_pattern = r"(?:standard|routine)" if label == "standard" else re.escape(label)

    patterns = (
        rf"(\d+)(?:\s*(?:to|-)\s*(\d+))?\s*weeks?\s*(?:for\s+)?(?:service\s*)?{label_pattern}",
        rf"{label_pattern}\s*(?:service\s*)?[:\-]?\s*(\d+)(?:\s*(?:to|-)\s*(\d+))?\s*weeks?",
    )
    for pattern in patterns:
        match = re.search(pattern, haystack)
        if match:
            return int(match.group(2) or match.group(1)) * 7

    day_patterns = (
        rf"(\d+)(?:\s*(?:to|-)\s*(\d+))?\s*(?:business\s*)?days?\s*(?:for\s+)?(?:service\s*)?{label_pattern}",
        rf"{label_pattern}\s*(?:service\s*)?[:\-]?\s*(\d+)(?:\s*(?:to|-)\s*(\d+))?\s*(?:business\s*)?days?",
    )
    for pattern in day_patterns:
        match = re.search(pattern, haystack)
        if match:
            return int(match.group(2) or match.group(1))
    return default


def _processing_days(value: str, default: int) -> int:
    text = (value or "").lower()
    weeks = re.search(r"(\d+)(?:\s*(?:to|-)\s*(\d+))?\s*weeks?", text)
    if weeks:
        high = int(weeks.group(2) or weeks.group(1))
        return high * 7
    days = re.search(r"(\d+)(?:\s*(?:to|-)\s*(\d+))?\s*(?:business\s*)?days?", text)
    if days:
        high = int(days.group(2) or days.group(1))
        return high
    return default


def _trip_needs_visa(destination: str) -> bool:
    text = (destination or "").lower()
    return any(country in text for country in ("india", "china", "brazil", "vietnam"))


def _now():
    from app.models import utcnow

    return utcnow()
