"""Ingest every source into one Qdrant collection.

Usage:
  python -m knowledge.ingest                       collection from config.yaml
  python -m knowledge.ingest --collection dev_docs --samples
"""

import argparse
import random
import time
from collections import Counter

from knowledge.config import load_config
from knowledge.ingest.chunk import chunk_document
from knowledge.ingest.embed import DenseEmbedder, SparseEmbedder
from knowledge.ingest.parse import clean
from knowledge.ingest.store import ensure_collection, get_client, replace_document
from knowledge.sources.local_docs import all_adapters


def show(c) -> None:
    print(f"\n--- {c.source} | {c.path} | section: {c.section or '-'} | chunk {c.index} | {len(c.text)} chars")
    print(f"    url: {c.url}")
    print(c.text[:900] + (" ..." if len(c.text) > 900 else ""))


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=cfg.collection)
    ap.add_argument("--samples", action="store_true", help="print 5 sample chunks")
    ap.add_argument("--dry-run", action="store_true", help="chunk only: no embedding, no Qdrant")
    a = ap.parse_args()

    ch = cfg.chunking
    if not a.dry_run:
        embedder = DenseEmbedder(cfg.embedding.dense_model, cfg.embedding.batch_size)
        sparse = SparseEmbedder(cfg.embedding.sparse_model, cfg.embedding.batch_size)
        client = get_client()
        ensure_collection(client, a.collection, embedder.dim)

    t0, per_source, all_chunks, docs = time.time(), Counter(), [], 0
    for adapter in all_adapters(a.collection):
        for doc in adapter.documents(a.collection):
            doc.text = clean(doc.text, ch.max_code_lines)
            chunks = chunk_document(doc, ch.max_chars, ch.overlap_chars, ch.min_chars)
            if not a.dry_run:
                texts = [c.text for c in chunks]
                replace_document(client, a.collection, doc.id, chunks,
                                 embedder.embed_passages(texts) if chunks else [],
                                 sparse.embed_passages(texts) if chunks else [])
            docs += 1
            per_source[doc.source] += len(chunks)
            all_chunks.extend(chunks)
        print(f"  {adapter.name:22} {per_source[adapter.name]:5} chunks")

    lengths = [len(c.text) for c in all_chunks]
    print(f"\n{docs} documents -> {len(all_chunks)} chunks in '{a.collection}' "
          f"(avg {sum(lengths) // max(len(lengths), 1)} chars, max {max(lengths, default=0)}) "
          f"in {time.time() - t0:.0f}s")
    if not a.dry_run:
        print(f"points in collection: {client.count(a.collection).count}  (equal to chunks = no duplicates)")

    if a.samples and all_chunks:
        table = max(all_chunks, key=lambda c: c.text.count("\n|"))
        code = next(c for c in all_chunks if "```" in c.text and c.source == "github_actions_docs")
        random.seed(7)
        for c in [table, code, *random.sample(all_chunks, 3)]:
            show(c)


if __name__ == "__main__":
    main()
