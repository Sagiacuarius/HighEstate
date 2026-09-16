"""Deterministic keyword-overlap KB retrieval (change: agent-autonomy, WU15).

Tenant-scoped read over `knowledge_base_articles` (migration 008): the
account predicate comes first (server-derived via `_require`), scoring is a
single deterministic array-overlap statement (overlap count, tie-break
`(hits, category, id)`, LIMIT 1). Zero rows means no match (None) — the
caller escalates, never fabricates. No embeddings, no vectors, no tsvector.

Token normalizer: lowercase, accent-strip, non-letter split, fixed stopword
set. Shared with the retrieval test's pure-Python twin oracle.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from api.adapters.postgres_commercial_crm import _require
from api.adapters.postgres_core import PostgresRunner

# Fixed stopword set (Spanish + English question scaffolding). Content words
# from the seeded corpus (expensas, garantia, visitas, ...) are never members.
STOPWORDS = frozenset({
    "de", "la", "el", "en", "y", "a", "los", "las", "del", "se", "que",
    "por", "con", "una", "uno", "un", "para", "es", "son", "su", "sus",
    "al", "lo", "le", "les", "me", "te", "nos", "mi", "mis", "tu", "tus",
    "como", "mas", "pero", "si", "no", "ni", "esta", "este", "estos",
    "estas", "ese", "esa", "eso", "esto", "hay", "tiene", "ser", "sea",
    "cual", "cuales", "que", "cuanto", "cuanta", "cuantos", "donde",
    "cuando", "porque", "the", "an", "of", "to", "is", "are", "what",
    "does", "do", "how", "which",
})

_NON_LETTERS_RE = re.compile(r"[^a-z]+")


def normalize_tokens(text: str) -> list[str]:
    """Lowercase, accent-strip, split on non-letters, drop stopwords."""
    if not isinstance(text, str):
        return []
    folded = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return [tok for tok in _NON_LETTERS_RE.split(stripped)
            if tok and tok not in STOPWORDS]


# Deterministic scoring SQL (design shape): account predicate first,
# parameters only, hits > 0, ORDER BY hits DESC, category, id, LIMIT 1.
# Note: the design draft wrote `cardinality((SELECT ... INTERSECT ...))`,
# which is not valid PostgreSQL (`cardinality` takes an array, not a row
# set); the ARRAY(...) wrapper below is the exact equivalent — INTERSECT
# still dedupes, cardinality still counts the overlap.
KB_SCORING_SQL = (
    "WITH scored AS ("
    " SELECT a.id, a.category, a.question, a.answer,"
    " cardinality(ARRAY((SELECT unnest(a.keywords) INTERSECT SELECT unnest(%s::text[])))) AS hits"
    " FROM knowledge_base_articles a"
    " WHERE a.client_account_id = %s"
    ") SELECT id, category, question, answer FROM scored"
    " WHERE hits > 0"
    " ORDER BY hits DESC, category, id"
    " LIMIT 1"
)


class KnowledgeBaseRepository:
    """Single read: best-matching article for (context, query_text)."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def find_best_match(self, context, query_text: str) -> dict[str, Any] | None:
        account_id = _require(context)
        tokens = normalize_tokens(query_text)
        if not tokens:
            return None
        rows = await self._runner.query(KB_SCORING_SQL, (tokens, account_id))
        if not rows:
            return None
        row = rows[0]
        return {
            "id": str(row["id"]),
            "category": row["category"],
            "question": row["question"],
            "answer": row["answer"],
        }
