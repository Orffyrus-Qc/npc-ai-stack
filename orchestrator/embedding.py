"""
Shared fastembed model instance - loading the ONNX model is real, measured
startup cost (a fresh `TextEmbedding(...)` re-triggers it), so anything that
needs to embed text (MemoryStore's episodic recall, WikiKnowledgeStore's wiki
chunks) should share one instance via get_embedder() rather than constructing
its own.
"""

from __future__ import annotations

from fastembed import TextEmbedding

EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # 384-dim, fast on CPU
EMBED_DIM = 384

_embedder: TextEmbedding | None = None


def get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(EMBED_MODEL)
    return _embedder


def embed(text: str) -> list[float]:
    return list(next(iter(get_embedder().embed([text]))))
