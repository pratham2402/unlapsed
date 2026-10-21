from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session, init_db
from app.models import (
    Activity,
    AgentRun,
    Document,
    Event,
    Fact,
    Packet,
    Plan,
    PlanTask,
)
from app.services.agent import run_agent
from app.services.demo import seed_demo
from app.services.extraction import (
    ExtractionError,
    LowTextError,
    extract_document_from_bytes,
)
from app.services.packet import generate_calendar
from app.services.planner import get_active_plan, get_latest_event
from app.services.tracing import configure_tracing
from app.services.verification import get_latest_requirement

BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_tracing()
    init_db()
    yield


app = FastAPI(title="Unlapsed", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def _usd(cents: int | None) -> str:
    return f"${(cents or 0) / 100:,.0f}"


def _datefmt(value, fmt: str = "%d %b %Y") -> str:
    if not value:
        return "Not set"
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime(fmt)


def _datetimefmt(value, fmt: str = "%d %b %Y, %H:%M") -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.strftime(fmt) + " UTC"
    return value.strftime(fmt)


def _status_class(value: str | None) -> str:
    status = (value or "").lower()
    if status in {"verified", "valid"}:
        return "status-verified"
    if status in {"needs_manual_check", "expiring", "fallback"}:
        return "status-watch"
    if status in {"expired", "error"}:
        return "status-alert"
    return "status-neutral"


def _status_text(value: str | None) -> str:
    return {
        "fallback": "sample",
        "needs_manual_check": "verify manually",
        "simulated": "simulated",
        "verified": "verified",
        "valid": "valid",
        "expiring": "expiring",
        "expired": "expired",
    }.get((value or "").lower(), (value or "").replace("_", " "))


def _mode_text(value: str | None) -> str:
    return {
        "fallback": "sample",
        "live": "live",
        "manual": "manual",
    }.get((value or "").lower(), value or "")


templates.env.filters["usd"] = _usd
templates.env.filters["datefmt"] = _datefmt
templates.env.filters["datetimefmt"] = _datetimefmt
templates.env.filters["status_class"] = _status_class
templates.env.filters["status_text"] = _status_text
templates.env.filters["mode_text"] = _mode_text


def _read_only() -> bool:
    return get_settings().demo_read_only


def _context(request: Request, session: Session, **extra) -> dict:
    settings = get_settings()
    user_id = settings.demo_user_email
    documents = session.exec(select(Document).where(Document.user_id == user_id)).all()
    plan = get_active_plan(session, user_id)
    last_run = session.exec(
        select(AgentRun)
        .where(AgentRun.user_id == user_id)
        .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
    ).first()
    requirements = {}
    for doc_type in ("passport", "india_evisa", "driver_license", "professional_license", "renters_insurance"):
        requirement = get_latest_requirement(session, doc_type)
        if requirement is not None:
            requirements[doc_type] = requirement

    context = {
        "request": request,
        "settings": settings,
        "user_id": user_id,
        "holder_name": next((doc.holder_name for doc in documents if doc.holder_name), "Alex Morgan"),
        "documents": documents,
        "plan": plan,
        "last_run": last_run,
        "requirements": requirements,
        "live_nebius": settings.live_nebius,
        "live_tavily": settings.live_tavily,
        "now": datetime.now(timezone.utc),
        "path": request.url.path,
        "msg": request.query_params.get("msg"),
        "error": request.query_params.get("error"),
    }
    context.update(extra)
    return context


@app.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    user_id = get_settings().demo_user_email
    tasks = []
    if plan := get_active_plan(session, user_id):
        tasks = session.exec(
            select(PlanTask)
            .where(PlanTask.plan_id == plan.id)
            .order_by(PlanTask.due_date, PlanTask.sort_order)
        ).all()
    next_tasks = sorted(
        [task for task in tasks if task.status != "done"],
        key=lambda task: (not task.blocking, task.due_date or date.max, task.sort_order),
    )[:4]
    conflict_doc_ids = {
        task.document_id
        for task in tasks
        if task.document_id is not None and task.blocking
    }

    headline = "Add a trip to see what needs renewing first."
    if plan:
        critical = next(
            (task for task in next_tasks if task.priority == "critical" and task.due_date),
            None,
        )
        if critical:
            headline = f"{critical.title} by {critical.due_date.strftime('%d %B')}."
        else:
            headline = plan.summary.split(".")[0] + "."

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_context(
            request,
            session,
            tasks=tasks,
            next_tasks=next_tasks,
            headline=headline,
            conflict_doc_ids=conflict_doc_ids,
        ),
    )


@app.get("/documents")
def documents(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="documents.html",
        context=_context(request, session),
    )


@app.get("/documents/{document_id}")
def document_detail(document_id: int, request: Request, session: Session = Depends(get_session)):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    facts = session.exec(
        select(Fact).where(Fact.document_id == document.id).order_by(Fact.id)
    ).all()
    requirement = get_latest_requirement(session, document.doc_type)
    return templates.TemplateResponse(
        request=request,
        name="document_detail.html",
        context=_context(
            request,
            session,
            document=document,
            facts=facts,
            requirement=requirement,
        ),
    )


@app.post("/documents/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    if _read_only():
        return RedirectResponse(
            "/documents?error=The+public+demo+is+read-only.+Run+the+project+locally+to+upload.",
            status_code=303,
        )
    if file.size and file.size > 10 * 1024 * 1024:
        return RedirectResponse("/documents?error=File+is+larger+than+10+MB", status_code=303)
    if not (file.filename or "").lower().endswith(".pdf"):
        return RedirectResponse("/documents?error=Upload+a+PDF+file", status_code=303)
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        return RedirectResponse("/documents?error=File+is+larger+than+10+MB", status_code=303)
    try:
        extracted = extract_document_from_bytes(data, file.filename or "upload.pdf")
    except LowTextError as exc:
        return RedirectResponse(f"/documents?error={_quote(str(exc))}", status_code=303)
    except ExtractionError as exc:
        return RedirectResponse(f"/documents?error={_quote(str(exc))}", status_code=303)

    user_id = get_settings().demo_user_email
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
        source_filename=file.filename or "upload.pdf",
        extraction_confidence=extracted.confidence,
        extraction_mode=extracted.mode,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

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

    run_agent(session, user_id, run_type="manual")
    return RedirectResponse(f"/documents/{document.id}?msg=Document+extracted", status_code=303)


@app.get("/events/new")
def new_event(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="event_new.html",
        context=_context(request, session),
    )


@app.post("/events")
def create_event(
    request: Request,
    title: str = Form(...),
    destination: str = Form(""),
    depart_date: str = Form(""),
    return_date: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    if _read_only():
        return RedirectResponse(
            "/events/new?error=The+public+demo+is+read-only.+Run+the+project+locally+to+add+an+event.",
            status_code=303,
        )
    user_id = get_settings().demo_user_email
    try:
        depart = date.fromisoformat(depart_date) if depart_date else None
        ret = date.fromisoformat(return_date) if return_date else None
    except ValueError:
        return RedirectResponse("/events/new?error=Use+a+valid+date", status_code=303)

    if depart and ret and ret < depart:
        return RedirectResponse(
            "/events/new?error=Return+date+must+be+after+departure",
            status_code=303,
        )

    event = Event(
        user_id=user_id,
        event_type="trip",
        title=title.strip() or "Trip",
        destination=destination.strip(),
        depart_date=depart,
        return_date=ret,
        notes=notes.strip(),
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    run = run_agent(session, user_id, run_type="manual")
    plan = get_active_plan(session, user_id)
    if plan is None:
        return RedirectResponse("/?error=Agent+could+not+build+a+plan", status_code=303)
    suffix = "created" if run.status == "success" else f"error={_quote(run.summary)}"
    return RedirectResponse(f"/plans/{plan.id}?msg={suffix}", status_code=303)


@app.get("/plans/{plan_id}")
def plan_detail(plan_id: int, request: Request, session: Session = Depends(get_session)):
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    tasks = session.exec(
        select(PlanTask).where(PlanTask.plan_id == plan.id).order_by(PlanTask.sort_order)
    ).all()
    event = session.get(Event, plan.event_id) if plan.event_id else None
    packet = session.exec(
        select(Packet).where(Packet.plan_id == plan.id).order_by(Packet.created_at.desc())
    ).first()
    blocking = [task for task in tasks if task.blocking]
    parallel = [task for task in tasks if not task.blocking]
    return templates.TemplateResponse(
        request=request,
        name="plan.html",
        context=_context(
            request,
            session,
            plan=plan,
            tasks=tasks,
            blocking_tasks=blocking,
            parallel_tasks=parallel,
            event=event,
            packet=packet,
        ),
    )


@app.post("/plans/{plan_id}/run")
def run_plan(plan_id: int, request: Request, session: Session = Depends(get_session)):
    if _read_only():
        return RedirectResponse(
            f"/plans/{plan_id}?error=The+public+demo+is+read-only.",
            status_code=303,
        )
    if session.get(Plan, plan_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    run = run_agent(session, get_settings().demo_user_email, run_type="manual")
    if run.status == "error":
        return RedirectResponse(f"/plans/{plan_id}?error={_quote(run.summary)}", status_code=303)
    return RedirectResponse(f"/plans/{plan_id}?msg=Agent+run+complete", status_code=303)


@app.get("/plans/{plan_id}/packet.pdf")
def download_packet(plan_id: int, session: Session = Depends(get_session)):
    packet = session.exec(
        select(Packet).where(Packet.plan_id == plan_id).order_by(Packet.created_at.desc())
    ).first()
    if packet is None:
        raise HTTPException(status_code=404, detail="Packet not generated yet")
    path = get_settings().generated_dir / Path(packet.filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Packet file is missing")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"unlapsed-packet-{plan_id}.pdf",
    )


@app.get("/plans/{plan_id}/calendar.ics")
def download_calendar(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    tasks = session.exec(
        select(PlanTask).where(PlanTask.plan_id == plan.id).order_by(PlanTask.sort_order)
    ).all()
    body = generate_calendar(plan, tasks)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="unlapsed-plan-{plan.id}.ics"'},
    )


@app.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int, session: Session = Depends(get_session)):
    task = session.get(PlanTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if _read_only():
        return RedirectResponse(
            f"/plans/{task.plan_id}?error=The+public+demo+is+read-only.",
            status_code=303,
        )
    task.status = "todo" if task.status == "done" else "done"
    session.add(task)
    session.commit()
    return RedirectResponse(f"/plans/{task.plan_id}", status_code=303)


@app.get("/activity")
def activity(request: Request, session: Session = Depends(get_session)):
    user_id = get_settings().demo_user_email
    activities = session.exec(
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(Activity.created_at.desc(), Activity.id.desc())
        .limit(120)
    ).all()
    runs = session.exec(
        select(AgentRun)
        .where(AgentRun.user_id == user_id)
        .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(12)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="activity.html",
        context=_context(request, session, activities=activities, runs=runs),
    )


@app.get("/privacy")
def privacy(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context=_context(request, session),
    )


@app.post("/privacy/documents/{document_id}/delete")
def delete_document(document_id: int, session: Session = Depends(get_session)):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if _read_only():
        return RedirectResponse(
            "/privacy?error=The+public+demo+is+read-only.",
            status_code=303,
        )
    facts = session.exec(select(Fact).where(Fact.document_id == document.id)).all()
    for fact in facts:
        session.delete(fact)
    session.delete(document)
    session.commit()
    run_agent(session, get_settings().demo_user_email, run_type="manual")
    return RedirectResponse("/privacy?msg=Document+deleted", status_code=303)


@app.get("/privacy/export")
def export_data(session: Session = Depends(get_session)):
    user_id = get_settings().demo_user_email
    documents = session.exec(select(Document).where(Document.user_id == user_id)).all()
    facts = session.exec(
        select(Fact).where(Fact.document_id.in_([doc.id for doc in documents]))
    ).all() if documents else []
    plan = get_active_plan(session, user_id)
    tasks = session.exec(select(PlanTask).where(PlanTask.plan_id == plan.id)).all() if plan else []
    payload = {
        "user": user_id,
        "documents": [
            {
                "doc_type": doc.doc_type,
                "title": doc.title,
                "issuer": doc.issuer,
                "holder_name": doc.holder_name,
                "number_last4": doc.document_number_last4,
                "issue_date": doc.issue_date.isoformat() if doc.issue_date else None,
                "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
                "constraints": doc.constraints,
            }
            for doc in documents
        ],
        "facts": [
            {
                "document_id": fact.document_id,
                "key": fact.key,
                "value": fact.value,
                "source_page": fact.source_page,
                "source_excerpt": fact.source_excerpt,
            }
            for fact in facts
        ],
        "plan": {
            "title": plan.title,
            "summary": plan.summary,
            "risk_usd": plan.risk_total_usd,
        } if plan else None,
        "tasks": [
            {
                "title": task.title,
                "start_date": task.start_date.isoformat() if task.start_date else None,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "blocking": task.blocking,
                "cost_usd": task.cost_of_missing_usd,
            }
            for task in tasks
        ],
    }
    return JSONResponse(payload)


@app.post("/demo/reset")
def reset_demo(session: Session = Depends(get_session)):
    if _read_only():
        return RedirectResponse(
            "/privacy?error=The+public+demo+is+read-only.",
            status_code=303,
        )
    plan = seed_demo(session, reset=True)
    if plan is None:
        return RedirectResponse("/?error=Could+not+seed+the+demo", status_code=303)
    return RedirectResponse(f"/plans/{plan.id}?msg=Demo+reset", status_code=303)


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "live_nebius": settings.live_nebius,
        "live_tavily": settings.live_tavily,
        "fast_model": settings.nebius_model_fast,
        "reasoning_model": settings.nebius_model_reasoning,
    }


def _quote(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value[:180])
