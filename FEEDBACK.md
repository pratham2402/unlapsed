# Feedback for the Nebius x NVIDIA Global AI Hackathon

## Nebius Token Factory

- Setup was straightforward. The OpenAI-compatible endpoint worked with the existing Python SDK.
- The model list was the main friction. The first fast model ID I tried was not available, and I had to use `GET /v1/models` to find the exact Nemotron Nano ID.
- Nemotron Nano handled structured document extraction well. Nemotron Super handled the dense official-policy pages well.
- The reasoning models can consume the token budget before returning content. I had to use a sufficient `max_tokens` and retry with the fast model when a call came back empty.
- Latency was fine for a demo.

## NVIDIA Nemotron

- Nano is a good fit for extraction. It returned clean JSON and useful confidence values.
- Super was better on policy pages with several sections, but the empty-content edge case is worth documenting.
- I would like clearer per-model guidance on JSON mode and token budgets.

## Tavily

- Domain filtering worked well. The passport fee and processing time came from travel.state.gov, and the DMV and professional board pages also worked.
- Some official sites are JavaScript-heavy or split fees across subpages. The India e-Visa page did not state the fee in the returned content, so the app marked it for manual verification instead of guessing.
- More guidance on official-site coverage and extracting tables from PDFs would help.

## Other tools

- LangSmith, Toloka, and Tandem.ai were not used in this MVP.
