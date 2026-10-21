"""Check which live services are configured and make one small call to each."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.nebius import NebiusClient, NebiusError
from app.services.tavily import TavilyError, TavilySearch


def main() -> None:
    settings = get_settings()

    print("Unlapsed key check")
    print("=" * 40)
    print(f"Nebius Token Factory: {'set' if settings.live_nebius else 'missing'}")
    print(f"Tavily:               {'set' if settings.live_tavily else 'missing'}")
    print(f"LangSmith:            {'set' if settings.langchain_api_key else 'missing'}")
    print(f"Toloka:               {'set' if settings.toloka_api_key else 'missing'}")
    print(f"Tandem.ai:            {'set' if settings.tandem_api_key else 'missing'}")
    print()

    if settings.live_nebius:
        try:
            models = NebiusClient().list_models()
            print(f"Nebius call OK. {len(models)} models available.")
            for model in models[:12]:
                print(f"  {model}")
            if len(models) > 12:
                print(f"  ... and {len(models) - 12} more. Run scripts/list_models.py.")
        except NebiusError as exc:
            print(f"Nebius call FAILED: {exc}")
    else:
        print("Nebius: using the deterministic fallback extractor.")

    print()

    if settings.live_tavily:
        try:
            hits = TavilySearch().search(
                "U.S. passport renewal fee processing time",
                include_domains=["travel.state.gov"],
                max_results=3,
            )
            print(f"Tavily call OK. {len(hits)} official hits.")
            for hit in hits:
                print(f"  {hit.title} -> {hit.url}")
        except TavilyError as exc:
            print(f"Tavily call FAILED: {exc}")
    else:
        print("Tavily: using labelled sample requirements.")


if __name__ == "__main__":
    main()
