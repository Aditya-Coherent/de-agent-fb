"""One-time backfill: embed every cmi_reports row into a pgvector column.

Enables semantic (meaning-based) lookup of a DB report by market name,
instead of the exact/substring LIKE match `db_source._search_terms` does.
Embeddings are computed locally (sentence-transformers, CPU) — no external
API, no per-call cost. Safe to re-run: only rows with a NULL embedding (or
--force) are (re-)embedded.

Usage:
    python backfill_report_embeddings.py
    python backfill_report_embeddings.py --force   # re-embed everything
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation.config import GenConfig
from me_engine.generation.semantic_search import EMBED_DIM, embed_many

BATCH_SIZE = 200


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill cmi_reports.embedding via local sentence-transformers.")
    p.add_argument("--force", action="store_true", help="Re-embed rows that already have an embedding")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = GenConfig.load(ROOT)
    if not cfg.neon_url:
        print("ERROR: NEON_DATABASE_URL not set in .env")
        return 2

    import psycopg
    from pgvector.psycopg import register_vector

    with psycopg.connect(cfg.neon_url) as conn:
        with conn.cursor() as cur:
            print("Ensuring pgvector extension + embedding column exist...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f'ALTER TABLE cmi_reports ADD COLUMN IF NOT EXISTS embedding vector({EMBED_DIM})')  # type: ignore[arg-type]
        conn.commit()
        register_vector(conn)  # must run AFTER the extension exists

        where = "" if args.force else "WHERE embedding IS NULL"
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM cmi_reports {where}")
            count_row = cur.fetchone()
            total = count_row[0] if count_row else 0
        print(f"Rows to embed: {total}")
        if total == 0:
            print("Nothing to do.")
            return 0

        done = 0
        start = time.time()
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT "newsId", keyword, "newsSubject"
                    FROM cmi_reports
                    {where}
                    ORDER BY "newsId"
                    LIMIT %s
                    """,  # type: ignore[arg-type]
                    (BATCH_SIZE,),
                )
                rows = cur.fetchall() 
            if not rows:
                break

            texts = [(kw or subj or "").strip() for _id, kw, subj in rows]
            ids = [news_id for news_id, _kw, _subj in rows]
            # rows with no usable text at all still get an embedding of "" so
            # they're marked done and don't loop forever; they'll just never
            # be a good semantic match (empty-string embedding is far from
            # everything).
            vectors = embed_many(texts, show_progress=False)

            # One bulk UPDATE per batch
            #  (single round trip) instead of one
            # UPDATE per row — the per-row form measured ~0.25s/row round-trip
            # to Neon (~50s/batch just in network waits); this cuts that to
            # ~1 round trip per batch.
            values_sql = ", ".join(["(%s, %s)"] * len(rows))
            params: list = []
            for news_id, vec in zip(ids, vectors):
                params.append(news_id)
                params.append(vec)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE cmi_reports AS t
                    SET embedding = v.embedding
                    FROM (VALUES {values_sql}) AS v(id, embedding)
                    WHERE t."newsId" = v.id
                    """,  # type: ignore[arg-type]
                    params,
                )
            conn.commit()

            done += len(rows)
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            print(f"  {done}/{total} embedded  ({rate:.1f} rows/sec)")

            if args.force:
                # --force re-embeds everything unconditionally; paginate by id
                # to avoid re-processing the same page forever.
                where = f'WHERE "newsId" > {ids[-1]}'
            # else: "WHERE embedding IS NULL" shrinks as we go — re-querying
            # naturally advances past just-embedded rows.

    print(f"Done — embedded {done} rows in {time.time() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
