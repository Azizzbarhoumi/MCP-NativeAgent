from eval.metrics import retrieval_coverage, retrieval_hit, summarize


def test_hit_and_coverage():
    assert retrieval_hit(["a", "b"], ["b", "c"]) is True
    assert retrieval_hit(["a"], ["c"]) is False
    assert retrieval_coverage(["a", "b", "b"], ["b", "c"]) == 0.5
    assert retrieval_coverage(["b", "c"], ["b", "c"]) == 1.0


def test_summarize_per_type_and_all():
    rows = [
        {"type": "lookup", "hit": True, "coverage": 1.0, "verdict": "correct", "latency_s": 1.0, "tokens": 100},
        {"type": "lookup", "hit": False, "coverage": 0.0, "verdict": "incorrect", "latency_s": 3.0, "tokens": 300},
        {"type": "multi_hop", "hit": True, "coverage": 0.5, "verdict": "partial", "latency_s": 2.0, "tokens": 200},
    ]
    s = summarize(rows)
    assert s["lookup"]["n"] == 2 and s["lookup"]["retrieval_hit"] == 0.5 and s["lookup"]["correct"] == 0.5
    assert s["lookup"]["latency_s"] == 2.0 and s["lookup"]["tokens"] == 200
    assert s["multi_hop"]["full_coverage"] == 0.0 and s["multi_hop"]["partial"] == 1.0
    assert s["all"]["n"] == 3 and abs(s["all"]["correct"] - 1 / 3) < 1e-9
