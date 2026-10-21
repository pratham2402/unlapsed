from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def init_db() -> None:
    # Importing the models registers their tables on SQLModel.metadata.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_sqlite(engine)


def _migrate_sqlite(db_engine) -> None:
    """Add columns introduced after the first SQLite demo database.

    SQLModel's create_all does not alter existing tables. The demo uses SQLite,
    so a small additive migration keeps the committed database usable. A
    production Postgres deployment should use a real migration tool.
    """
    if not str(db_engine.url).startswith("sqlite"):
        return

    additions = {
        "events": {
            "destination_country": "VARCHAR DEFAULT ''",
            "home_location": "VARCHAR DEFAULT ''",
            "is_international": "BOOLEAN DEFAULT 0",
            "event_type_confidence": "FLOAT DEFAULT 0",
            "event_type_reason": "VARCHAR DEFAULT ''",
            "relevant_docs_json": "VARCHAR DEFAULT '[]'",
        },
        "travel_rules": {
            "visa_required": "BOOLEAN",
            "visa_type": "VARCHAR DEFAULT ''",
            "visa_rule_text": "VARCHAR DEFAULT ''",
        },
    }
    with db_engine.begin() as connection:
        for table, columns in additions.items():
            rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {row[1] for row in rows}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
