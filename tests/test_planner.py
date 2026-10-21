from datetime import date, timedelta

from app.models import Document, Event, Requirement, TravelRule
from app.services.event_analysis import (
    guess_home_location,
    infer_event,
    is_domestic_destination,
)
from app.services.tavily import SearchHit
from app.services.verification import (
    _filter_travel_pages,
    _month_candidates,
    _optional_bool,
)
from app.services.planner import (
    add_months,
    build_event_plan,
    build_trip_plan,
    required_doc_types,
    visa_doc_type,
)


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


def make_travel_rule(months: int = 6, status: str = "verified") -> TravelRule:
    return TravelRule(
        destination="India",
        home_location="United States",
        passport_validity_months=months,
        rule_text=f"Passport must be valid for {months} months beyond return.",
        status=status,
        confidence=0.9,
    )


def make_event(
    depart: date = date(2027, 1, 10),
    ret: date = date(2027, 1, 24),
    destination: str = "India",
    event_type: str = "trip",
    destination_country: str = "",
    home_location: str = "",
) -> Event:
    return Event(
        user_id="test",
        event_type=event_type,
        title=f"{event_type.replace('_', ' ').title()}",
        destination=destination,
        destination_country=destination_country,
        home_location=home_location,
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

    plan = build_trip_plan(documents, requirements, make_event(), travel_rule=make_travel_rule(6))
    keys = {task.key for task in plan.tasks}

    assert "passport_processing_choice" in keys
    assert "passport_renewal" in keys
    assert "india_evisa" in keys
    assert "insurance_renewal" in keys
    assert "professional_renewal" in keys
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

    plan = build_trip_plan(documents, requirements, make_event(), travel_rule=make_travel_rule(6))
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

    plan = build_trip_plan(documents, requirements, make_event(), travel_rule=make_travel_rule(6))
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

    plan = build_trip_plan(documents, requirements, make_event(), travel_rule=make_travel_rule(6))
    keys = {task.key for task in plan.tasks}

    assert "passport_renewal" not in keys
    assert "passport_processing_choice" not in keys
    assert "india_evisa" in keys


def test_road_trip_license_expiring_before_event_is_blocking() -> None:
    today = date.today()
    expiry = today + timedelta(days=10)
    event = make_event(
        depart=today + timedelta(days=11),
        ret=today + timedelta(days=14),
        destination="Pacific Coast",
        event_type="road_trip",
    )
    documents = [
        make_document("driver_license", expiry),
        make_document("passport", today + timedelta(days=365)),
    ]
    requirements = {"driver_license": make_requirement("driver_license", "2 weeks")}

    plan = build_event_plan(documents, requirements, event)
    keys = {task.key for task in plan.tasks}

    assert "driver_license_event" in keys
    assert "passport_renewal" not in keys
    assert "india_evisa" not in keys
    task = next(task for task in plan.tasks if task.key == "driver_license_event")
    assert task.blocking is True
    assert "before Road Trip ends" in task.description


def test_unmapped_destination_does_not_add_india_visa() -> None:
    documents = [make_document("passport", date(2029, 6, 14))]
    requirements = {"passport": make_requirement("passport")}
    event = make_event(destination="China")

    plan = build_trip_plan(documents, requirements, event)
    keys = {task.key for task in plan.tasks}

    assert "india_evisa" not in keys
    assert any("No visa source is registered for China" in warning for warning in plan.warnings)
    assert "Visa requirements for China are not checked" in plan.summary


def test_watch_plan_has_no_trip_tasks_or_default_dates() -> None:
    today = date.today()
    documents = [make_document("driver_license", today + timedelta(days=5))]
    event = Event(user_id="test", event_type="watch", title="Document watch")

    plan = build_event_plan(documents, {}, event)
    keys = {task.key for task in plan.tasks}

    assert plan.title == "Document watch"
    assert "watch_driver_license" in keys
    assert "trip_ready" not in keys
    assert "passport_renewal" not in keys
    assert all(task.blocking is False for task in plan.tasks)
    assert all(task.phase == "Expiry watch" for task in plan.tasks)


def test_road_trip_requirement_set_excludes_passport() -> None:
    today = date.today()
    event = make_event(
        depart=today + timedelta(days=20),
        ret=today + timedelta(days=22),
        event_type="road_trip",
    )
    documents = [
        make_document("passport", today + timedelta(days=365)),
        make_document("driver_license", today + timedelta(days=300)),
        make_document("renters_insurance", today + timedelta(days=300)),
    ]

    types = required_doc_types(event, documents)

    assert "driver_license" in types
    assert "renters_insurance" in types
    assert "passport" not in types


def test_other_event_treats_any_expiring_document_as_blocking() -> None:
    today = date.today()
    event = make_event(
        depart=today + timedelta(days=20),
        ret=today + timedelta(days=22),
        destination="",
        event_type="other",
    )
    documents = [make_document("passport", today + timedelta(days=10))]
    requirements = {"passport": make_requirement("passport")}

    plan = build_event_plan(documents, requirements, event)
    task = next(task for task in plan.tasks if task.key == "passport_event")

    assert task.blocking is True
    assert "before Other ends" in task.description


def test_verified_three_month_validity_rule_skips_passport_renewal() -> None:
    documents = [make_document("passport", date(2027, 6, 14))]
    requirements = {
        "passport": make_requirement("passport"),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
    }
    rule = TravelRule(
        destination="India",
        home_location="United States",
        passport_validity_months=3,
        rule_text="Passport must be valid for three months beyond departure.",
        status="verified",
        confidence=0.9,
    )

    plan = build_trip_plan(
        documents, requirements, make_event(), travel_rule=rule
    )
    keys = {task.key for task in plan.tasks}

    assert "passport_renewal" not in keys
    assert "passport_processing_choice" not in keys
    assert "assumes six months" not in plan.summary


def test_missing_validity_rule_asks_for_manual_verification() -> None:
    documents = [make_document("passport", date(2027, 6, 14))]
    requirements = {
        "passport": make_requirement("passport"),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
    }

    plan = build_trip_plan(documents, requirements, make_event())
    keys = {task.key for task in plan.tasks}

    assert "passport_rule_check" in keys
    assert "passport_renewal" not in keys
    assert any("will not assume a period" in warning for warning in plan.warnings)
    assert "not verified" in plan.summary


def test_india_visa_matching_uses_a_word_boundary() -> None:
    assert visa_doc_type("India") == "india_evisa"
    assert visa_doc_type("Trip to India") == "india_evisa"
    assert visa_doc_type("Indianapolis") is None
    assert visa_doc_type("Indiana") is None


def test_guess_home_location_reads_the_passport_issuer() -> None:
    documents = [
        Document(
            user_id="test",
            doc_type="passport",
            title="Passport",
            issuer="U.S. DEPARTMENT OF STATE",
        )
    ]

    assert guess_home_location(documents) == "United States"


def test_domestic_destination_comparison_is_conservative() -> None:
    assert is_domestic_destination("California", "California, United States") is True
    assert is_domestic_destination("India", "California, United States") is None


def test_travel_page_filter_prefers_the_advisory_over_visa_tables() -> None:
    hits = [
        SearchHit(
            title="Italy visa reciprocity",
            url="https://travel.state.gov/content/travel/en/us-visas/Visa-Reciprocity-and-Civil-Documents-by-Country/Italy.html",
            content="Visa issuance fee and validity period.",
        ),
        SearchHit(
            title="Italy Travel Advisory",
            url="https://travel.state.gov/en/international-travel/travel-advisories/italy.html",
            content="Passports must be valid for at least 3 months beyond departure.",
        ),
    ]

    filtered = _filter_travel_pages(hits, "Italy")
    other_country = SearchHit(
        title="France Travel Advisory",
        url="https://travel.state.gov/en/international-travel/travel-advisories/france.html",
        content="Passports must be valid for at least 3 months.",
    )
    destination_filtered = _filter_travel_pages(hits + [other_country], "Italy")

    assert filtered[0].title == "Italy Travel Advisory"
    assert all("italy" in hit.url for hit in destination_filtered)


def test_destination_country_resolves_domestic_and_international() -> None:
    assert (
        is_domestic_destination("Big Sur", "California, United States", "United States")
        is True
    )
    assert (
        is_domestic_destination("Paris", "California, United States", "France")
        is False
    )
    assert is_domestic_destination("Sydney", "US", "Australia") is False


def test_event_inference_uses_the_destination_country(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.event_analysis.NebiusClient.available",
        property(lambda self: False),
    )
    today = date.today()
    event = make_event(
        depart=today + timedelta(days=20),
        ret=today + timedelta(days=28),
        destination="Paris",
        event_type="auto",
        destination_country="France",
        home_location="California, United States",
    )
    documents = [make_document("passport", today + timedelta(days=800))]

    analysis = infer_event(documents, event)

    assert analysis.event_type == "trip"
    assert analysis.is_international is True
    assert analysis.destination_country == "France"


def test_explicit_international_override_stays_international() -> None:
    today = date.today()
    event = make_event(
        depart=today + timedelta(days=20),
        ret=today + timedelta(days=22),
        destination="California",
        event_type="trip",
        destination_country="United States",
        home_location="California, United States",
    )
    documents = [make_document("passport", today + timedelta(days=800))]

    analysis = infer_event(documents, event)

    assert analysis.event_type == "trip"
    assert analysis.is_international is True


def test_non_india_visa_rule_adds_a_generic_visa_task() -> None:
    documents = [make_document("passport", date(2029, 6, 14))]
    requirements = {"passport": make_requirement("passport")}
    event = make_event(destination="Italy", destination_country="Italy")
    rule = TravelRule(
        destination="Italy",
        home_location="United States",
        passport_validity_months=3,
        rule_text="Passport must be valid for three months beyond departure.",
        visa_required=True,
        visa_type="Schengen visa",
        visa_rule_text="A Schengen visa is required for this trip.",
        status="verified",
        confidence=0.9,
    )

    plan = build_trip_plan(documents, requirements, event, travel_rule=rule)
    keys = {task.key for task in plan.tasks}
    task = next(task for task in plan.tasks if task.key == "visa_check")

    assert "visa_check" in keys
    assert task.blocking is True
    assert "Schengen visa" in task.title


def test_no_visa_required_rule_does_not_add_a_task() -> None:
    documents = [make_document("passport", date(2029, 6, 14))]
    requirements = {"passport": make_requirement("passport")}
    event = make_event(destination="Italy", destination_country="Italy")
    rule = TravelRule(
        destination="Italy",
        home_location="United States",
        passport_validity_months=3,
        rule_text="Passport must be valid for three months beyond departure.",
        visa_required=False,
        visa_rule_text="No visa is required for a short stay.",
        status="verified",
        confidence=0.9,
    )

    plan = build_trip_plan(documents, requirements, event, travel_rule=rule)
    keys = {task.key for task in plan.tasks}

    assert "visa_check" not in keys
    assert "no visa is required" in plan.summary.lower()


def test_unknown_visa_rule_adds_a_manual_verification_task() -> None:
    documents = [make_document("passport", date(2029, 6, 14))]
    requirements = {"passport": make_requirement("passport")}
    event = make_event(destination="Italy", destination_country="Italy")
    rule = TravelRule(
        destination="Italy",
        home_location="United States",
        passport_validity_months=3,
        rule_text="Passport must be valid for three months beyond departure.",
        visa_required=None,
        status="verified",
        confidence=0.9,
    )

    plan = build_trip_plan(documents, requirements, event, travel_rule=rule)
    task = next(task for task in plan.tasks if task.key == "visa_check")

    assert task.blocking is True
    assert "Verify visa" in task.title


def test_india_visa_task_is_not_duplicated_by_the_generic_rule() -> None:
    documents = [make_document("passport", date(2029, 6, 14))]
    requirements = {
        "passport": make_requirement("passport"),
        "india_evisa": make_requirement("india_evisa", "4 to 7 business days"),
    }
    event = make_event(destination="India", destination_country="India")
    rule = TravelRule(
        destination="India",
        home_location="United States",
        passport_validity_months=6,
        rule_text="Passport must be valid for six months.",
        visa_required=True,
        visa_type="e-Visa",
        visa_rule_text="An e-Visa is required.",
        status="verified",
        confidence=0.9,
    )

    plan = build_trip_plan(documents, requirements, event, travel_rule=rule)
    keys = {task.key for task in plan.tasks}

    assert "india_evisa" in keys
    assert "visa_check" not in keys


def test_month_candidates_and_boolean_parsing() -> None:
    assert _month_candidates("Valid for 3 months beyond departure.") == {3}
    assert _month_candidates("Must have six (6) months validity.") == {6}
    assert _optional_bool("false") is False
    assert _optional_bool("true") is True
    assert _optional_bool(None) is None


def test_event_inference_falls_back_to_road_trip(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.event_analysis.NebiusClient.available",
        property(lambda self: False),
    )
    today = date.today()
    event = Event(
        user_id="test",
        event_type="auto",
        title="Road trip to Big Sur",
        destination="Big Sur",
        home_location="California, United States",
        depart_date=today + timedelta(days=11),
        return_date=today + timedelta(days=14),
    )
    documents = [make_document("driver_license", today + timedelta(days=10))]

    analysis = infer_event(documents, event)

    assert analysis.event_type == "road_trip"
    assert analysis.is_international is False
    assert "driver_license" in analysis.relevant_document_types
    assert analysis.mode == "fallback"
