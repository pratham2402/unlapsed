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

Unlapsed reads text-based PDFs of documents such as a passport, driver's license, insurance policy, and professional license. It extracts the holder, issuer, dates, and constraints, and keeps the exact source line for every fact.

It then searches the official issuer site with Tavily for the current fee, processing time, required documents, and steps. The result is stored with its URL, checked date, and confidence. If the official source does not state a value, the app marks it "verify manually" instead of guessing.

It builds a dependency-aware plan backward from a life event. The seeded case is a synthetic US professional planning a trip to India. The passport expires on 14 June 2027, and the trip returns on 24 January 2027. The destination requires six months of passport validity beyond the return date, so the passport must be valid through 24 July 2027. The agent builds the passport renewal, then the India e-Visa, then the final travel check. It also flags the renters insurance lapse and the 30 continuing education hours for the professional license as parallel risks.

It prices the cost of missing each deadline, generates a PDF packet with the checklist and form-ready data, and creates an `.ics` calendar file.

## Why it is different

- It plans a chain, not a list of dates.
- It ties every fee and processing time to an official source with a checked date.
- It prepares a packet, not just a reminder.
- It refuses to invent an answer when the evidence is weak.

## How we built it

- FastAPI and Jinja for the product.
- SQLModel and SQLite for persistent memory, facts, requirements, plans, tasks, and the activity log.
- Nebius Token Factory for inference.
- NVIDIA Nemotron Nano for document extraction and Nemotron Super for official-policy extraction.
- Tavily for official-source search with a domain allowlist and citation capture.
- pypdf for text PDF extraction.
- ReportLab for the packet PDF.
- A deterministic planner for the dependency graph, date math, and risk pricing. The model extracts and explains; it does not decide the legal deadline.

## Model use and cost

The fast model handles document extraction. The reasoning model handles official-policy extraction. If the reasoning call fails, the app retries once with the fast model, then falls back to a source-only result.

Page loads make no model calls. The plan, evidence, and activity log are read from SQLite. A run makes one extraction call per new document and one policy call per document type. Tavily results and requirements are cached in the database. The planner and the date math spend no tokens.

## Challenges

- Keeping the model from inventing fees or processing times. Weak evidence becomes "verify manually."
- Making the plan trustworthy. The dates and dependencies are code, not model output.
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
- More jurisdictions and document types.
- A source registry with change monitoring.
- Authentication and encrypted storage for a production deployment.

## Built with

Nebius Token Factory, NVIDIA Nemotron, Tavily, FastAPI, SQLModel, SQLite, pypdf, ReportLab, Jinja.

## Links

- Repository: https://github.com/pratham2402/unlapsed
- Demo: add the public URL after deployment
- Video: add the YouTube URL
- Feedback: see `FEEDBACK.md`
