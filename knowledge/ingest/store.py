"""Qdrant storage. Idempotent: each document's old chunks are deleted, then new ones upserted
under stable ids, so re-running ingestion never duplicates and never leaves stale chunks.

Each point has two vectors: "dense" (meaning, from bge-small) and "bm25" (keywords, sparse)."""

from qdrant_client import QdrantClient, models

from common.config import settings
from knowledge.ingest.chunk import Chunk

DENSE = "dense"
SPARSE = "bm25"


def get_client() -> QdrantClient:
    url = settings.qdrant_url
    # http(s) URL -> server (docker-compose); anything else -> local folder (tests, offline).
    return QdrantClient(url=url, timeout=60) if url.startswith("http") else QdrantClient(path=url)


def has_sparse(client: QdrantClient, name: str) -> bool:
    return SPARSE in (client.get_collection(name).config.params.sparse_vectors or {})


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    # A collection made before hybrid search has no "bm25" vector, and Qdrant cannot add a
    # vector type to an existing collection, so we rebuild it (ingestion refills it).
    if client.collection_exists(name) and not has_sparse(client, name):
        print(f"  collection '{name}' has no '{SPARSE}' vector: recreating it for hybrid search")
        client.delete_collection(name)
    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            # IDF modifier: Qdrant computes the "rare words count more" part of BM25 itself.
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        client.create_payload_index(name, "doc_id", models.PayloadSchemaType.KEYWORD)


def replace_document(client: QdrantClient, collection: str, doc_id: str, chunks: list[Chunk],
                     vectors: list[list[float]], sparse: list[models.SparseVector]) -> None:
    client.delete(collection, points_selector=models.FilterSelector(filter=models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])))
    if chunks:
        client.upsert(collection, points=[
            models.PointStruct(id=c.id, vector={DENSE: v, SPARSE: sv}, payload=c.model_dump(exclude={"id"}))
            for c, v, sv in zip(chunks, vectors, sparse)
        ])
