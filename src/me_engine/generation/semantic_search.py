"""Local-embedding semantic search over the Neon `cmi_reports` table.

Keyword LIKE matching (see `_search_terms` in db_source.py) only finds a report
when the requested market name shares a literal substring with the DB's
`keyword`/`newsSubject` column. It misses genuinely-equivalent rewordings
("EV Charging Market" vs "Electric Vehicle Charging Infrastructure Market").

This module embeds report keywords once (via `backfill_embeddings.py`) into a
pgvector column, then finds the nearest report by meaning at query time — no
external API, the model runs locally (sentence-transformers, CPU).
"""
from __future__ import annotations

from functools import lru_cache

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL_NAME)


def embed(text: str) -> list[float]:
    """Embed a single piece of text into a 384-dim vector (L2-normalised)."""
    vec = _model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_many(texts: list[str], batch_size: int = 64, show_progress: bool = False) -> list[list[float]]:
    """Embed many texts in one batched pass (used by the backfill script)."""
    vecs = _model().encode(
        texts, batch_size=batch_size, normalize_embeddings=True,
        show_progress_bar=show_progress,
    )
    return vecs.tolist()


def to_vector_literal(vec: list[float]) -> str:
    """Render a vector as pgvector's text literal, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def nearest_report(cur, market_name: str, min_segmentation_len: int = 400):
    """Given an already-open psycopg cursor, return (newsId, keyword, similarity)
    for the nearest report by embedding similarity, or None.

    Used as the fallback when `_search_terms` LIKE matching finds nothing — a
    differently-worded but equivalent market name ("EV Charging Market" vs
    "Electric Vehicle Charging Infrastructure Market") still finds its report.
    Reuses the caller's connection/cursor (no extra round trip); degrades to
    None on any failure (embedding column missing because the backfill script
    hasn't run yet, model load failure, etc.) — this is best-effort evidence,
    never a hard requirement.
    """
    try:
        query_vec = embed(market_name)
        lit = to_vector_literal(query_vec)
        cur.execute(
            """
            SELECT "newsId", keyword, 1 - (embedding <=> %s::vector) AS similarity
            FROM cmi_reports
            WHERE embedding IS NOT NULL
              AND length(coalesce(segmentation, '')) > %s
            ORDER BY embedding <=> %s::vector
            LIMIT 1
            """,
            (lit, min_segmentation_len, lit),
        )
        return cur.fetchone()
    except Exception:  # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def ensure_embedded(cur, news_id: int, text: str) -> None:
    """Best-effort: embed this report NOW if it doesn't have one yet.

    Called whenever a report is actually touched by a real search (LIKE hit),
    so DB coverage grows organically through real usage instead of needing a
    big upfront batch backfill (backfill_report_embeddings.py still exists for
    that, but isn't required — this is the incremental alternative). A single
    local embedding + single-row UPDATE is cheap; any failure (model not
    loadable, column missing because pgvector was never enabled, etc.) is
    swallowed — this must never break the caller's actual evidence lookup.
    """
    try:
        cur.execute('SELECT embedding IS NULL FROM cmi_reports WHERE "newsId" = %s', (news_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return  # already embedded, or report not found
        lit = to_vector_literal(embed(text))
        cur.execute('UPDATE cmi_reports SET embedding = %s::vector WHERE "newsId" = %s', (lit, news_id))
    except Exception:  # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:  # noqa: BLE001
            pass


def semantic_search(neon_url: str, market_name: str, limit: int = 5, min_segmentation_len: int = 400):
    """Return up to `limit` (newsId, keyword, similarity) candidates, nearest first.

    Similarity is cosine similarity in [-1, 1] (pgvector's `<=>` is cosine
    *distance*; we convert to similarity = 1 - distance for readability).
    Only reports with a non-trivial segmentation tree are considered, since a
    report with no segmentation is useless as evidence regardless of how well
    its name matches.
    """
    import psycopg
    from pgvector.psycopg import register_vector

    query_vec = embed(market_name)
    with psycopg.connect(neon_url) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT "newsId", keyword, 1 - (embedding <=> %s) AS similarity
                FROM cmi_reports
                WHERE embedding IS NOT NULL
                  AND length(coalesce(segmentation, '')) > %s
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (query_vec, min_segmentation_len, query_vec, limit),
            )
            return cur.fetchall()
