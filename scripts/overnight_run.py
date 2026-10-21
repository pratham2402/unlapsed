"""Run the renewal agent without a browser.

Use this from cron or a scheduler to make the assistant feel always-on.
It re-checks official requirements, rebuilds the plan, and writes a packet.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from app.config import get_settings
from app.db import engine, init_db
from app.services.agent import run_agent


def main() -> None:
    settings = get_settings()
    init_db()
    with Session(engine) as session:
        run = run_agent(session, settings.demo_user_email, run_type="scheduled")
        print(f"Run #{run.id}: {run.status}")
        print(run.summary)
        if run.changes:
            print("Changes:")
            for change in run.changes:
                print(f"- {change}")


if __name__ == "__main__":
    main()
