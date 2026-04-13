# backend/services/embedding_service.py
#
# Provides the embedding function used by ToolRegistry.
#
# We use sentence-transformers with the "all-MiniLM-L6-v2" model because:
#   - It's free — no API calls, no API key
#   - ~80MB download (cached after first run)
#   - Fast CPU inference (~5ms per text)
#   - Good enough for tool-description similarity (not long-form retrieval)
#   - No dependency on Groq or OpenAI for the registry
#
# Embeddings are computed ONCE at startup for all tool descriptions.
# At inference time, only the user query is embedded (one small call).
#
# Add to requirements.txt:
#   sentence-transformers>=2.6.0

import logging
import numpy as np
from functools import lru_cache
from typing import Callable

logger = logging.getLogger(__name__)

# The model name. MiniLM-L6-v2 is the sweet spot: small, fast, accurate enough.
# For higher accuracy at the cost of ~4× more RAM, use "all-mpnet-base-v2".
_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _load_model():
    """
    Load and cache the SentenceTransformer model.
    lru_cache ensures it's only loaded once per process.
    """
    from sentence_transformers import SentenceTransformer
    logger.info(f"[EMBEDDING] Loading model '{_MODEL_NAME}'...")
    model = SentenceTransformer(_MODEL_NAME)
    logger.info(f"[EMBEDDING] Model ready.")
    return model


def get_embedding_fn() -> Callable[[str], np.ndarray]:
    """
    Returns a callable that converts a string to a numpy embedding vector.

    Usage:
        embed = get_embedding_fn()
        vector = embed("get order details by order_id")
        # → np.ndarray of shape (384,)

    The callable is safe to call from async code — sentence-transformers
    is CPU-bound and synchronous, which is fine because it's only called:
      1. At startup (N tool descriptions, once)
      2. Once per user message (the query embedding)
    """
    model = _load_model()

    def embed(text: str) -> np.ndarray:
        # encode() returns a numpy array by default
        return model.encode([text], convert_to_numpy=True)[0]

    return embed