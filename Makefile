.PHONY: install seed run check test reset

install:
	uv sync

seed:
	uv run python scripts/seed_demo.py

run:
	uv run uvicorn app.main:app --reload

check:
	uv run python scripts/check_keys.py

test:
	uv run pytest -q

reset:
	uv run python scripts/seed_demo.py
