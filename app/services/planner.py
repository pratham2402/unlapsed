from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import Document, Event, Plan, PlanTask, Requirement, TravelRule

PASSPORT_STANDARD_DAYS = 56
PASSPORT_EXPEDITED_DAYS = 21
E_VISA_BUFFER_DAYS = 14

EVENT_TYPE_LABELS = {
    "trip": "International trip",
    "road_trip": "Domestic travel",
    "move": "Move",
    "job_start": "Job start",
    "other": "Other dated event",
}
EVENT_TYPES = tuple(EVENT_TYPE_LABELS)

# Which documents can stop the event, and which are parallel risks.
EVENT_RULES: dict[str, tuple[set[str], set[str]]] = {
    "road_trip": ({"driver_license"}, {"renters_insurance", "professional_license"}),
    "move": ({"driver_license", "renters_insurance"}, {"professional_license"}),
    "job_start": ({"professional_license"}, {"driver_license"}),
}

FINAL_CHECKS = {
    "road_trip": (
        "Final domestic travel check",
        "Confirm your driver's license or ID, vehicle insurance, registration, and any booking documents before you leave.",
    ),
    "move": (
        "Final move check",
        "Confirm the address change, your license, insurance, and any work documents are valid for the move.",
    ),
    "job_start": (
        "Final work document check",
        "Confirm your professional license, identification, and right-to-work documents are valid on your start date.",
    ),
    "other": (
        "Final event check",
        "Confirm every document this event depends on is valid on the event date.",
    ),
}


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


def build_event_plan(
    documents: list[Document],
    requirements: dict[str, Requirement],
    event: Event,
    travel_rule: TravelRule | None = None,
) -> PlanDraft:
    """Build the plan for the event type.

    The graph is deterministic. The model classifies the event, extracts
    document fields, and reads official pages, but it does not decide the
    dates or the dependency order.
    """
    if event.event_type == "watch" or (not event.depart_date and not event.return_date):
        return _build_watch_plan(documents, requirements)

    if event.event_type == "trip":
        return build_trip_plan(documents, requirements, event, travel_rule)

    return _build_dated_event_plan(documents, requirements, event)


def build_trip_plan(
    documents: list[Document],
    requirements: dict[str, Requirement],
    event: Event,
    travel_rule: TravelRule | None = None,
) -> PlanDraft:
    today = date.today()
    depart = event.depart_date or (today + timedelta(days=90))
    return_date = event.return_date or (depart + timedelta(days=14))
    validity_months, validity_verified = _passport_validity_months(travel_rule)
    required_validity = (
        add_months(return_date, validity_months)
        if validity_months is not None
        else None
    )

    passport = latest_doc(documents, "passport")
    insurance = latest_doc(documents, "renters_insurance")
    professional = latest_doc(documents, "professional_license")
    driver_license = latest_doc(documents, "driver_license")

    passport_req = requirements.get("passport")
    visa_type = visa_doc_type(event.destination, event.destination_country)
    visa_req = requirements.get(visa_type) if visa_type else None
    insurance_req = requirements.get("renters_insurance")
    professional_req = requirements.get("professional_license")

    warnings: list[str] = []
    tasks: list[TaskDraft] = []
    summary_lines: list[str] = []
    sort = 0

    if not validity_verified:
        warnings.append(_validity_warning(travel_rule, validity_months))

    passport_expiry = passport.expiry_date if passport is not None else None
    passport_before_return = (
        passport_expiry is not None and passport_expiry < return_date
    )
    passport_needs_renewal = (
        passport is None
        or passport_expiry is None
        or (
            required_validity is not None
            and passport_expiry < required_validity
        )
        or (required_validity is None and passport_before_return)
    )
    passport_rule_unknown = (
        required_validity is None and passport is not None and not passport_needs_renewal
    )

    passport_done: date | None = None
    expedited_recommended = False

    if passport_rule_unknown:
        tasks.append(
            TaskDraft(
                key="passport_rule_check",
                title=f"Verify the passport validity rule for {event.destination or 'the destination'}",
                description=(
                    f"Your passport expires on {_human(passport_expiry)}. "
                    "The destination's validity rule could not be verified, so the "
                    "planner cannot tell whether the passport meets the entry requirement. "
                    "Check the official source before booking."
                ),
                phase="Critical path",
                start_date=today,
                due_date=max(today, depart - timedelta(days=30)),
                blocking=True,
                priority="critical",
                evidence_url=travel_rule.source_url if travel_rule else "",
                document_id=passport.id if passport else None,
                sort_order=sort,
            )
        )
        sort += 1
        summary_lines.append(
            f"Your passport expires on {_human(passport_expiry)}. The destination's "
            "passport validity rule is not verified. Verify it manually before booking."
        )

    if passport_needs_renewal:
        visa_days = _processing_days(
            visa_req.processing_time if visa_req else "", default=7
        )
        standard_days, expedited_days = _passport_processing_days(passport_req)
        visa_due = depart - timedelta(days=E_VISA_BUFFER_DAYS)
        visa_start = visa_due - timedelta(days=visa_days)
        passport_due_standard = visa_start - timedelta(days=1)
        passport_start_standard = passport_due_standard - timedelta(days=standard_days)
        passport_due_expedited = visa_start - timedelta(days=1)
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
                    "or the trip cannot proceed."
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
        if required_validity is not None:
            validity_sentence = (
                f"{event.destination or 'The destination'} requires validity through "
                f"{_human(required_validity)} "
                f"({_validity_rule_text(validity_months)}; return is "
                f"{_human(return_date)}). "
            )
        else:
            validity_sentence = (
                "The destination's passport validity rule is not verified. "
                "Renew the passport before you travel and confirm the rule. "
            )
        tasks.append(
            TaskDraft(
                key="passport_renewal",
                title="Renew your passport",
                description=(
                    f"{passport_intro} {validity_sentence}"
                    f"The {path} path starts on {_human(start)} and must finish by "
                    f"{_human(due)}. "
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
        elif required_validity is not None:
            summary_lines.append(
                f"Your passport expires on {expiry_text}. Because the trip returns on "
                f"{_human(return_date)}, the destination requires validity through "
                f"{_human(required_validity)} ({_validity_rule_text(validity_months)}). "
                "It must be renewed before you travel."
            )
            if not validity_verified:
                summary_lines.append(_validity_warning(travel_rule, validity_months))
        else:
            summary_lines.append(
                f"Your passport expires on {expiry_text}. The destination's passport "
                "validity rule is not verified. Renew the passport and confirm the rule."
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

    if visa_type and visa_req is not None:
        visa_days = _processing_days(visa_req.processing_time, default=7)
        visa_due = depart - timedelta(days=E_VISA_BUFFER_DAYS)
        if passport_done:
            visa_start = passport_done + timedelta(days=1)
            visa_intro = "Apply after the new passport arrives."
            passport_dependency = "passport_renewal"
        elif passport_rule_unknown:
            visa_start = max(today, visa_due - timedelta(days=visa_days + 7))
            visa_intro = "Confirm the passport validity rule first."
            passport_dependency = "passport_rule_check"
        else:
            visa_start = max(today, visa_due - timedelta(days=visa_days + 7))
            visa_intro = "Apply before departure."
            passport_dependency = ""
        tasks.append(
            TaskDraft(
                key=visa_type,
                title="Apply for the India e-Visa"
                if visa_type == "india_evisa"
                else f"Apply for the {visa_req.title}",
                description=(
                    f"{visa_intro} The visa normally takes about "
                    f"{visa_days} days. Apply by {_human(visa_due)} so there is a "
                    f"{E_VISA_BUFFER_DAYS}-day buffer before departure."
                ),
                phase="Critical path",
                start_date=visa_start,
                due_date=visa_due,
                depends_on=[passport_dependency] if passport_dependency else [],
                blocking=True,
                priority="critical",
                evidence_url=visa_req.official_url,
                sort_order=sort,
            )
        )
        sort += 1
        summary_lines.append(
            "The visa application opens only after the new passport arrives. "
            f"Leave a {E_VISA_BUFFER_DAYS}-day buffer before departure."
        )
    else:
        visa_country = event.destination_country or event.destination or "the destination"
        if travel_rule is not None:
            visa_due = depart - timedelta(days=E_VISA_BUFFER_DAYS)
            if travel_rule.visa_required is True:
                visa_label = travel_rule.visa_type or "visa or entry authorization"
                tasks.append(
                    TaskDraft(
                        key="visa_check",
                        title=f"Apply for or verify {visa_label} for {visa_country}",
                        description=(
                            f"{travel_rule.visa_rule_text or 'The official source says a visa is required.'} "
                            f"Confirm the current requirement on the official source and apply by "
                            f"{_human(visa_due)}. Leave a {E_VISA_BUFFER_DAYS}-day buffer before departure."
                        ),
                        phase="Critical path",
                        start_date=max(today, visa_due - timedelta(days=14)),
                        due_date=visa_due,
                        depends_on=["passport_renewal"] if passport_done else [],
                        blocking=True,
                        priority="critical",
                        evidence_url=travel_rule.source_url,
                        sort_order=sort,
                    )
                )
                sort += 1
                summary_lines.append(
                    f"The official source says a {visa_label} is required for {visa_country}. "
                    f"Verify it and apply by {_human(visa_due)}."
                )
            elif travel_rule.visa_required is None:
                tasks.append(
                    TaskDraft(
                        key="visa_check",
                        title=f"Verify visa or entry authorization for {visa_country}",
                        description=(
                            "The official source did not state whether a visa is required. "
                            f"Check it before booking and resolve it by {_human(visa_due)}."
                        ),
                        phase="Critical path",
                        start_date=max(today, visa_due - timedelta(days=14)),
                        due_date=visa_due,
                        depends_on=["passport_renewal"] if passport_done else [],
                        blocking=True,
                        priority="critical",
                        evidence_url=travel_rule.source_url,
                        sort_order=sort,
                    )
                )
                sort += 1
                summary_lines.append(
                    f"The official source did not state the visa requirement for {visa_country}. "
                    "Verify it manually."
                )
            else:
                summary_lines.append(
                    f"The official source says no visa is required for this trip to {visa_country}."
                )
        elif visa_country:
            warnings.append(
                f"No visa source is registered for {visa_country}. "
                "Verify visa requirements manually."
            )
            summary_lines.append(
                f"Visa requirements for {visa_country} are not checked in this build. "
                "Verify them manually."
            )

    tasks.append(
        TaskDraft(
            key="trip_ready",
            title="Final travel document check",
            description=(
                "Confirm the new passport number, the approved visa or ETA, "
                "and the passport validity rule one last time before booking non-refundable items."
            ),
            phase="Critical path",
            start_date=max(today, depart - timedelta(days=21)),
            due_date=max(today, depart - timedelta(days=7)),
            depends_on=[task.key for task in tasks if task.blocking],
            blocking=True,
            priority="high",
            sort_order=sort,
        )
    )
    sort += 1

    if insurance and insurance.expiry_date and insurance.expiry_date <= return_date + timedelta(days=30):
        due = _deadline(insurance.expiry_date, 14, today)
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
        due = _deadline(professional.expiry_date, 30, today)
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
            due = _deadline(driver_license.expiry_date, 14, today)
            tasks.append(
                TaskDraft(
                    key="driver_license_renewal",
                    title="Renew your driver's license",
                    description=f"Your license expires on {_human(driver_license.expiry_date)}.",
                    phase="Parallel risk",
                    start_date=max(today, due - timedelta(days=7)),
                    due_date=due,
                    blocking=False,
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

    critical_path = [task.key for task in tasks if task.blocking]

    return PlanDraft(
        title=f"Document plan for {event.title}",
        summary=" ".join(summary_lines),
        tasks=tasks,
        risk_total_cents=0,
        critical_path=critical_path,
        warnings=warnings,
    )


def _build_watch_plan(
    documents: list[Document],
    requirements: dict[str, Requirement],
) -> PlanDraft:
    today = date.today()
    tasks: list[TaskDraft] = []
    summary_lines: list[str] = []
    sort = 0

    for document in sorted(documents, key=lambda doc: doc.expiry_date or date.max):
        if document.doc_type == "other" or document.expiry_date is None:
            continue
        requirement = requirements.get(document.doc_type)
        days = (document.expiry_date - today).days
        if days < 0:
            due = today
            start = today
            priority = "critical"
            summary_lines.append(
                f"Your {document.title} expired on {_human(document.expiry_date)}. Renew it now."
            )
        elif days <= document.renewal_window_days:
            due = _deadline(document.expiry_date, 14, today)
            start = max(today, due - timedelta(days=7))
            priority = "high" if days <= 30 else "medium"
            summary_lines.append(
                f"Your {document.title} expires on {_human(document.expiry_date)}. "
                f"Renew by {_human(due)}."
            )
        else:
            continue

        tasks.append(
            TaskDraft(
                key=f"watch_{document.doc_type}",
                title=f"Renew {document.title}",
                description=(
                    f"Your {document.title} expires on {_human(document.expiry_date)}. "
                    "No dated event is on file, so this is an expiry watch."
                ),
                phase="Expiry watch",
                start_date=start,
                due_date=due,
                blocking=False,
                priority=priority,
                evidence_url=requirement.official_url if requirement else "",
                document_id=document.id,
                sort_order=sort,
            )
        )
        sort += 1

    if not summary_lines:
        summary_lines.append(
            "No documents are inside their renewal window. The register is up to date."
        )

    return PlanDraft(
        title="Document watch",
        summary=" ".join(summary_lines),
        tasks=tasks,
        risk_total_cents=0,
        critical_path=[],
        warnings=[],
    )


def _build_dated_event_plan(
    documents: list[Document],
    requirements: dict[str, Requirement],
    event: Event,
) -> PlanDraft:
    today = date.today()
    event_start, event_end = _event_window(event)
    critical, parallel = EVENT_RULES.get(event.event_type, (set(), set()))
    agent_types = {doc_type for doc_type in event.relevant_doc_types if doc_type != "other"}

    if event.event_type == "other":
        critical = {doc.doc_type for doc in documents if doc.doc_type != "other"} | agent_types
        parallel = set()
    else:
        parallel = parallel | (agent_types - critical)

    tasks: list[TaskDraft] = []
    summary_lines: list[str] = []
    warnings: list[str] = []
    sort = 0

    for document in sorted(documents, key=lambda doc: doc.expiry_date or date.max):
        if document.doc_type == "other" or document.expiry_date is None:
            continue
        is_critical = document.doc_type in critical
        is_parallel = document.doc_type in parallel
        if not (is_critical or is_parallel):
            continue

        requirement = requirements.get(document.doc_type)
        blocking = is_critical and document.expiry_date <= event_end

        if document.expiry_date <= event_end:
            due = _deadline(document.expiry_date, 14, today)
            start = max(today, due - timedelta(days=7))
            if blocking:
                summary_lines.append(
                    f"Your {document.title} expires on {_human(document.expiry_date)}, "
                    f"before {event.title} ends on {_human(event_end)}. It must be renewed first."
                )
                description = (
                    f"Your {document.title} expires on {_human(document.expiry_date)}, "
                    f"before {event.title} ends on {_human(event_end)}. "
                    f"Renew by {_human(due)}. The event cannot go ahead without it."
                )
            else:
                summary_lines.append(
                    f"Your {document.title} expires on {_human(document.expiry_date)}. "
                    f"It is a separate risk from {event.title}."
                )
                description = (
                    f"Your {document.title} expires on {_human(document.expiry_date)}. "
                    f"Renew by {_human(due)}. It is a separate risk from {event.title}."
                )
            tasks.append(
                TaskDraft(
                    key=f"{document.doc_type}_event",
                    title=f"Renew {document.title} before {event.title}",
                    description=description,
                    phase="Critical path" if blocking else "Parallel risk",
                    start_date=start,
                    due_date=due,
                    blocking=blocking,
                    priority="critical" if blocking else "high",
                    evidence_url=requirement.official_url if requirement else "",
                    document_id=document.id,
                    sort_order=sort,
                )
            )
            sort += 1
        elif document.days_until_expiry is not None and document.days_until_expiry <= document.renewal_window_days:
            due = _deadline(document.expiry_date, 14, today)
            tasks.append(
                TaskDraft(
                    key=f"{document.doc_type}_watch",
                    title=f"Renew {document.title} after {event.title}",
                    description=(
                        f"Your {document.title} expires on {_human(document.expiry_date)}. "
                        f"It is valid through the event, but it is inside its renewal window. "
                        f"Renew by {_human(due)}."
                    ),
                    phase="Parallel risk",
                    start_date=max(today, due - timedelta(days=7)),
                    due_date=due,
                    blocking=False,
                    priority="medium",
                    evidence_url=requirement.official_url if requirement else "",
                    document_id=document.id,
                    sort_order=sort,
                )
            )
            sort += 1

    if event.event_type == "road_trip" and latest_doc(documents, "driver_license") is None:
        summary_lines.append(
            "No driver's license is on file. Add one before planning a road trip."
        )
        warnings.append("A road trip plan has no driver's license document to check.")
    if event.event_type == "job_start" and latest_doc(documents, "professional_license") is None:
        summary_lines.append(
            "No professional license is on file. Add one if the job requires it."
        )

    title, description = FINAL_CHECKS.get(
        event.event_type,
        ("Final event check", "Confirm every document this event depends on is valid."),
    )
    due = max(today, event_start - timedelta(days=3))
    tasks.append(
        TaskDraft(
            key="event_ready",
            title=title,
            description=description,
            phase="Critical path",
            start_date=max(today, due - timedelta(days=7)),
            due_date=due,
            depends_on=[task.key for task in tasks if task.blocking],
            blocking=True,
            priority="high",
            sort_order=sort,
        )
    )

    if not summary_lines:
        summary_lines.append(
            "No document expires before this event. The final check is still on the timeline."
        )

    return PlanDraft(
        title=f"Document plan for {event.title}",
        summary=" ".join(summary_lines),
        tasks=tasks,
        risk_total_cents=0,
        critical_path=[task.key for task in tasks if task.blocking],
        warnings=warnings,
    )


def required_doc_types(event: Event | None, documents: list[Document]) -> set[str]:
    """Return only the document types this event needs to check.

    Keeping this narrow avoids extra Tavily and model calls for documents that
    cannot affect the event.
    """
    present = {doc.doc_type for doc in documents if doc.doc_type != "other"}
    if event is None or event.event_type == "watch" or (not event.depart_date and not event.return_date):
        return present

    agent_types = {doc_type for doc_type in event.relevant_doc_types if doc_type != "other"}

    if event.event_type == "trip":
        doc_types = set(present) | {"passport"} | agent_types
        visa_type = visa_doc_type(event.destination, event.destination_country)
        if visa_type:
            doc_types.add(visa_type)
        return doc_types
    if event.event_type in EVENT_RULES:
        critical, parallel = EVENT_RULES[event.event_type]
        return (present & (critical | parallel)) | critical | agent_types
    return present | agent_types


def visa_doc_type(
    destination: str | None,
    destination_country: str | None = None,
) -> str | None:
    text = (destination_country or destination or "").lower()
    if re.search(r"\bindia\b", text):
        return "india_evisa"
    return None


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


def _passport_validity_months(
    travel_rule: TravelRule | None,
) -> tuple[int | None, bool]:
    if travel_rule is not None and travel_rule.passport_validity_months is not None:
        return (
            max(0, int(travel_rule.passport_validity_months)),
            travel_rule.status == "verified",
        )
    return None, False


def _validity_warning(travel_rule: TravelRule | None, months: int | None) -> str:
    if months is not None:
        return (
            f"The passport validity rule needs manual confirmation. "
            f"The plan uses {months} months."
        )
    return (
        "No verified passport validity rule was found for this destination. "
        "The planner will not assume a period. Verify it manually."
    )


def _validity_rule_text(months: int) -> str:
    if months <= 0:
        return "no extra validity beyond the return date"
    if months == 1:
        return "one month beyond the return date"
    return f"{months} months beyond the return date"


def _event_window(event: Event) -> tuple[date, date]:
    today = date.today()
    start = event.depart_date or today
    end = event.return_date or start
    if end < start:
        end = start
    return start, end


def _deadline(expiry: date, buffer_days: int, today: date) -> date:
    return max(today, expiry - timedelta(days=buffer_days))


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


def _now():
    from app.models import utcnow

    return utcnow()
