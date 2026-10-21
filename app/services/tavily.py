from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from tavily import TavilyClient

from app.config import get_settings


class TavilyError(RuntimeError):
    pass


@dataclass
class SearchHit:
    title: str
    url: str
    content: str
    score: float = 0.0


class TavilySearch:
    """Official-source search with a hard allowlist at the call site."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.tavily_api_key).strip()
        self._client: TavilyClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> TavilyClient:
        if not self.available:
            raise TavilyError("TAVILY_API_KEY is not set")
        if self._client is None:
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    def search(
        self,
        query: str,
        include_domains: Sequence[str] | None = None,
        max_results: int = 5,
    ) -> list[SearchHit]:
        client = self._get_client()
        kwargs = {
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
        }
        if include_domains:
            kwargs["include_domains"] = list(include_domains)

        try:
            response = client.search(**kwargs)
        except Exception as exc:
            raise TavilyError(f"Tavily search failed: {exc}") from exc

        hits: list[SearchHit] = []
        for item in response.get("results", []) or []:
            url = item.get("url", "")
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=item.get("title", "") or url,
                    url=url,
                    content=item.get("content", "") or "",
                    score=float(item.get("score", 0.0) or 0.0),
                )
            )
        return hits
