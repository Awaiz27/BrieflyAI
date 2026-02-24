import asyncio
import aiohttp
from typing import Any, Dict, List, Optional

class OllamaEmbeddingClient:
    """
    Ollama embedding client.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3-embedding:0.6b-fp16",
        timeout: int = 60,
        max_concurrency: int = 2,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self._endpoint = f"{self.base_url}/api/embeddings"
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Embed texts with bounded async concurrency.
        Order is preserved.
        """
        if not texts:
            return []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            tasks = [
                self._embed_one(session, idx, text)
                for idx, text in enumerate(texts)
            ]

            results = await asyncio.gather(*tasks)

        # restore original order
        results.sort(key=lambda x: x[0])
        return [embedding for _, embedding in results]

    async def _embed_one(
        self,
        session: aiohttp.ClientSession,
        idx: int,
        text: str,
    ):
        """
        Single embedding with retry + exponential backoff.
        """
        if len(text) > 512:
            text = text[:511]

        async with self._semaphore:
            for attempt in range(1, self.max_retries + 1):
                try:
                    async with session.post(
                        self._endpoint,
                        json={
                            "model": self.model,
                            "prompt": text,
                        },
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        return idx, data["embedding"]

                except Exception as e:
                    if attempt >= self.max_retries:
                        raise RuntimeError(
                            f"Ollama embedding failed after {attempt} attempts"
                        ) from e

                    sleep = self.backoff_base * (2 ** (attempt - 1))
                    await asyncio.sleep(sleep)



class OllamaLLMClient:
    """
    Async Ollama LLM client using aiohttp.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:0.6b-q4_K_M",
        system_prompt: str = "You are a helpful assistant.",
        timeout: int = 60,
        max_concurrency: int = 4,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        default_options: Optional[Dict[str, Any]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self.default_options = default_options or {
            "temperature": 0.4,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
        }

        self._endpoint = f"{self.base_url}/api/chat"
        self._semaphore = asyncio.Semaphore(max_concurrency)

        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session:
            await self._session.close()

    async def generate(
        self,
        prompts: List[str],
        *,
        system_prompt: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        if not prompts:
            return []

        if self._session is None:
            raise RuntimeError("ClientSession not initialized")

        tasks = [
            self._generate_one(prompt, system_prompt, options)
            for prompt in prompts
        ]

        return await asyncio.gather(*tasks)

    async def _generate_one(
        self,
        user_prompt: str,
        system_prompt: Optional[str],
        options: Optional[Dict[str, Any]],
    ) -> str:
        async with self._semaphore:
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt or self.system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                "stream": False,
                "options": self._merge_options(options),
            }

            for attempt in range(1, self.max_retries + 1):
                try:
                    async with self._session.post(
                        self._endpoint,
                        json=payload,
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        return data["message"]["content"]

                except Exception as exc:
                    if attempt >= self.max_retries:
                        raise RuntimeError("Ollama chat failed") from exc

                    await asyncio.sleep(
                        self.backoff_base * (2 ** (attempt - 1))
                    )

    def _merge_options(
        self,
        overrides: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        merged = dict(self.default_options)
        if overrides:
            merged.update(overrides)
        return merged