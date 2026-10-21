"""Create the database and seed the demo only when it is empty.

This is the container start path. It keeps a deployed demo usable after a
restart without wiping changes on every boot.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Document
from app.services.demo import ensure_packet, seed_demo


def main() -> None:
    init_db()
    with Session(engine) as session:
        if session.exec(select(Document)).first() is None:
            plan = seed_demo(session, reset=True)
            print(f"Seeded demo. Plan: {plan.title if plan else 'none'}")
        else:
            print("Database already has documents. Skipping seed.")
        ensure_packet(session)
        print("Packet ready.")


if __name__ == "__main__":
    main()
