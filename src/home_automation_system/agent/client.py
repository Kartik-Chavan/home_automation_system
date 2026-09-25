"""Small raw HTTP client for Cohere Chat API v2."""

from __future__ import annotations

import os
from typing import Any

import httpx


class CohereClient:
    """Call Cohere without the heavyweight provider SDK."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key: str = api_key or os.getenv("COHERE_API_KEY", "")
        self._model: str = model or os.getenv("COHERE_MODEL", "command-a-03-2025")
        self._base_url: str = os.getenv("COHERE_BASE_URL", "https://api.cohere.com/v2/chat")
        self._timeout: float = float(os.getenv("COHERE_TIMEOUT_SECONDS", "45"))
        if not self._api_key:
            raise RuntimeError("COHERE_API_KEY is required to enable the AI agent")

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Send one Cohere v2 chat request and return its JSON response."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
        }
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response: httpx.Response = await client.post(
                self._base_url, headers=headers, json=payload
            )
        if response.is_error:
            raise RuntimeError(
                f"Cohere API returned HTTP {response.status_code}: {response.text[:500]}"
            )
        body: object = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("Cohere API returned a non-object response")
        return body
