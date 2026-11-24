"""Pure metric functions for the knowledge eval (no I/O, so they are easy to test)."""

from collections import defaultdict


def retrieval_hit(retrieved_docs: list[str], expected_docs: list[str]) -> bool:
    """True if ANY retrieved chunk comes from ANY expected document."""
    return bool(set(retrieved_docs) & set(expected_docs))


def retrieval_coverage(retrieved_docs: list[str], expected_docs: list[str]) -> float:
    """Share of expected documents that appear among the retrieved chunks (multi-hop needs all)."""
    expected = set(expected_docs)
    return len(expected & set(retrieved_docs)) / len(expected) if expected else 0.0


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Per question type plus 'all': n, retrieval hit rate, full coverage rate, correct rate,
    partial rate, mean latency (s) and mean tokens. Rows need: type, hit, coverage, verdict,
    latency_s, tokens."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["type"]].append(r)
        groups["all"].append(r)

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    return {
        t: {
            "n": len(rs),
            "retrieval_hit": mean([r["hit"] for r in rs]),
            "full_coverage": mean([r["coverage"] == 1.0 for r in rs]),
            "correct": mean([r["verdict"] == "correct" for r in rs]),
            "partial": mean([r["verdict"] == "partial" for r in rs]),
            "latency_s": mean([r["latency_s"] for r in rs]),
            "tokens": mean([r["tokens"] for r in rs]),
        }
        for t, rs in groups.items()
    }


def table(summary: dict[str, dict], mode: str) -> str:
    order = [t for t in ("lookup", "exact_match", "multi_hop") if t in summary] + ["all"]
    lines = [f"| {mode} | n | retrieval hit | full coverage | correct | partial | latency s | tokens |",
             "|---|---|---|---|---|---|---|---|"]
    for t in order:
        s = summary[t]
        lines.append(f"| {t} | {s['n']} | {s['retrieval_hit']:.0%} | {s['full_coverage']:.0%} | "
                     f"{s['correct']:.0%} | {s['partial']:.0%} | {s['latency_s']:.1f} | {s['tokens']:.0f} |")
    return "\n".join(lines)
