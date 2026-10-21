from __future__ import annotations

import json
import re
from typing import Any

from openai import APIError, BadRequestError, OpenAI

from app.config import get_settings


class NebiusError(RuntimeError):
    pass


class NebiusClient:
    """Small wrapper around the OpenAI-compatible Token Factory API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        fast_model: str | None = None,
        reasoning_model: str | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.nebius_api_key).strip()
        self.base_url = (base_url or settings.nebius_base_url).strip()
        self.fast_model = fast_model or settings.nebius_model_fast
        self.reasoning_model = reasoning_model or settings.nebius_model_reasoning
        self._client: OpenAI | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> OpenAI:
        if not self.available:
            raise NebiusError("NEBIUS_API_KEY is not set")
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def list_models(self) -> list[str]:
        client = self._get_client()
        try:
            response = client.models.list()
        except APIError as exc:
            raise NebiusError(f"Could not list models: {exc}") from exc
        return sorted(model.id for model in response.data)

    def chat_text(
        self,
        user: str,
        system: str = "",
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        client = self._get_client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        try:
            response = client.chat.completions.create(
                model=model or self.reasoning_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except APIError as exc:
            raise NebiusError(f"Token Factory request failed: {exc}") from exc

        content = response.choices[0].message.content or ""
        return content.strip()

    def chat_json(
        self,
        user: str,
        system: str = "",
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2500,
    ) -> dict[str, Any]:
        client = self._get_client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        kwargs: dict[str, Any] = {
            "model": model or self.reasoning_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            try:
                response = client.chat.completions.create(
                    **kwargs,
                    response_format={"type": "json_object"},
                )
            except BadRequestError:
                # Some models do not expose JSON mode. The prompt still asks for JSON.
                response = client.chat.completions.create(**kwargs)
        except APIError as exc:
            raise NebiusError(f"Token Factory request failed: {exc}") from exc

        content = response.choices[0].message.content or ""
        return _parse_json(content)


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise NebiusError("Model returned an empty response")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise NebiusError("Model did not return valid JSON")
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise NebiusError("Model returned JSON that is not an object")
    return parsed
