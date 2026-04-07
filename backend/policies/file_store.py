# backend/policies/file_store.py

import json
import logging
from pathlib import Path

from backend.core.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# Rough token estimation: 1 token ≈ 4 characters (good enough for logging)
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FilePolicyStore:
    """
    Keyword-filtered policy file loader.

    On init:
        - Reads manifest.json
        - Loads always_load files (cached in memory — in every prompt)

    On build_context(user_message):
        - Scores on_topic files against user message keywords
        - Returns top 3 matches + always_load as one combined string
        - Logs estimated token counts so you can track context size
    """

    def __init__(self):
        self._base     = Path(settings.knowledge_base_dir)
        self._manifest = self._load_manifest()
        self._always   = self._load_always_files()

        always_tokens = sum(_estimate_tokens(c) for c in self._always)
        logger.info(
            f"FilePolicyStore ready — "
            f"{len(self._always)} always-load files (~{always_tokens} tokens), "
            f"{len(self._manifest.get('on_topic', []))} on-topic files"
        )

    # ── Public ─────────────────────────────────────────────────────────────────

    def build_context(self, user_message: str) -> str:
        """
        Build the full knowledge context string for this user message.
        Logs estimated token count for every section so you can see exactly
        what's being injected into the prompt.
        """
        on_topic_entries, on_topic_contents = self._score_and_select(user_message)
        sections = self._always + on_topic_contents

        # ── Token logging ────────────────────────────────────────────────────
        always_tokens   = sum(_estimate_tokens(c) for c in self._always)
        on_topic_tokens = sum(_estimate_tokens(c) for c in on_topic_contents)
        total_tokens    = always_tokens + on_topic_tokens

        logger.info(
            f"[CONTEXT] Policy injection — "
            f"always: ~{always_tokens} tokens | "
            f"on-topic ({[e['file'] for e in on_topic_entries]}): ~{on_topic_tokens} tokens | "
            f"total knowledge context: ~{total_tokens} tokens"
        )

        context = "\n\n---\n\n".join(sections)
        return context

    # ── Private ────────────────────────────────────────────────────────────────

    def _load_manifest(self) -> dict:
        manifest_path = Path(settings.knowledge_manifest_path)
        if not manifest_path.exists():
            logger.warning(f"Manifest not found at {manifest_path} — using empty config")
            return {"always_load": [], "on_topic": []}
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_always_files(self) -> list[str]:
        """Load all always_load files at startup — cached in memory."""
        contents = []
        for entry in self._manifest.get("always_load", []):
            content = self._read_file(entry["file"])
            if content:
                token_est = _estimate_tokens(content)
                logger.debug(f"Always-load: {entry['file']} (~{token_est} tokens)")
                contents.append(content)
        return contents

    def _score_and_select(
        self,
        user_message: str,
        top_n: int = 3,
    ) -> tuple[list[dict], list[str]]:
        """
        Score each on_topic file against the user message keywords.
        Returns (matched_entries, matched_contents).
        Falls back to fallback files if nothing matches.
        """
        message_lower = user_message.lower()
        scored        = []

        for entry in self._manifest.get("on_topic", []):
            keywords = [kw.lower() for kw in entry.get("keywords", [])]
            score    = sum(1 for kw in keywords if kw in message_lower)
            if score > 0:
                scored.append((score, entry.get("priority", 99), entry))

        scored.sort(key=lambda x: (-x[0], x[1]))
        top_entries = [entry for _, _, entry in scored[:top_n]]

        if not top_entries:
            top_entries = self._get_fallback_entries()

        contents = [c for c in (self._read_file(e["file"]) for e in top_entries) if c]
        return top_entries, contents

    def _get_fallback_entries(self) -> list[dict]:
        fallback_files = self._manifest.get("fallback_if_no_topic_match", [])
        on_topic_map   = {
            e["file"]: e
            for e in self._manifest.get("on_topic", [])
        }
        return [on_topic_map[f] for f in fallback_files if f in on_topic_map]

    def _read_file(self, relative_path: str) -> str | None:
        full_path = self._base / relative_path
        if not full_path.exists():
            logger.warning(f"Knowledge file not found: {full_path}")
            return None
        try:
            content = full_path.read_text(encoding="utf-8").strip()
            if not content:
                logger.warning(f"Knowledge file is empty: {full_path}")
                return None
            return content
        except Exception as e:
            logger.error(f"Failed to read {full_path}: {e}")
            return None