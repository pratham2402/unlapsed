#!/bin/sh
set -e

uv run python scripts/bootstrap.py

exec uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
