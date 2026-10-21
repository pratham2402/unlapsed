from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from app.models import Document, Event
from app.services.nebius import NebiusClient, NebiusError
from app.services.planner import EVENT_TYPE_LABELS, EVENT_TYPES

ALLOWED_DOC_TYPES = {
    "passport",
    "driver_license",
    "renters_insurance",
    "professional_license",
}

US_ALIASES = (
    "united states",
    "usa",
    "u.s.",
    "u.s.a.",
    "america",
)

US_STATES = (
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
)


@dataclass
class EventAnalysis:
    event_type: str
    is_international: bool
    home_location: str
    destination_country: str
    relevant_document_types: list[str]
    reason: str
    confidence: float
    mode: str = "fallback"


def infer_event(documents: list[Document], event: Event) -> EventAnalysis:
    """Classify the event and decide which documents can affect it.

    The model is allowed to choose the event style and relevant documents. The
    planner still owns the dates, dependency order, and blocker labels.
    """
    explicit_type = event.event_type if event.event_type in EVENT_TYPES else ""
    home = (event.home_location or "").strip() or guess_home_location(documents)

    if explicit_type and home:
        return _fallback_analysis(documents, event, explicit_type, home)

    client = NebiusClient()
    if client.available:
        try:
            return _infer_with_model(
                client, documents, event, home, explicit_type, client.fast_model
            )
        except (NebiusError, ValueError, KeyError, TypeError):
            try:
                return _infer_with_model(
                    client,
                    documents,
                    event,
                    home,
                    explicit_type,
                    client.reasoning_model,
                )
            except (NebiusError, ValueError, KeyError, TypeError):
                pass

    return _fallback_analysis(documents, event, explicit_type, home)


def _is_us_text(value: str) -> bool:
    text = (value or "").lower().strip()
    if text in {"us", "u.s.", "usa", "u.s.a.", "america", "united states"}:
        return True
    return any(alias in text for alias in US_ALIASES)


def guess_home_location(documents: list[Document]) -> str:
    for document in documents:
        if document.doc_type == "passport":
            issuer = (document.issuer or "").lower()
            if any(alias in issuer for alias in US_ALIASES) or "department of state" in issuer:
                return "United States"

    for document in documents:
        if document.doc_type in {"driver_license", "professional_license"}:
            issuer = (document.issuer or "").lower()
            state = next((state for state in US_STATES if state in issuer), "")
            if state:
                return f"{state.title()}, United States"

    return ""


def is_domestic_destination(
    destination: str,
    home_location: str,
    destination_country: str = "",
) -> bool | None:
    """Return True or False only when the comparison is unambiguous."""
    country_text = (destination_country or "").lower().strip()
    home_text = (home_location or "").lower().strip()
    if country_text and home_text:
        home_country = home_text.split(",")[-1].strip()
        if country_text == home_text or country_text == home_country:
            return True
        if _is_us_text(country_text) and _is_us_text(home_text):
            return True
        if any(state in home_text for state in US_STATES) and _is_us_text(country_text):
            return True
        return False

    destination_text = (destination or "").lower().strip()
    if not destination_text or not home_text:
        return None

    if _is_us_text(home_text):
        if any(alias in destination_text for alias in US_ALIASES):
            return True
        if any(state in destination_text for state in US_STATES):
            return True

    home_city = home_text.split(",")[0].strip()
    if home_city and home_city in destination_text:
        return True

    return None


def _infer_with_model(
    client: NebiusClient,
    documents: list[Document],
    event: Event,
    home: str,
    explicit_type: str,
    model: str,
) -> EventAnalysis:
    document_lines = []
    for document in documents:
        document_lines.append(
            {
                "type": document.doc_type,
                "title": document.title,
                "issuer": document.issuer,
                "expiry_date": document.expiry_date.isoformat()
                if document.expiry_date
                else None,
                "constraints": document.constraints,
            }
        )

    prompt = f"""
Classify this dated event for a document renewal planner.

Return one JSON object:
{{
  "event_type": "trip | road_trip | move | job_start | other",
  "is_international": true,
  "home_location": "country or state where the person lives",
  "destination_country": "country of the destination, or an empty string",
  "relevant_document_types": ["passport", "driver_license"],
  "reason": "one short sentence explaining the reading",
  "confidence": 0.0
}}

Definitions:
- trip: international travel.
- road_trip: domestic travel where driving or a domestic ID matters.
- move: relocation to a new home.
- job_start: starting a job or a new role.
- other: any other dated event.

Rules:
- Use only the event details and the document inventory below.
- home_location is supplied when known. If it is empty, infer it from the issuers.
- destination_country is supplied when known. If it is empty, infer the country from the destination.
- Compare destination_country with home_location to decide domestic versus international.
- relevant_document_types must be chosen from the document inventory. Add "passport" only when international travel makes it relevant.
- If a document expires before the event ends, include it as relevant even when the event does not obviously need it.
- Never invent a document type.
- If you are unsure, choose "other" and a low confidence.

Event:
{json.dumps({
    "title": event.title,
    "destination": event.destination,
    "destination_country": event.destination_country,
    "home_location": home,
    "start_date": event.depart_date.isoformat() if event.depart_date else None,
    "end_date": event.return_date.isoformat() if event.return_date else None,
    "notes": event.notes,
    "explicit_type": explicit_type,
}, ensure_ascii=False)}

Documents:
{json.dumps(document_lines, ensure_ascii=False)}
"""
    payload = client.chat_json(
        prompt,
        system=(
            "You classify events for a document renewal planner. Return strict JSON only. "
            "Do not invent documents or dates."
        ),
        model=model,
        max_tokens=1200,
    )
    return _normalise_payload(payload, documents, event, home, explicit_type)


def _normalise_payload(
    payload: dict,
    documents: list[Document],
    event: Event,
    home: str,
    explicit_type: str,
) -> EventAnalysis:
    event_type = str(payload.get("event_type") or "").strip().lower()
    if explicit_type:
        event_type = explicit_type
    if event_type not in EVENT_TYPES:
        event_type = _fallback_type(event, home)

    country = (
        str(payload.get("destination_country") or event.destination_country or "")
        .strip()
    )
    deterministic = is_domestic_destination(event.destination, home, country)
    if deterministic is True and not country:
        country = _home_country(home)

    raw_international = _as_bool(payload.get("is_international"))
    if raw_international is None:
        raw_international = event_type == "trip"
    if explicit_type == "trip":
        is_international = True
    elif explicit_type == "road_trip":
        is_international = False
    elif deterministic is not None:
        is_international = not deterministic
    else:
        is_international = raw_international

    if event_type == "trip" and not is_international and not explicit_type:
        event_type = "road_trip"
    if event_type in {"road_trip", "move", "job_start"}:
        is_international = False

    relevant = [
        str(value).strip()
        for value in payload.get("relevant_document_types") or []
        if str(value).strip() in ALLOWED_DOC_TYPES
    ]
    relevant = _add_expiring_documents(relevant, documents, event)

    confidence = _clamp(payload.get("confidence", 0.5))
    if confidence <= 0.0:
        confidence = 0.6
    if event_type == "other" and confidence > 0.6:
        confidence = 0.6

    return EventAnalysis(
        event_type=event_type,
        is_international=is_international,
        home_location=str(payload.get("home_location") or home).strip() or home,
        destination_country=country,
        relevant_document_types=sorted(set(relevant)),
        reason=str(payload.get("reason") or "").strip()
        or f"Read as {EVENT_TYPE_LABELS.get(event_type, event_type)}.",
        confidence=confidence,
        mode="live",
    )


def _fallback_analysis(
    documents: list[Document],
    event: Event,
    event_type: str,
    home: str,
) -> EventAnalysis:
    explicit = event_type in EVENT_TYPES
    event_type = event_type if explicit else _fallback_type(event, home)
    country = (event.destination_country or "").strip()
    deterministic = is_domestic_destination(event.destination, home, country)
    if deterministic is True and not country:
        country = _home_country(home)
    if explicit and event_type == "trip":
        is_international = True
    elif explicit and event_type == "road_trip":
        is_international = False
    elif deterministic is not None:
        is_international = not deterministic
    else:
        is_international = event.is_international or event_type == "trip"
    if event_type == "trip" and not is_international and not explicit:
        event_type = "road_trip"
    if event_type in {"road_trip", "move", "job_start"}:
        is_international = False

    present = {document.doc_type for document in documents if document.doc_type != "other"}
    if event_type == "trip":
        relevant = present | {"passport"}
    elif event_type == "road_trip":
        relevant = (present - {"passport"}) | {"driver_license"}
    elif event_type == "move":
        relevant = (present - {"passport"}) | {"driver_license", "renters_insurance"}
    elif event_type == "job_start":
        relevant = (present - {"passport"}) | {"professional_license"}
    else:
        relevant = present

    relevant = _add_expiring_documents(sorted(relevant), documents, event)
    return EventAnalysis(
        event_type=event_type,
        is_international=is_international,
        home_location=home,
        destination_country=country,
        relevant_document_types=sorted(set(relevant)),
        reason=f"Read as {EVENT_TYPE_LABELS.get(event_type, event_type)} from the event details.",
        confidence=0.45 if home else 0.3,
        mode="fallback",
    )


def _fallback_type(event: Event, home: str) -> str:
    text = f"{event.title} {event.notes} {event.destination}".lower()
    if any(word in text for word in ("move", "relocat", "new home", "new apartment")):
        return "move"
    if any(word in text for word in ("job", "new role", "start work", "new position")):
        return "job_start"
    if any(word in text for word in ("road trip", "roadtrip", "drive", "driving")):
        return "road_trip"
    if event.destination or event.destination_country:
        domestic = is_domestic_destination(
            event.destination, home, event.destination_country
        )
        if domestic is True:
            return "road_trip"
        return "trip"
    return "other"


def _home_country(home: str) -> str:
    if _is_us_text(home):
        return "United States"
    parts = [part.strip() for part in (home or "").split(",") if part.strip()]
    return parts[-1] if parts else (home or "").strip()


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    if isinstance(value, int):
        return bool(value)
    return None


def _add_expiring_documents(
    relevant: list[str],
    documents: list[Document],
    event: Event,
) -> list[str]:
    end = event.return_date or event.depart_date
    if end is None:
        return relevant
    result = set(relevant)
    for document in documents:
        if document.doc_type == "other" or document.expiry_date is None:
            continue
        if document.expiry_date <= end:
            result.add(document.doc_type)
    return sorted(result)


def _clamp(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
