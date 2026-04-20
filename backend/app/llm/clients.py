"""Async Ollama clients for embeddings and LLM generation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class OllamaEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: int = 60,
        max_concurrency: int = 2,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ):
        self._endpoint = f"{base_url.rstrip('/')}/api/embeddings"
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff_base
        self._sem = asyncio.Semaphore(max_concurrency)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout)) as session:
            tasks = [self._embed_one(session, i, t) for i, t in enumerate(texts)]
            results = await asyncio.gather(*tasks)
        results.sort(key=lambda x: x[0])
        return [emb for _, emb in results]

    async def _embed_one(self, session: aiohttp.ClientSession, idx: int, text: str) -> tuple[int, list[float]]:
        # Truncate to avoid excessive token counts
        if len(text) > 512:
            text = text[:511]
        async with self._sem:
            for attempt in range(1, self._max_retries + 1):
                try:
                    async with session.post(
                        self._endpoint, json={"model": self._model, "prompt": text}
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        return idx, data["embedding"]
                except Exception:
                    if attempt >= self._max_retries:
                        raise
                    await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))
        raise RuntimeError("unreachable")


class OllamaLLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        system_prompt: str = "You are a helpful assistant.",
        timeout: int = 60,
        max_concurrency: int = 4,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        default_options: Optional[dict[str, Any]] = None,
    ):
        self._endpoint = f"{base_url.rstrip('/')}/api/chat"
        self._model = model
        self._system_prompt = system_prompt
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff_base
        self._options = default_options or {"temperature": 0.4, "top_p": 0.9, "repeat_penalty": 1.1}
        self._sem = asyncio.Semaphore(max_concurrency)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "OllamaLLMClient":
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout))
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def generate(self, prompts: list[str], *, system_prompt: Optional[str] = None) -> list[str]:
        if not prompts:
            return []
        if not self._session:
            raise RuntimeError("Use async with OllamaLLMClient()")
        tasks = [self._generate_one(p, system_prompt) for p in prompts]
        return await asyncio.gather(*tasks)

    async def _generate_one(self, user_prompt: str, system_prompt: Optional[str]) -> str:
        async with self._sem:
            payload = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt or self._system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": dict(self._options),
            }
            for attempt in range(1, self._max_retries + 1):
                try:
                    async with self._session.post(self._endpoint, json=payload) as resp:  # type: ignore[union-attr]
                        resp.raise_for_status()
                        data = await resp.json()
                        return data["message"]["content"]
                except Exception:
                    if attempt >= self._max_retries:
                        raise
                    await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))
        raise RuntimeError("unreachable")
