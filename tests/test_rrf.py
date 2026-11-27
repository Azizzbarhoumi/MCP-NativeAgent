"""RRF merge: rank-based fusion of dense and keyword results."""

from knowledge.retrieve import Hit, rrf_merge


def h(cid: str, score: float = 0.0) -> Hit:
    return Hit(chunk_id=cid, score=score, text=cid, source="s", path="p", section="", url=None)


def test_chunk_found_by_both_lists_wins():
    dense = [h("a", 0.9), h("b", 0.8), h("c", 0.7)]
    sparse = [h("c", 25.0), h("d", 20.0), h("a", 5.0)]
    merged = rrf_merge([dense, sparse], k=4, rrf_k=60)
    # a: 1/61 + 1/63, c: 1/63 + 1/61 -> tie at top; b and d appear once each
    assert {x.chunk_id for x in merged[:2]} == {"a", "c"}
    assert [x.chunk_id for x in merged[2:]] == ["b", "d"]


def test_raw_scores_are_ignored_only_ranks_count():
    # A huge BM25 score must not beat a chunk ranked first by both lists.
    dense = [h("x", 0.1), h("y", 0.05)]
    sparse = [h("x", 1.0), h("z", 999.0)]
    assert rrf_merge([dense, sparse], k=1)[0].chunk_id == "x"


def test_keeps_k_and_scores_are_rrf_sums():
    merged = rrf_merge([[h("a"), h("b"), h("c")]], k=2, rrf_k=60)
    assert [x.chunk_id for x in merged] == ["a", "b"]
    assert abs(merged[0].score - 1 / 61) < 1e-12


def test_no_duplicates():
    merged = rrf_merge([[h("a"), h("b")], [h("b"), h("a")]], k=5)
    assert len(merged) == len({x.chunk_id for x in merged}) == 2


def test_cap_per_doc_keeps_order_and_limits_one_document():
    from knowledge.retrieve import Hit, cap_per_doc
    def h(i, path):
        return Hit(chunk_id=str(i), score=1.0 / (i + 1), text="t", source="s", path=path, section="", url=None)
    hits = [h(0, "manual"), h(1, "manual"), h(2, "manual"), h(3, "stash"), h(4, "manual"), h(5, "reflog")]
    out = cap_per_doc(hits, 2, 4)
    assert [x.chunk_id for x in out] == ["0", "1", "3", "5"]


def test_dev_docs_has_no_cap_so_its_benchmark_is_unchanged():
    from knowledge.config import load_config
    cfg = load_config()
    assert "dev_docs" not in cfg.retrieval.max_per_doc
