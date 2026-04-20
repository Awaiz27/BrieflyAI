"""Embedding and vector blending utilities."""

from __future__ import annotations

import numpy as np

from app.llm.clients import OllamaEmbeddingClient
from app.settings import get_settings

_embedder: OllamaEmbeddingClient | None = None


def _get_embedder() -> OllamaEmbeddingClient:
    global _embedder
    if _embedder is None:
        s = get_settings()
        _embedder = OllamaEmbeddingClient(
            base_url=s.model_base_url,
            model=s.embedder_model_name,
            max_concurrency=s.embedder_max_concurrency,
        )
    return _embedder


async def embed_query(text: str) -> list[float]:
    vec = await _get_embedder().embed([text])
    return vec[0]


def blend_vectors(a: list[float], b: list[float], alpha: float = 0.7) -> list[float]:
    av = np.array(a, dtype=np.float32)
    bv = np.array(b, dtype=np.float32)
    v = alpha * av + (1.0 - alpha) * bv
    v = v / (np.linalg.norm(v) + 1e-12)
    return v.tolist()
