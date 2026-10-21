from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    doc_type: str = Field(index=True)
    title: str
    issuer: str = ""
    holder_name: str = ""
    document_number_last4: str = ""
    issue_date: date | None = None
    expiry_date: date | None = None
    renewal_window_days: int = 180
    constraints_json: str = "{}"
    source_filename: str = ""
    extraction_confidence: float = 0.0
    extraction_mode: str = "live"
    status: str = "valid"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def constraints(self) -> dict[str, Any]:
        return _load_json(self.constraints_json, {})

    @property
    def days_until_expiry(self) -> int | None:
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def is_expired(self) -> bool:
        return self.days_until_expiry is not None and self.days_until_expiry < 0

    @property
    def is_expiring(self) -> bool:
        days = self.days_until_expiry
        return days is not None and 0 <= days <= self.renewal_window_days

    @property
    def status_label(self) -> str:
        if self.is_expired:
            return "expired"
        if self.is_expiring:
            return "expiring"
        return "valid"


class Fact(SQLModel, table=True):
    __tablename__ = "facts"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True, foreign_key="documents.id")
    key: str
    value: str
    source_page: int | None = None
    source_excerpt: str = ""
    confidence: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class Requirement(SQLModel, table=True):
    __tablename__ = "requirements"

    id: int | None = Field(default=None, primary_key=True)
    doc_type: str = Field(index=True)
    jurisdiction: str = "US"
    title: str
    fee: str = ""
    processing_time: str = ""
    required_documents_json: str = "[]"
    steps_json: str = "[]"
    official_url: str = ""
    source_title: str = ""
    source_excerpt: str = ""
    checked_at: datetime = Field(default_factory=utcnow)
    confidence: float = 0.0
    status: str = "verified"
    provider: str = "tavily"

    @property
    def required_documents(self) -> list[str]:
        return _load_json(self.required_documents_json, [])

    @property
    def steps(self) -> list[str]:
        return _load_json(self.steps_json, [])

    @property
    def is_verified(self) -> bool:
        return self.status == "verified"


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    event_type: str = "auto"
    title: str
    destination: str = ""
    destination_country: str = ""
    home_location: str = ""
    is_international: bool = False
    event_type_confidence: float = 0.0
    event_type_reason: str = ""
    relevant_docs_json: str = "[]"
    depart_date: date | None = None
    return_date: date | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def relevant_doc_types(self) -> list[str]:
        return _load_json(self.relevant_docs_json, [])


class TravelRule(SQLModel, table=True):
    __tablename__ = "travel_rules"

    id: int | None = Field(default=None, primary_key=True)
    destination: str = Field(index=True)
    home_location: str = ""
    passport_validity_months: int | None = None
    rule_text: str = ""
    visa_required: bool | None = None
    visa_type: str = ""
    visa_rule_text: str = ""
    source_title: str = ""
    source_url: str = ""
    source_excerpt: str = ""
    checked_at: datetime = Field(default_factory=utcnow)
    confidence: float = 0.0
    status: str = "needs_manual_check"
    provider: str = "tavily"


class Plan(SQLModel, table=True):
    __tablename__ = "plans"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    event_id: int | None = Field(default=None, foreign_key="events.id")
    title: str
    summary: str = ""
    status: str = "active"
    # Legacy field kept for SQLite compatibility. The product no longer uses
    # invented dollar risk; consequences are expressed as blocking or parallel.
    risk_total_cents: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def risk_total_usd(self) -> float:
        return self.risk_total_cents / 100


class PlanTask(SQLModel, table=True):
    __tablename__ = "plan_tasks"

    id: int | None = Field(default=None, primary_key=True)
    plan_id: int = Field(index=True, foreign_key="plans.id")
    document_id: int | None = Field(default=None, foreign_key="documents.id")
    title: str
    description: str = ""
    phase: str = ""
    start_date: date | None = None
    due_date: date | None = None
    depends_on_json: str = "[]"
    blocking: bool = True
    # Legacy field kept for SQLite compatibility. Always zero in new plans.
    cost_of_missing_cents: int = 0
    priority: str = "medium"
    status: str = "todo"
    evidence_url: str = ""
    sort_order: int = 0

    @property
    def depends_on(self) -> list[str]:
        return _load_json(self.depends_on_json, [])

    @property
    def cost_of_missing_usd(self) -> float:
        return self.cost_of_missing_cents / 100


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    run_type: str = "manual"
    status: str = "running"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    summary: str = ""
    changes_json: str = "[]"
    model: str = ""
    trace_url: str = ""

    @property
    def changes(self) -> list[str]:
        return _load_json(self.changes_json, [])


class Activity(SQLModel, table=True):
    __tablename__ = "activities"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    run_id: int | None = Field(default=None, foreign_key="agent_runs.id")
    kind: str = "system"
    message: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Packet(SQLModel, table=True):
    __tablename__ = "packets"

    id: int | None = Field(default=None, primary_key=True)
    plan_id: int = Field(index=True, foreign_key="plans.id")
    filename: str
    created_at: datetime = Field(default_factory=utcnow)
