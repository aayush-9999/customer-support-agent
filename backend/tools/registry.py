# backend/tools/registry.py

import logging
import numpy as np
from typing import Callable
from backend.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. Returns 0.0 on zero vectors."""
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


class ToolRegistry:
    """
    Stores all real tools and their pre-computed embeddings.
    The LLM never sees these tool schemas directly — it only sees
    the 2 meta-tools (tool_search, tool_invoke).

    At startup:
        - Receives all BaseTool instances
        - Pre-computes one embedding per tool (name + description)
        - Caches them in memory — zero cost at inference time

    At inference:
        - tool_search() scores query embedding against all tool embeddings
        - Returns top_n matches with their full schemas
        - tool_invoke() retrieves and executes the selected tool
    """

    def __init__(self, tools: list[BaseTool], embedding_fn: Callable[[str], np.ndarray]):
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}
        self._embedding_fn = embedding_fn

        # Pre-compute embeddings at startup — not at inference time
        self._embeddings: dict[str, np.ndarray] = {}

        logger.info(f"[REGISTRY] Computing embeddings for {len(tools)} tools...")
        for tool in tools:
            # Combine name + description for richer embedding signal
            text = f"{tool.name}: {tool.description}"
            self._embeddings[tool.name] = embedding_fn(text)
            logger.debug(f"[REGISTRY] Embedded: {tool.name}")

        logger.info(f"[REGISTRY] Ready — {len(tools)} tools indexed.")

    def search(self, query: str, top_n: int = 3) -> list[dict]:
        """
        Semantic search over the tool registry.
        Returns top_n tools sorted by cosine similarity to the query.
        Each result includes the full schema so the LLM knows the parameters.
        """
        query_embedding = self._embedding_fn(query)

        scored: list[tuple[float, str]] = []
        for name, emb in self._embeddings.items():
            score = _cosine_similarity(query_embedding, emb)
            scored.append((score, name))

        scored.sort(key=lambda x: -x[0])

        results = []
        for score, name in scored[:top_n]:
            tool = self._tools[name]
            results.append({
                "tool_id":          name,
                "description":      tool.description,
                "parameters":       tool.parameters,
                "similarity_score": round(score, 3),
            })

        logger.info(
            f"[REGISTRY] search='{query[:60]}' → "
            f"{[r['tool_id'] for r in results]} "
            f"(scores: {[r['similarity_score'] for r in results]})"
        )
        return results

    def get_tool(self, tool_id: str) -> BaseTool | None:
        """Retrieve a tool by its exact name for invocation."""
        return self._tools.get(tool_id)

    def all_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def tool_count(self) -> int:
        return len(self._tools)