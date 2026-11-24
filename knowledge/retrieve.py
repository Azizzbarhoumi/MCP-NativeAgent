"""Retrieval: question -> top-k chunks.

Modes:
  baseline  dense only: finds chunks with similar MEANING.
  hybrid    dense + BM25 keywords, merged with Reciprocal Rank Fusion (RRF).
            Keywords catch exact tokens that meaning-search blurs: flag names like
            --maxfail, rule codes like F841, exit code numbers, setting names.
"""

from functools import lru_cache

from pydantic import BaseModel

from knowledge.config import load_config
from knowledge.ingest.embed import DenseEmbedder, SparseEmbedder
from knowledge.ingest.store import DENSE, SPARSE, get_client

RRF_K = 60        # standard constant from the RRF paper; damps the gap between rank 1 and 2
CANDIDATES = 20   # each search returns this many before merging; the merge keeps k


class Hit(BaseModel):
    chunk_id: str
    score: float
    text: str
    source: str
    path: str
    section: str
    url: str | None


@lru_cache
def _dense() -> DenseEmbedder:
    cfg = load_config()
    return DenseEmbedder(cfg.embedding.dense_model, cfg.embedding.batch_size)


@lru_cache
def _sparse() -> SparseEmbedder:
    cfg = load_config()
    return SparseEmbedder(cfg.embedding.sparse_model, cfg.embedding.batch_size)


def _query(collection: str, vector, using: str, k: int) -> list[Hit]:
    points = get_client().query_points(collection, query=vector, using=using, limit=k, with_payload=True).points
    return [
        Hit(chunk_id=str(p.id), score=p.score, text=p.payload["text"], source=p.payload["source"],
            path=p.payload["path"], section=p.payload.get("section", ""), url=p.payload.get("url"))
        for p in points
    ]


def dense_search(question: str, k: int, collection: str) -> list[Hit]:
    """Cosine similarity between the question vector and every chunk vector; best k first."""
    return _query(collection, _dense().embed_query(question), DENSE, k)


def sparse_search(question: str, k: int, collection: str) -> list[Hit]:
    """BM25: chunks sharing the question's words score higher, rare words count more."""
    return _query(collection, _sparse().embed_query(question), SPARSE, k)


def rrf_merge(ranked_lists: list[list[Hit]], k: int, rrf_k: int = RRF_K) -> list[Hit]:
    """Reciprocal Rank Fusion: each list gives a chunk 1 / (rrf_k + rank). Sum over lists,
    sort, keep k. Uses ranks only, so dense cosine scores (0..1) and BM25 scores (0..30+)
    never need to be put on the same scale. A chunk found by both lists rises to the top."""
    scores: dict[str, float] = {}
    first_seen: dict[str, Hit] = {}
    for hits in ranked_lists:
        for rank, h in enumerate(hits, 1):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            first_seen.setdefault(h.chunk_id, h)
    best = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:k]
    return [first_seen[cid].model_copy(update={"score": scores[cid]}) for cid in best]


def search(question: str, k: int | None = None, collection: str | None = None,
           mode: str = "baseline") -> list[Hit]:
    cfg = load_config()
    k, collection = k or cfg.retrieval.k, collection or cfg.collection
    if mode == "baseline":
        return dense_search(question, k, collection)
    if mode == "hybrid":
        return rrf_merge([dense_search(question, CANDIDATES, collection),
                          sparse_search(question, CANDIDATES, collection)], k)
    raise ValueError(f"unknown retrieval mode {mode!r}")


def round_robin(ranked_lists: list[list[Hit]], limit: int) -> list[Hit]:
    """Merge several result lists by taking the 1st of each, then the 2nd of each, and so on,
    skipping duplicates. Unlike RRF, this guarantees every sub-question gets passages, which is
    the point of decomposition: one topic must not crowd out the other."""
    out, seen = [], set()
    for rank in range(max((len(x) for x in ranked_lists), default=0)):
        for hits in ranked_lists:
            if rank < len(hits) and hits[rank].chunk_id not in seen:
                seen.add(hits[rank].chunk_id)
                out.append(hits[rank])
                if len(out) == limit:
                    return out
    return out


def multi_search(queries: list[str], k: int | None = None, mode: str = "baseline", extra: int = 3,
                 collection: str | None = None) -> list[Hit]:
    """Additive decomposition merge: the ORIGINAL question's top k stay first and untouched;
    the sub-questions can only ADD up to `extra` new passages (round-robin among them).
    v1 merged everything round-robin, and a wrong split (ex11) pushed good passages out."""
    original = search(queries[0], k=k, collection=collection, mode=mode)
    seen = {h.chunk_id for h in original}
    subs = [[h for h in search(q, k=k, collection=collection, mode=mode) if h.chunk_id not in seen] for q in queries[1:]]
    return original + round_robin(subs, extra)
