"""Ask the knowledge base a question.

Usage:
  python -m knowledge.ask "How do I cache pip dependencies in GitHub Actions?"
  python -m knowledge.ask "..." --k 8
"""

import argparse

from common.schemas import new_id
from common.tracing import flush, get_langfuse
from knowledge.answer import answer_question


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--k", type=int, default=None, help="chunks to retrieve (default: config)")
    ap.add_argument("--mode", default="baseline", choices=["baseline", "hybrid"])
    ap.add_argument("--prompt", default="v2", choices=["v1", "v2"])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--decompose", action="store_true")
    ap.add_argument("--collection", default=None, help="Qdrant collection (default: config, dev_docs)")
    a = ap.parse_args()

    if get_langfuse() is None:
        print("WARNING: Langfuse keys missing in .env; tracing is DISABLED for this run.")
    task_id = new_id()
    response, hits, timing = answer_question(a.question, task_id, k=a.k, mode=a.mode, prompt=a.prompt, check=a.check, decomposition=a.decompose, collection=a.collection)

    print(f"\nAnswer:\n{response.answer}\n")
    print("Citations:")
    for i in timing["cited"]:
        h = hits[i - 1]
        print(f"  [{i}] {h.source}/{h.path} > {h.section or '-'}\n       {h.url or ''}")
    if not timing["cited"]:
        print("  (none)")
    print(f"\nRetrieved ({len(hits)}, mode={a.mode}):")
    for i, h in enumerate(hits, 1):
        mark = "*" if i in timing["cited"] else " "
        print(f" {mark}[{i}] score={h.score:.3f}  {h.source}/{h.path} > {h.section or '-'}  id={h.chunk_id[:8]}")
    print(f"\nretrieval {timing['retrieval_s']:.2f}s | answer {timing['answer_s']:.2f}s | trace task-{task_id[:8]}")
    flush()


if __name__ == "__main__":
    main()
