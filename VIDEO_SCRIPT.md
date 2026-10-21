# Three-minute demo script

## What the hackathon requires

- A public YouTube video, three minutes or less.
- Footage of the project actually working, not slides.
- An explanation of how the project uses Nebius Token Factory and an NVIDIA open source model.
- A public demo URL is also required, but the video can use the live site or localhost.
- No copyrighted music or third-party trademarks unless you have permission.

Voiceover is the safest option. The rules ask for a public video that explains the project. A silent screen recording with captions is weaker. Record your voice, even on a phone headset.

## Before recording

- Run the app with your live keys.
- Open the browser at the dashboard. Use 100% zoom.
- Hide bookmarks and personal tabs.
- Close any window that shows `.env` or API keys.
- Turn on the sample case. Do not show real personal documents.
- Record at 1080p if your machine can handle it.
- Keep the total under three minutes.

## Shot list and voiceover

### 0:00 to 0:12 — Hook

**On screen:** Dashboard, full page.

**Voiceover:**
"A passport can look valid and still ruin a trip. The problem is not one expiry date. It is the chain between the documents."

### 0:12 to 0:30 — What Unlapsed does

**On screen:** Scroll from the expiry watch to the event plan and its blocking tasks.

**Voiceover:**
"Unlapsed is a private renewal agent. It reads your documents, checks the current official rule, builds a plan backward from a date that matters, and prepares the packet. This is a synthetic sample case, not real personal data."

### 0:30 to 0:55 — The dashboard

**On screen:** Point at the expiry watch, the nearest expiry, then the event plan and its blocking tasks. Open the plan.

**Voiceover:**
"The documents are the product. The expiry watch comes first. The trip is one dated event. The agent reads the event as international, checks the destination's passport and visa rules on the official source, and finds the conflict. Then it orders the work: renew the passport, apply for the e-Visa, and do the final check."

### 0:55 to 1:20 — Document provenance

**On screen:** Open Documents, then the passport detail. Scroll to the extracted record and the provenance table.

**Voiceover:**
"Every fact comes from the document. Here is the expiry date, the source line, the page, and the confidence. This is not a chat answer. It is a record you can inspect."

### 1:20 to 1:50 — The plan and the evidence

**On screen:** Return to the Plan. Scroll through the critical path and the evidence panel.

**Voiceover:**
"The plan is a dependency graph, not a list. The e-Visa waits for the new passport. The final check waits for both. Underneath, every requirement has an official source and a checked date. The passport validity rule, fees, and processing times came from the State Department. The e-Visa page did not state a fee, so the app says verify manually. It does not guess."

### 1:50 to 2:10 — The packet

**On screen:** Click Download packet. Show the PDF pages: plan, evidence, form-ready data, checklist.

**Voiceover:**
"The output is a packet. Plan, evidence, form-ready data, a cover letter, and a checklist. The user can also add the tasks to a calendar."

### 2:10 to 2:35 — The stack

**On screen:** Open the Activity page. Show a run and the verification entries. Then point back to the rail's Live sources label.

**Voiceover:**
"Under the hood, Nebius Token Factory runs NVIDIA Nemotron. Nano handles document extraction. Super handles official-policy extraction. Tavily finds the official source. The planner and the date math are deterministic, so the model is not deciding the legal deadline. Page loads make no model calls, and the requirements are cached. A run only spends tokens on new documents and new policy checks."

### 2:35 to 2:50 — Privacy and close

**On screen:** Open Privacy. Show the stored fields and the delete controls. End on the dashboard.

**Voiceover:**
"The PDF is read in memory and not stored. Only structured fields and source excerpts are kept, and the user can delete or export them. Unlapsed is a planning aid, not legal advice. The goal is to catch the chain before it costs you a trip."

## Recording notes

- Speak a little slower than you think you need to. The script is about 330 words, which fits in two and a half minutes at a calm pace.
- If you go over three minutes, cut the document provenance section to ten seconds. Do not cut the Nebius and NVIDIA explanation.
- Use the live site with your keys. The green "Live sources" label is good evidence that the calls are real.
- Keep the mouse movements slow and deliberate. Do not scroll past the evidence panel.
- Add a title card only if you have time. The video should spend its seconds on the working app.

## YouTube upload

- Title: `Unlapsed — a private renewal agent for document chains`
- Visibility: Public.
- Description: include the live demo URL and the GitHub repository URL.
- Add a short note that the sample documents are synthetic.
- Do not use copyrighted music.
