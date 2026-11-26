"""The averaged benchmark must report mean and spread per setting, keep a tuned-only and a
held-out-only file of the same setting in one block, show the beginner score, and leave replays
out (they rerun a recorded plan, so they would count one run twice)."""

import json
import sys

from eval import average_runs as av


def part(answers, passed, beginner, usd=0.01):
    if not answers:
        return {"answers": 0, "passed": "-", "beginner_items_ok": "-", "beginner.risk_plain": "-",
                "dangerous_without_backup": 0, "risk_under_rated_steps": 0, "usd_estimate_total": 0.0}
    return {"answers": answers, "passed": passed, "beginner_items_ok": beginner, "beginner.risk_plain": beginner,
            "dangerous_without_backup": 0, "risk_under_rated_steps": 1, "usd_estimate_total": usd}


def e2e_file(tag, tuned, held, **extra):
    return {"summary": {"tag": tag, "answer_model": "groq/openai/gpt-oss-20b", "judge": "groq/openai/gpt-oss-120b",
                        "tuned": tuned, "held_out": held, **extra}, "rows": []}


def knowledge_file(prompt, correct):
    rows = [{"verdict": "correct", "answer": "x"}] * 3
    return {"mode": "baseline", "prompt": prompt, "judge": "groq", "answer_model": "groq/openai/gpt-oss-20b",
            "summary": {t: {"correct": correct, "retrieval_hit": 1.0} for t in ("lookup", "exact_match", "multi_hop", "all")},
            "rows": rows}


def test_setting_tag_joins_the_two_halves():
    assert av.setting_tag("assistant_tuned") == "assistant"
    assert av.setting_tag("assistant_held_out") == "assistant"
    assert av.setting_tag("assistant_tuned_x3") == "assistant_x3"
    assert av.setting_tag("oracle") == "oracle"


def test_split_files_of_one_setting_make_one_block_with_mean_and_spread():
    runs = [e2e_file("assistant_tuned", part(19, "60%", "50%", usd=0.02), part(0, "-", "-")),
            e2e_file("assistant_tuned", part(19, "80%", "70%", usd=0.04), part(0, "-", "-")),
            e2e_file("assistant_held_out", part(0, "-", "-"), part(6, "50%", "40%"))]
    text = "\n".join(av.e2e_table(runs))
    assert text.count("**assistant**") == 1 and "2 tuned and 1 held-out run(s)" in text
    assert "| passed | 70% (60%-80%) | 50% |" in text
    assert "| beginner_items_ok | 60% (50%-70%) | 40% |" in text
    assert "| beginner.risk_plain | 60% (50%-70%) | 40% |" in text
    assert "| usd_estimate_total (mean per run) | 0.03 | 0.01 |" in text      # empty halves don't dilute it


def test_main_leaves_replays_out_and_keeps_knowledge_settings_apart(tmp_path, monkeypatch, capsys):
    files = {"t1.json": e2e_file("assistant_tuned", part(19, "60%", "50%"), part(0, "-", "-")),
             "replay.json": e2e_file("replay_assistant_tuned", part(19, "100%", "50%"), part(0, "-", "-"),
                                     replayed_from="t1.json"),
             "a.json": knowledge_file("v2_check", 0.8), "b.json": knowledge_file("v2_check_decomp", 0.9)}
    for name, d in files.items():
        (tmp_path / name).write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "avg.md"
    monkeypatch.setattr(sys, "argv", ["x", *(str(tmp_path / n) for n in files), "--out", str(out)])
    av.main()
    text = out.read_text(encoding="utf-8")
    assert "skipped (a replay of t1.json" in capsys.readouterr().out
    assert "replay_assistant" not in text and "| passed | 60% | n/a |" in text
    assert "| baseline+v2_check | groq/openai/gpt-oss-20b |" in text
    assert "| baseline+v2_check_decomp | groq/openai/gpt-oss-20b |" in text
