from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import Requirement, TravelRule
from app.services.nebius import NebiusClient, NebiusError
from app.services.tavily import SearchHit, TavilyError, TavilySearch


@dataclass(frozen=True)
class OfficialSource:
    title: str
    query: str
    domains: tuple[str, ...] = ()
    jurisdiction: str = "US"
    manual_only: bool = False
    secondary_query: str = ""


OFFICIAL_SOURCES: dict[str, OfficialSource] = {
    "passport": OfficialSource(
        title="U.S. passport renewal",
        query=(
            "U.S. passport renewal by mail DS-82 current fee standard expedited "
            "processing time required documents six month validity"
        ),
        domains=("travel.state.gov",),
        secondary_query="official U.S. passport processing time routine expedited 2026",
    ),
    "india_evisa": OfficialSource(
        title="India e-Visa for U.S. citizens",
        query=(
            "India e-Visa U.S. citizens current fee processing time required documents "
            "passport six months validity official application"
        ),
        domains=("indianvisaonline.gov.in", "travel.state.gov"),
        jurisdiction="IN",
    ),
    "driver_license": OfficialSource(
        title="California driver's license renewal",
        query=(
            "California driver license renewal current fee processing time required documents "
            "REAL ID official DMV"
        ),
        domains=("dmv.ca.gov",),
    ),
    "professional_license": OfficialSource(
        title="California professional engineer renewal",
        query=(
            "California professional engineer license renewal continuing education PDH "
            "current fee official board"
        ),
        domains=("bpelsg.ca.gov",),
    ),
    "renters_insurance": OfficialSource(
        title="Renters insurance renewal",
        query="renters insurance policy renewal requirements",
        domains=(),
        manual_only=True,
    ),
}


TRAVEL_RULE_DOMAINS = ("travel.state.gov",)


@dataclass
class TravelRuleData:
    destination: str
    home_location: str = "United States"
    passport_validity_months: int | None = None
    rule_text: str = ""
    visa_required: bool | None = None
    visa_type: str = ""
    visa_rule_text: str = ""
    source_title: str = ""
    source_url: str = ""
    source_excerpt: str = ""
    confidence: float = 0.0
    status: str = "needs_manual_check"
    provider: str = "tavily"


@dataclass
class RequirementData:
    doc_type: str
    jurisdiction: str
    title: str
    fee: str = ""
    processing_time: str = ""
    required_documents: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    official_url: str = ""
    source_title: str = ""
    source_excerpt: str = ""
    confidence: float = 0.0
    status: str = "verified"
    provider: str = "tavily"


def verify_requirement(
    doc_type: str,
    jurisdiction: str = "US",
    issuer: str = "",
) -> RequirementData:
    source = OFFICIAL_SOURCES.get(doc_type)
    if source is None:
        return _manual_result(
            doc_type,
            jurisdiction,
            f"No official-source map exists for {doc_type.replace('_', ' ')}.",
        )

    if source.manual_only:
        return _manual_result(
            doc_type,
            source.jurisdiction,
            "Renewal terms come from your policy. Check the declaration page and the insurer's portal.",
            title=source.title,
            issuer=issuer,
        )

    searcher = TavilySearch()
    if not searcher.available:
        return _manual_result(
            doc_type,
            source.jurisdiction,
            "TAVILY_API_KEY is not set. Add it to .env to verify the current official requirement.",
            title=source.title,
        )

    try:
        hits = searcher.search(source.query, include_domains=source.domains, max_results=5)
        hits = _filter_allowed(hits, source.domains)
    except TavilyError as exc:
        return _manual_result(
            doc_type,
            source.jurisdiction,
            f"Official-source search failed: {exc}",
            title=source.title,
        )

    if source.secondary_query and hits:
        try:
            extra = searcher.search(
                source.secondary_query,
                include_domains=source.domains,
                max_results=4,
            )
            hits = _dedupe_hits(_filter_allowed(extra, source.domains) + hits)
        except TavilyError:
            pass

    if not hits:
        try:
            broad_hits = searcher.search(source.query, max_results=8)
            hits = _filter_allowed(broad_hits, source.domains)
        except TavilyError as exc:
            return _manual_result(
                doc_type,
                source.jurisdiction,
                f"Official-source search failed: {exc}",
                title=source.title,
            )

    if not hits:
        return _manual_result(
            doc_type,
            source.jurisdiction,
            "No official source was found for this requirement. Verify it manually before relying on it.",
            title=source.title,
        )

    context = _build_context(hits[:5])
    client = NebiusClient()
    if not client.available:
        return _source_only_result(doc_type, source, hits[0])

    system = (
        "You extract official renewal requirements. Use only the supplied sources. "
        "Never invent a fee, processing time, or document. If the source does not state it, "
        "write 'Not stated in source'. Return strict JSON only."
    )
    prompt = f"""
Extract the current renewal requirement for a {doc_type.replace('_', ' ')} in {source.jurisdiction}.
Return one JSON object:

{{
  "title": "short official title",
  "fee": "exact fee as stated, or 'Not stated in source'",
  "processing_time": "exact processing time as stated, or 'Not stated in source'",
  "required_documents": ["..."],
  "steps": ["..."],
  "official_url": "one of the supplied URLs",
  "confidence": 0.0,
  "notes": "anything important about eligibility or timing"
}}

Only use the sources below. Do not use outside knowledge.

Sources:
{context}
"""
    try:
        payload = client.chat_json(
            prompt,
            system=system,
            model=client.reasoning_model,
            max_tokens=1800,
        )
    except NebiusError:
        try:
            payload = client.chat_json(
                prompt,
                system=system,
                model=client.fast_model,
                max_tokens=2500,
            )
        except NebiusError:
            return _source_only_result(doc_type, source, hits[0])

    official_url = str(payload.get("official_url") or "")
    chosen = _match_hit(official_url, hits) or hits[0]
    if not chosen.url.startswith(("http://", "https://")):
        chosen = hits[0]
    confidence = _clamp(payload.get("confidence", 0.6))
    fee = str(payload.get("fee") or "Not stated in source").strip()
    processing = str(payload.get("processing_time") or "Not stated in source").strip()

    status = "verified"
    if fee.lower().startswith("not stated") and processing.lower().startswith("not stated"):
        status = "needs_manual_check"
    if confidence < 0.45:
        status = "needs_manual_check"

    return RequirementData(
        doc_type=doc_type,
        jurisdiction=source.jurisdiction,
        title=str(payload.get("title") or source.title).strip(),
        fee=fee,
        processing_time=processing,
        required_documents=_string_list(payload.get("required_documents")),
        steps=_string_list(payload.get("steps")),
        official_url=chosen.url,
        source_title=chosen.title,
        source_excerpt=chosen.content[:500],
        confidence=confidence,
        status=status,
        provider="tavily",
    )


def verify_travel_rule(
    destination: str,
    home_location: str = "United States",
) -> TravelRuleData:
    """Read the destination's passport validity rule from an official source."""
    destination = (destination or "").strip()
    home_location = (home_location or "United States").strip()
    if not destination:
        return TravelRuleData(
            destination=destination,
            home_location=home_location,
            rule_text="No destination was supplied.",
            status="needs_manual_check",
        )

    home_text = home_location.lower().strip()
    is_us_home = home_text in {"us", "u.s.", "usa"} or any(
        alias in home_text
        for alias in ("united states", "u.s.", "usa", "america")
    )
    if not is_us_home:
        return TravelRuleData(
            destination=destination,
            home_location=home_location,
            rule_text=(
                "The travel-rule source registry is currently US-focused. "
                "Verify the passport validity rule with your own government."
            ),
            status="needs_manual_check",
        )

    searcher = TavilySearch()
    if not searcher.available:
        return TravelRuleData(
            destination=destination,
            home_location=home_location,
            rule_text=(
                "TAVILY_API_KEY is not set. Verify the destination's passport "
                "validity rule manually."
            ),
            status="needs_manual_check",
        )

    query = (
        f"{destination} Travel Advisory passport validity entry requirements "
        "official travel.state.gov"
    )
    secondary = (
        f"{destination} country information passport must be valid "
        "official travel.state.gov"
    )
    try:
        hits = searcher.search(
            query, include_domains=TRAVEL_RULE_DOMAINS, max_results=8
        )
        hits = _filter_travel_pages(
            _filter_allowed(hits, TRAVEL_RULE_DOMAINS), destination
        )
        if not hits:
            extra = searcher.search(
                secondary, include_domains=TRAVEL_RULE_DOMAINS, max_results=8
            )
            hits = _filter_travel_pages(
                _filter_allowed(extra, TRAVEL_RULE_DOMAINS), destination
            )
    except TavilyError as exc:
        return TravelRuleData(
            destination=destination,
            home_location=home_location,
            rule_text=f"Official-source search failed: {exc}",
            status="needs_manual_check",
        )

    if not hits:
        return TravelRuleData(
            destination=destination,
            home_location=home_location,
            rule_text=(
                "No official destination page was found. Verify the passport "
                "validity rule manually."
            ),
            status="needs_manual_check",
        )

    client = NebiusClient()
    if not client.available:
        return _travel_rule_source_only(destination, home_location, hits[0])

    prompt = f"""
Extract the passport validity rule for a traveler from {home_location} visiting {destination}.
Return one JSON object:

{{
  "passport_validity_months": 6,
  "rule_text": "the exact rule in one sentence, or 'Not stated in source'",
  "visa_required": true,
  "visa_type": "short label such as Schengen visa, e-Visa, visa on arrival, or none",
  "visa_rule_text": "the exact visa rule in one sentence, or 'Not stated in source'",
  "official_url": "one of the supplied URLs",
  "confidence": 0.0,
  "notes": "anything important about entry or transit"
}}

Rules:
- Use only the supplied official sources.
- Prefer the destination-specific Travel Advisory or Country Information page.
- Ignore visa reciprocity tables.
- Return an integer number of months only when the source states it.
- Six months beyond arrival, departure, or return all mean 6.
- If the source says the passport must simply be valid at entry, return 0.
- If the source does not state a validity period, return null.
- For visa_required, return true only when the source says a visa is required, false when it says no visa is required, and null when the source does not state it.
- Confidence is for the passport validity rule. Do not set it to zero only because the visa type is missing.
- Never invent a passport or visa rule.

Sources:
{_build_context(hits[:5])}
"""
    try:
        payload = client.chat_json(
            prompt,
            system=(
                "You extract official passport validity rules. Use only the supplied "
                "sources. Return strict JSON only. Never invent a rule."
            ),
            model=client.reasoning_model,
            max_tokens=1200,
        )
    except NebiusError:
        try:
            payload = client.chat_json(
                prompt,
                system=(
                    "You extract official passport validity rules. Use only the supplied "
                    "sources. Return strict JSON only. Never invent a rule."
                ),
                model=client.fast_model,
                max_tokens=1600,
            )
        except NebiusError:
            return _travel_rule_source_only(destination, home_location, hits[0])

    months = _optional_int(payload.get("passport_validity_months"))
    official_url = str(payload.get("official_url") or "")
    chosen = _match_hit(official_url, hits) or hits[0]
    rule_text = str(payload.get("rule_text") or "").strip()
    visa_required = _optional_bool(payload.get("visa_required"))
    visa_type = str(payload.get("visa_type") or "").strip()
    visa_rule_text = str(payload.get("visa_rule_text") or "").strip()
    if visa_type.lower().startswith("not stated"):
        visa_type = ""
    if visa_rule_text.lower().startswith("not stated"):
        visa_rule_text = ""
    confidence = _clamp(payload.get("confidence", 0.6))

    candidates = _month_candidates(f"{rule_text} {chosen.content}")
    mismatch = months is not None and bool(candidates) and months not in candidates

    status = "verified"
    if months is None or not rule_text or rule_text.lower().startswith("not stated"):
        status = "needs_manual_check"
    if mismatch:
        status = "needs_manual_check"
        confidence = min(confidence, 0.4)
    elif status == "verified" and confidence < 0.6:
        confidence = 0.6
    if confidence < 0.45:
        status = "needs_manual_check"

    return TravelRuleData(
        destination=destination,
        home_location=home_location,
        passport_validity_months=months,
        rule_text=rule_text or "Not stated in source",
        visa_required=visa_required,
        visa_type=visa_type,
        visa_rule_text=visa_rule_text,
        source_title=chosen.title,
        source_url=chosen.url,
        source_excerpt=chosen.content[:500],
        confidence=confidence,
        status=status,
        provider="tavily",
    )


def _travel_rule_source_only(
    destination: str,
    home_location: str,
    hit: SearchHit,
) -> TravelRuleData:
    return TravelRuleData(
        destination=destination,
        home_location=home_location,
        rule_text="Official page found, but the validity period was not extracted.",
        source_title=hit.title,
        source_url=hit.url,
        source_excerpt=hit.content[:500],
        confidence=0.3,
        status="needs_manual_check",
    )


def upsert_travel_rule(session: Session, data: TravelRuleData) -> TravelRule:
    latest = get_latest_travel_rule(session, data.destination)
    if latest and _same_travel_rule(latest, data):
        latest.checked_at = datetime.now(timezone.utc)
        session.add(latest)
        session.commit()
        session.refresh(latest)
        return latest

    rule = TravelRule(
        destination=data.destination,
        home_location=data.home_location,
        passport_validity_months=data.passport_validity_months,
        rule_text=data.rule_text,
        visa_required=data.visa_required,
        visa_type=data.visa_type,
        visa_rule_text=data.visa_rule_text,
        source_title=data.source_title,
        source_url=data.source_url,
        source_excerpt=data.source_excerpt,
        confidence=data.confidence,
        status=data.status,
        provider=data.provider,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def get_latest_travel_rule(session: Session, destination: str) -> TravelRule | None:
    statement = (
        select(TravelRule)
        .where(func.lower(TravelRule.destination) == (destination or "").strip().lower())
        .order_by(TravelRule.checked_at.desc(), TravelRule.id.desc())
    )
    return session.exec(statement).first()


def upsert_requirement(session: Session, data: RequirementData) -> Requirement:
    latest = get_latest_requirement(session, data.doc_type)
    if latest and _same_requirement(latest, data):
        latest.checked_at = datetime.now(timezone.utc)
        session.add(latest)
        session.commit()
        session.refresh(latest)
        return latest

    requirement = Requirement(
        doc_type=data.doc_type,
        jurisdiction=data.jurisdiction,
        title=data.title,
        fee=data.fee,
        processing_time=data.processing_time,
        required_documents_json=json.dumps(data.required_documents),
        steps_json=json.dumps(data.steps),
        official_url=data.official_url,
        source_title=data.source_title,
        source_excerpt=data.source_excerpt,
        confidence=data.confidence,
        status=data.status,
        provider=data.provider,
    )
    session.add(requirement)
    session.commit()
    session.refresh(requirement)
    return requirement


def get_latest_requirement(session: Session, doc_type: str) -> Requirement | None:
    statement = (
        select(Requirement)
        .where(Requirement.doc_type == doc_type)
        .order_by(Requirement.checked_at.desc(), Requirement.id.desc())
    )
    return session.exec(statement).first()


def seed_fallback_requirements(session: Session) -> None:
    """Seed clearly-labelled sample requirements for offline demo mode."""
    samples = [
        RequirementData(
            doc_type="passport",
            jurisdiction="US",
            title="U.S. passport renewal (DS-82)",
            fee="Check travel.state.gov for the current fee",
            processing_time="Currently 6 to 8 weeks standard, 2 to 3 weeks expedited",
            required_documents=[
                "Most recent passport",
                "One passport photo",
                "Form DS-82",
                "Fee payment",
            ],
            steps=[
                "Confirm you are eligible to renew by mail",
                "Complete Form DS-82",
                "Mail the form, photo, and fee",
                "Track the application",
            ],
            official_url="https://travel.state.gov/content/travel/en/passports/have-passport/renew.html",
            source_title="U.S. Department of State: Renew by Mail",
            source_excerpt="Sample data. Live mode replaces this with a current official-source result.",
            confidence=0.5,
            status="fallback",
            provider="seed",
        ),
        RequirementData(
            doc_type="india_evisa",
            jurisdiction="IN",
            title="India e-Visa for U.S. citizens",
            fee="Check indianvisaonline.gov.in for the current fee",
            processing_time="Typically 4 to 7 business days",
            required_documents=[
                "Passport valid for at least six months",
                "Recent photograph",
                "Email address",
                "Debit or credit card",
            ],
            steps=[
                "Complete the online application",
                "Pay the e-Visa fee",
                "Receive the Electronic Travel Authorization",
                "Carry a printed copy when you travel",
            ],
            official_url="https://indianvisaonline.gov.in/evisa/",
            source_title="Indian Visa Online: e-Visa",
            source_excerpt="Sample data. Live mode replaces this with a current official-source result.",
            confidence=0.5,
            status="fallback",
            provider="seed",
        ),
        RequirementData(
            doc_type="driver_license",
            jurisdiction="US",
            title="California driver's license renewal",
            fee="Check dmv.ca.gov for the current fee",
            processing_time="Online renewal is usually same-day; mail takes longer",
            required_documents=["Renewal notice", "Fee", "Vision test if required"],
            steps=["Renew online or in person", "Update to REAL ID if you have not already"],
            official_url="https://www.dmv.ca.gov/portal/driver-licenses-identification-cards/renew-your-driver-license/",
            source_title="California DMV: Renew your driver's license",
            source_excerpt="Sample data. Live mode replaces this with a current official-source result.",
            confidence=0.5,
            status="fallback",
            provider="seed",
        ),
        RequirementData(
            doc_type="professional_license",
            jurisdiction="US",
            title="California professional engineer renewal",
            fee="Check bpelsg.ca.gov for the current fee",
            processing_time="Varies by renewal cycle",
            required_documents=[
                "Continuing education records",
                "Renewal fee",
                "Online renewal confirmation",
            ],
            steps=[
                "Complete the required professional development hours",
                "Renew online with the board",
                "Keep your records for an audit",
            ],
            official_url="https://www.bpelsg.ca.gov/licensees/renewal.shtml",
            source_title="California Board for Professional Engineers: Renewal",
            source_excerpt="Sample data. Live mode replaces this with a current official-source result.",
            confidence=0.5,
            status="fallback",
            provider="seed",
        ),
        RequirementData(
            doc_type="renters_insurance",
            jurisdiction="US",
            title="Renters insurance renewal",
            fee="From your policy",
            processing_time="Renew before the policy expiration date",
            required_documents=["Policy declaration page", "Premium payment"],
            steps=["Review coverage and deductibles", "Pay the premium before expiration"],
            official_url="",
            source_title="Your policy document",
            source_excerpt="The renewal terms come from your policy, not a public agency.",
            confidence=0.2,
            status="needs_manual_check",
            provider="seed",
        ),
    ]

    for sample in samples:
        if get_latest_requirement(session, sample.doc_type) is None:
            upsert_requirement(session, sample)


def _manual_result(
    doc_type: str,
    jurisdiction: str,
    note: str,
    title: str | None = None,
    issuer: str = "",
) -> RequirementData:
    return RequirementData(
        doc_type=doc_type,
        jurisdiction=jurisdiction,
        title=title or doc_type.replace("_", " ").title(),
        fee="",
        processing_time="",
        required_documents=[],
        steps=[],
        official_url="",
        source_title=issuer or "Manual verification required",
        source_excerpt=note,
        confidence=0.0,
        status="needs_manual_check",
        provider="manual",
    )


def _source_only_result(
    doc_type: str,
    source: OfficialSource,
    hit: SearchHit,
) -> RequirementData:
    return RequirementData(
        doc_type=doc_type,
        jurisdiction=source.jurisdiction,
        title=source.title,
        official_url=hit.url,
        source_title=hit.title,
        source_excerpt=hit.content[:500],
        confidence=0.35,
        status="needs_manual_check",
        provider="tavily",
    )


def _dedupe_hits(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    unique: list[SearchHit] = []
    for hit in hits:
        if hit.url in seen:
            continue
        seen.add(hit.url)
        unique.append(hit)
    return unique


def _filter_allowed(hits: list[SearchHit], domains: tuple[str, ...]) -> list[SearchHit]:
    if not domains:
        return hits
    return [hit for hit in hits if _host_allowed(hit.url, domains)]


def _host_allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    for domain in domains:
        domain = domain.lower()
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _filter_travel_pages(
    hits: list[SearchHit],
    destination: str = "",
) -> list[SearchHit]:
    """Prefer destination travel pages over visa reciprocity tables."""
    slug = re.sub(r"[^a-z0-9]+", "-", (destination or "").lower()).strip("-")
    without_visa_tables = [
        hit for hit in hits if "visa-reciprocity" not in hit.url.lower()
    ]
    destination_hits = [
        hit for hit in without_visa_tables if slug and slug in hit.url.lower()
    ]
    if destination_hits:
        return destination_hits

    preferred = [
        hit
        for hit in without_visa_tables
        if "travel-advisories" in hit.url or "international-travel" in hit.url
    ]
    if preferred:
        return preferred
    return without_visa_tables or hits


def _match_hit(url: str, hits: list[SearchHit]) -> SearchHit | None:
    if not url:
        return None
    for hit in hits:
        if hit.url == url:
            return hit
    host = urlparse(url).netloc.lower()
    for hit in hits:
        if urlparse(hit.url).netloc.lower() == host:
            return hit
    return None


def _build_context(hits: list[SearchHit]) -> str:
    parts = []
    for index, hit in enumerate(hits, start=1):
        parts.append(
            f"[Source {index}]\nTitle: {hit.title}\nURL: {hit.url}\nContent: {hit.content[:4000]}"
        )
    return "\n\n".join(parts)


def _same_requirement(existing: Requirement, data: RequirementData) -> bool:
    return (
        existing.fee == data.fee
        and existing.processing_time == data.processing_time
        and existing.official_url == data.official_url
        and existing.status == data.status
        and existing.required_documents == data.required_documents
    )


def _same_travel_rule(existing: TravelRule, data: TravelRuleData) -> bool:
    return (
        existing.home_location == data.home_location
        and existing.passport_validity_months == data.passport_validity_months
        and existing.visa_required == data.visa_required
        and existing.visa_type == data.visa_type
        and existing.visa_rule_text == data.visa_rule_text
        and existing.source_url == data.source_url
        and existing.status == data.status
        and existing.rule_text == data.rule_text
    )


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "required", "1"}:
            return True
        if text in {"false", "no", "not required", "0"}:
            return False
    if isinstance(value, int):
        return bool(value)
    return None


def _month_candidates(text: str) -> set[int]:
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    pattern = re.compile(
        r"(?P<number>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?:\s*\(\s*\d+\s*\))?\s*months?",
        re.IGNORECASE,
    )
    found: set[int] = set()
    for match in pattern.finditer(text or ""):
        raw = match.group("number").lower()
        value = int(raw) if raw.isdigit() else words.get(raw)
        if value is not None:
            found.add(value)
    return found


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _clamp(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
