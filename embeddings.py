# embeddings.py
# 로컬 다국어 임베딩(fastembed) + pgvector 저장/의미검색 (서비스 계층).
# 모델: paraphrase-multilingual-MiniLM-L12-v2 (384차원) — 무료, API키 불필요, 한국어 지원.
from __future__ import annotations

import functools

from sqlalchemy import text

from db_config import get_engine

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384


@functools.lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=_MODEL_NAME)


def embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _model().embed(texts)]


def embed_one(t: str) -> list[float]:
    return embed([t])[0]


def _vec_literal(v: list[float]) -> str:
    """pgvector 텍스트 리터럴 '[a,b,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def store_embedding(source_type: str, source_id: str, chunk_text: str) -> None:
    """source(논문/인사이트/구조 등)의 텍스트를 임베딩해 embeddings 테이블에 저장."""
    if not chunk_text or not chunk_text.strip():
        return
    vec = embed_one(chunk_text[:8000])
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO embeddings (source_type, source_id, chunk_text, embedding)
            VALUES (:st, :sid, :ct, CAST(:emb AS vector))
        """), {"st": source_type, "sid": str(source_id),
               "ct": chunk_text[:2000], "emb": _vec_literal(vec)})


def delete_embeddings(source_type: str, source_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM embeddings WHERE source_type=:st AND source_id=:sid"),
            {"st": source_type, "sid": str(source_id)},
        )


def semantic_search(query: str, k: int = 5) -> list[dict]:
    """질의 임베딩과 코사인 유사도 top-k 반환 (source_type/source_id/chunk_text/score)."""
    qv = _vec_literal(embed_one(query))
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT source_type, source_id, chunk_text,
                   1 - (embedding <=> CAST(:q AS vector)) AS score
            FROM embeddings
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
        """), {"q": qv, "k": k}).mappings().all()
    return [dict(r) for r in rows]
