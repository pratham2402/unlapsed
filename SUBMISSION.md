# Devpost submission draft

## Project name

Unlapsed

## Tagline

A private renewal agent that plans the document chain, not just the expiry date.

## Track

Personal AI

## Inspiration

Most people do not miss a renewal because they forgot one date. They miss it because the dates depend on each other.

A passport has to be renewed before a visa. The visa has to be approved before the trip. A professional license needs continuing education hours before it can be renewed. A renters policy can lapse while you are away. Each item looks fine on its own, and the chain still fails.

I wanted a tool that plans the chain, checks the current official rule, and prepares the paperwork. Not another reminder that adds one more line to a calendar.

## What it does

Unlapsed puts the document expiry watch first. It reads text-based PDFs of documents such as a passport, driver's license, insurance policy, and professional license. It extracts the holder, issuer, dates, and constraints, and keeps the exact source line for every fact.

When the user adds a dated event, the agent reads the event text, the destination, the destination country, the home location, and the document inventory. It infers whether the event is domestic or international, which documents can affect it, and how much confidence it has. The user can override the reading. The plan page shows the reading and the reason.

It then searches the official issuer site with Tavily for the current fee, processing time, required documents, and steps. It also checks the destination's passport validity rule and visa requirement on an official source. The result is stored with its URL, checked date, and confidence. If the official source does not state a value, the app marks it "verify manually" instead of guessing. For a non-India destination, a stated or unknown visa requirement becomes a generic visa verification task, not a fabricated application.

It builds a dependency-aware plan backward from the event. The seeded case is a synthetic US professional planning a trip to India. The passport expires on 14 June 2027, and the trip returns on 24 January 2027. The destination requires six months of passport validity beyond the return date, so the passport must be valid through 24 July 2027. The agent builds the passport renewal, then the India e-Visa, then the final travel check. It also flags the renters insurance lapse and the 30 continuing education hours for the professional license as parallel risks.

The same planner handles domestic travel, where the driver's license becomes the blocking document if it expires before the event. It also handles a move, a job start, and a generic dated event. With no event at all, it runs an expiry watch instead of inventing a trip.

It marks what blocks the event and what is a parallel risk, then generates a PDF packet with the checklist and form-ready data and an `.ics` calendar file. It does not estimate trip losses or other personal costs. Official renewal fees are shown in the evidence panel, with their source.

The public demo is read-only. The database is generated state and is seeded on first boot from the sample PDFs, so the public URL does not spend sponsor credits. The video shows the live extraction and verification, and the repository has the full interactive path.

## Why it is different

- It plans a chain, not a list of dates.
- It ties every fee and processing time to an official source with a checked date.
- It prepares a packet, not just a reminder.
- It refuses to invent an answer when the evidence is weak.

## How we built it

- FastAPI and Jinja for the product.
- SQLModel and SQLite for persistent memory, facts, requirements, travel rules, plans, tasks, and the activity log.
- Nebius Token Factory for inference.
- NVIDIA Nemotron Nano for document extraction and event analysis, and Nemotron Super for official-policy and travel-rule extraction.
- Tavily for official-source search with a domain allowlist and citation capture.
- pypdf for text PDF extraction.
- ReportLab for the packet PDF.
- A deterministic planner for the dependency graph, date math, and blocker labels. The model classifies and extracts; it does not decide the legal deadline.
- No LangChain or LangGraph. The orchestration is a small Python service layer. The tools are direct function calls, not MCP servers.

## Model use and cost

The fast model handles document extraction and event analysis. The reasoning model handles official-policy extraction and the destination travel rule. If the reasoning call fails, the app retries once with the fast model, then falls back to a source-only result.

Page loads make no model calls. The plan, evidence, and activity log are read from SQLite. A run makes one event-analysis call, one extraction call per new document, one travel-rule call for an international event, and one policy call per relevant document type. Tavily results, requirements, and travel rules are cached in the database. The planner and the date math spend no tokens.

Hardcoded on purpose: official-source allowlists, date arithmetic, dependency order, blocker labels, safety controls, and data contracts. The model proposes and extracts. The code applies the rule.

## Challenges

- Keeping the model from inventing fees or processing times. Weak evidence becomes "verify manually."
- Making the plan trustworthy. The dates and dependencies are code, not model output.
- Letting the model read the event without letting it silently skip a document. The reading is shown, the user can override it, and any document that expires before the event is added back.
- Replacing the fixed six-month passport rule with an evidence-backed rule from the destination's official source. When the source is missing, the app asks the user to verify the rule instead of assuming a period.
- Resolving a city destination to a country so the passport and visa checks use the right official page. The country field is optional, but it removes ambiguity when the model cannot tell.
- Keeping the demo simple. The app rejects scanned PDFs with a clear message instead of pretending OCR works.

## Accomplishments

- A complete path from PDF to evidence to plan to packet.
- Provenance for every extracted fact.
- A live official-source check that found the passport fee and processing time.
- A visible activity log and a scheduled run script.
- A privacy story: PDFs are read in memory and not stored.

## What we learned

- The hard part of renewals is the dependency chain, not the reminder.
- Official-source verification is more useful than a confident answer.
- A small deterministic core makes the model output much easier to trust.

## What's next

- OCR for scanned documents.
- More country-specific visa workflows, beyond the generic verification task.
- A trusted destination-country resolver for city and region names.
- A source registry with change monitoring.
- Authentication and encrypted storage for a production deployment.

## Built with

Nebius Token Factory, NVIDIA Nemotron, Tavily, FastAPI, SQLModel, SQLite, pypdf, ReportLab, Jinja.

## Links

- Repository: https://github.com/pratham2402/unlapsed
- Demo: https://unlapsed.onrender.com
- Video: https://youtu.be/xwksMIgQR_Y
- Feedback: see `FEEDBACK.md`
