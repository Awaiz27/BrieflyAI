from llm_pipeline.OllamaClient import OllamaEmbeddingClient
from configs.constants import EMBEDDER_MODEL_NAME, MODEL_BASE_URL
from typing import List
import numpy as np

_embedder = OllamaEmbeddingClient(base_url=MODEL_BASE_URL, model=EMBEDDER_MODEL_NAME)

def embed_query(text: str) -> List[float]:
    vec = _embedder.embed([text])
    return vec[0]


def blend_vectors(a: List[float], b: List[float], alpha: float) -> List[float]:
    # alpha * a + (1-alpha) * b
    av = np.array(a, dtype=np.float32)
    bv = np.array(b, dtype=np.float32)
    v = alpha * av + (1.0 - alpha) * bv
    v = v / (np.linalg.norm(v) + 1e-12)
    return v.tolist()
