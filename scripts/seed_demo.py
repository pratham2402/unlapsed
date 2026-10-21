"""Reset the database and load the Unlapsed demo scenario."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from app.config import get_settings
from app.db import init_db, engine
from app.services.demo import seed_demo


def main() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    init_db()

    with Session(engine) as session:
        plan = seed_demo(session)
        print("Demo seeded.")
        print(f"User: {settings.demo_user_email}")
        print(f"Plan: {plan.title if plan else 'none'}")
        print("Open the app and visit /")


if __name__ == "__main__":
    main()
