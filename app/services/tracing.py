import os

from app.config import get_settings


def configure_tracing() -> None:
    """Enable LangSmith tracing when a key is present.

    The app works without LangSmith. If a key is supplied, every traced
    agent run and model call is attached to the configured project.
    """
    settings = get_settings()
    if not settings.langchain_api_key.strip():
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.langchain_tracing_v2 else "false"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key.strip()
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project


def traceable(name: str):
    """Apply LangSmith tracing without making it a hard dependency."""

    def decorator(fn):
        try:
            from langsmith import traceable as ls_traceable

            return ls_traceable(run_type="chain", name=name)(fn)
        except Exception:
            return fn

    return decorator
