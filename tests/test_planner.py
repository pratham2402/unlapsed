from datetime import date

from app.models import Document, Event, Requirement
from app.services.planner import add_months, build_trip_plan


def make_requirement(doc_type: str, processing_time: str = "6 to 8 weeks") -> Requirement:
    return Requirement(
        doc_type=doc_type,
        title=doc_type.replace("_", " ").title(),
        processing_time=processing_time,
        fee="Check the official source",
        official_url="https://example.gov",
        confidence=0.5,
        status="verified",
    )


def make_document(doc_type: str, expiry: date, constraints: str = "{}") -> Document:
    return Document(
        user_id="test",
        doc_type=doc_type,
        title=doc_type.replace("_", " ").title(),
        holder_name="Alex Morgan",
        expiry_date=expiry,
        constraints_json=constraints,
    )


def make_event(depart: date = date(2027, 1, 10), ret: date = date(2027, 1, 24)) -> Event:
    return Event(
        user_id="test",
        event_type="trip",
        title="Trip to India",
        destination="India",
        depart_date=depart,
        return_date=ret,
    )


def test_add_months_clamps_month_end() -> None:
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2027, 1, 24), 6) == date(2027, 7, 24)


def test_passport_six_month_rule_creates_renewal_chain() -> None:
    documents = [
        make_document("passport", date(2027, 6, 14)),
        make_document("renters_insurance", date(2027, 1, 1)),
        make_document(
            "professional_license",
            date(2027, 3, 31),
            '{"continuing_education_hours": 30}',
        ),
    ]
    requirements = {
        "passport": make_requirement("passport"),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
        "renters_insurance": make_requirement("renters_insurance"),
        "professional_license": make_requirement("professional_license"),
    }

    plan = build_trip_plan(documents, requirements, make_event())
    keys = {task.key for task in plan.tasks}

    assert "passport_processing_choice" in keys
    assert "passport_renewal" in keys
    assert "india_evisa" in keys
    assert "insurance_renewal" in keys
    assert "professional_renewal" in keys
    assert plan.risk_total_cents > 0
    assert "24 October 2026" in plan.summary

    passport_task = next(task for task in plan.tasks if task.key == "passport_renewal")
    assert passport_task.blocking is True
    assert passport_task.depends_on == []
    evisa_task = next(task for task in plan.tasks if task.key == "india_evisa")
    assert "passport_renewal" in evisa_task.depends_on


def test_processing_time_change_moves_the_plan() -> None:
    documents = [make_document("passport", date(2027, 6, 14))]
    requirements = {
        "passport": make_requirement(
            "passport", "8 to 11 weeks standard, 3 to 4 weeks expedited"
        ),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
    }

    plan = build_trip_plan(documents, requirements, make_event())
    decision = next(task for task in plan.tasks if task.key == "passport_processing_choice")

    assert decision.due_date == date.today()
    assert "no longer finish in time" in decision.description
    assert "no longer finish in time" in plan.summary


def test_routine_processing_time_is_parsed() -> None:
    documents = [make_document("passport", date(2027, 6, 14))]
    requirements = {
        "passport": make_requirement(
            "passport", "4-6 weeks for routine service; 2-3 weeks for expedited service (extra $60)"
        ),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
    }

    plan = build_trip_plan(documents, requirements, make_event())
    keys = {task.key for task in plan.tasks}

    assert "passport_renewal" in keys
    assert "passport_processing_choice" not in keys
    passport_task = next(task for task in plan.tasks if task.key == "passport_renewal")
    assert "Standard processing has enough margin" in passport_task.description


def test_valid_passport_skips_renewal() -> None:
    documents = [make_document("passport", date(2029, 6, 14))]
    requirements = {
        "passport": make_requirement("passport"),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
    }

    plan = build_trip_plan(documents, requirements, make_event())
    keys = {task.key for task in plan.tasks}

    assert "passport_renewal" not in keys
    assert "passport_processing_choice" not in keys
    assert "india_evisa" in keys
