"""The A vs B comparison must pair questions by id, count only the questions whose verdict changed,
use the exact McNemar test, never call a 1 to 2 question gap a win, and refuse runs that used a
different answer model or judge."""

import json

import pytest

from eval import compare_runs as cr
from knowledge.answer import NOT_FOUND

SETTINGS = {"mode": "baseline", "judge": "groq", "k": None, "answer_model": "groq/openai/gpt-oss-20b"}


def row(i, typ, verdict, answer="An answer [1].", decomposed=False, tokens=1000, latency=10.0):
    return {"id": i, "type": typ, "verdict": verdict, "answer": answer, "decomposed": decomposed,
            "tokens": tokens, "latency_s": latency}


def run(rows, prompt="v2_check", partial=False, **over):
    return {**SETTINGS, "prompt": prompt, **over, "rows": rows, "partial": partial, "path": f"/x/{prompt}.json"}


def pair(a_verdicts, b_verdicts, types=None):
    ids = [f"q{n}" for n in range(len(a_verdicts))]
    types = types or ["multi_hop"] * len(ids)
    a = run([row(i, t, v) for i, t, v in zip(ids, types, a_verdicts)])
    b = run([row(i, t, v, decomposed=t == "multi_hop") for i, t, v in zip(ids, types, b_verdicts)],
            prompt="v2_check_decomp")
    return a, b


@pytest.mark.parametrize("b,c,p", [(0, 0, 1.0), (1, 4, 0.375), (0, 5, 0.0625), (0, 6, 0.03125), (3, 3, 1.0),
                                   (2, 10, 0.0386)])
def test_mcnemar_exact_matches_binomial(b, c, p):
    assert cr.mcnemar_exact(b, c) == pytest.approx(p, abs=1e-4)
    assert cr.mcnemar_exact(b, c) == cr.mcnemar_exact(c, b)


def test_pairs_by_id_and_counts_per_type():
    types = ["lookup", "lookup", "exact_match", "multi_hop", "multi_hop", "multi_hop"]
    a, b = pair(["correct", "correct", "incorrect", "incorrect", "incorrect", "correct"],
                ["correct", "incorrect", "incorrect", "correct", "correct", "correct"], types)
    b["rows"].reverse()                                   # order in the file must not matter
    c = cr.compare(a, b)
    assert c["per_type"]["lookup"] == {"n": 2, "a": 2, "b": 1}
    assert c["per_type"]["multi_hop"] == {"n": 3, "a": 1, "b": 3}
    assert c["per_type"]["all"] == {"n": 6, "a": 3, "b": 4}
    assert c["only_a"] == ["q1"] and sorted(c["only_b"]) == ["q3", "q4"]
    assert c["split"] == {"multi_hop": 3, "others": 0}
    assert c["p"] == pytest.approx(1.0)


def test_small_gap_is_within_noise_even_if_one_sided():
    a, b = pair(["incorrect"] * 2 + ["correct"] * 5, ["correct"] * 7)
    c = cr.compare(a, b)
    assert len(c["only_b"]) == 2 and not c["only_a"]
    assert c["verdict"].startswith("Within noise") and "Keep decomposition off" in c["verdict"]


def test_large_gap_with_low_p_is_called_but_needs_a_second_pair():
    a, b = pair(["incorrect"] * 7 + ["correct"] * 3, ["correct"] * 10)
    c = cr.compare(a, b)
    assert c["p"] < 0.05
    assert c["verdict"].startswith("B (decomposition) is better") and "second pair" in c["verdict"]


def test_gap_above_noise_but_high_p_stays_within_noise():
    a, b = pair(["incorrect"] * 5 + ["correct"] * 2, ["correct"] * 5 + ["incorrect"] * 2)   # 5 up, 2 down
    c = cr.compare(a, b)
    assert len(c["only_b"]) - len(c["only_a"]) == 3 and c["p"] >= 0.05
    assert c["verdict"].startswith("Within noise")


def test_no_change_and_cancelling_changes():
    a, b = pair(["correct", "incorrect"], ["correct", "incorrect"])
    assert cr.compare(a, b)["verdict"].startswith("No question changed verdict")
    a, b = pair(["correct", "incorrect"], ["incorrect", "correct"])
    v = cr.compare(a, b)["verdict"]
    assert v.startswith("Within noise") and "cancel out" in v


@pytest.mark.parametrize("key,value", [("answer_model", "mistral/mistral-small-latest"), ("judge", "mistral"),
                                       ("mode", "hybrid"), ("k", 8)])
def test_refuses_runs_that_are_not_comparable(key, value):
    a, b = pair(["correct"], ["correct"])
    b[key] = value
    with pytest.raises(ValueError, match=key):
        cr.compare(a, b)


def test_wrong_delivered_does_not_count_refusals():
    a = run([row("q0", "lookup", "incorrect", answer=NOT_FOUND), row("q1", "lookup", "incorrect")])
    b = run([row("q0", "lookup", "incorrect"), row("q1", "lookup", "incorrect", answer=f"  {NOT_FOUND} sorry")])
    c = cr.compare(a, b)
    assert c["wrong"] == {"a": 1, "b": 1}


def test_partial_jsonl_compares_only_common_questions(tmp_path):
    full = tmp_path / "A.json"
    full.write_text(json.dumps({**SETTINGS, "prompt": "v2_check",
                                "rows": [row(f"q{n}", "lookup", "correct") for n in range(4)]}), encoding="utf-8")
    part = tmp_path / "B.partial.jsonl"
    part.write_text("\n".join([json.dumps({"_settings": {**SETTINGS, "prompt": "v2_check_decomp"}}),
                               json.dumps(row("q0", "lookup", "correct")),
                               json.dumps(row("q2", "lookup", "incorrect")), ""]), encoding="utf-8")
    a, b = cr.load(str(full)), cr.load(str(part))
    assert b["partial"] and len(b["rows"]) == 2
    c = cr.compare(a, b)
    assert c["common"] == 2 and c["only_a"] == ["q2"]
    assert "provisional" in c["verdict"] and "Only 2 of 4" in c["verdict"]


def test_main_writes_markdown(tmp_path, capsys):
    a, b = pair(["correct", "incorrect"], ["correct", "correct"], ["lookup", "multi_hop"])
    pa, pb = tmp_path / "A.json", tmp_path / "B.json"
    for p, r in ((pa, a), (pb, b)):
        p.write_text(json.dumps({k: v for k, v in r.items() if k not in ("partial", "path")}), encoding="utf-8")
    out = tmp_path / "cmp.md"
    cr.main([str(pa), str(pb), "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "| multi_hop | 1 | 0/1 (0%) | 1/1 (100%) | +1 |" in text
    assert "Better only with B: q1 (multi_hop)." in text and "Better only with A: none." in text
    assert "**Verdict:** Within noise" in text


def test_main_refuses_with_a_message_not_a_traceback(tmp_path):
    pa, pb = tmp_path / "A.json", tmp_path / "B.json"
    pa.write_text(json.dumps({"judge": "groq", "rows": []}), encoding="utf-8")       # older run: no model recorded
    pb.write_text(json.dumps({**SETTINGS, "rows": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not comparable: answer_model differs"):
        cr.main([str(pa), str(pb)])
