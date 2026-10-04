"""Thin Ollama HTTP client for script parsing.

Kept deliberately small: chat + generate, JSON-forced output, and an
``unload`` helper so the LLM releases VRAM before ComfyUI/SDXL takes the GPU
(the 16 GB card cannot hold both).
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:14b-instruct",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def is_up(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/api/tags", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            return False

    def models(self) -> list[str]:
        r = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
        timeout: float = 300.0,
    ) -> str:
        """Send a chat request; returns the assistant message text.

        When ``json_schema`` is given, Ollama constrains decoding to valid JSON
        matching the schema (structured outputs).
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_schema is not None:
            payload["format"] = json_schema
            # Thinking models (qwen3.5) put their answer in "thinking" and leave the
            # constrained content empty; the schema already structures the answer.
            payload["think"] = False
        r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
        if r.status_code != 200:
            raise LLMError(f"ollama chat failed ({r.status_code}): {r.text}")
        return r.json().get("message", {}).get("content", "")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
        timeout: float = 300.0,
    ) -> Any:
        text = self.chat(messages, json_schema=schema, temperature=temperature, timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model did not return valid JSON: {text[:500]}") from exc

    def unload(self) -> None:
        """Ask Ollama to evict the model from VRAM (keep_alive=0)."""
        try:
            httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=30.0,
            )
        except httpx.HTTPError:
            pass
