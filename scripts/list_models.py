"""Print the model IDs available on your Token Factory account."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.nebius import NebiusClient, NebiusError


def main() -> None:
    settings = get_settings()
    if not settings.live_nebius:
        print("NEBIUS_API_KEY is not set. Add it to .env first.")
        return

    print(f"Base URL: {settings.nebius_base_url}")
    try:
        models = NebiusClient().list_models()
    except NebiusError as exc:
        print(f"Could not list models: {exc}")
        return

    for model in models:
        print(model)

    print()
    print(f"Current fast model:      {settings.nebius_model_fast}")
    print(f"Current reasoning model: {settings.nebius_model_reasoning}")
    print("If either is not in the list above, update .env.")


if __name__ == "__main__":
    main()
