"""Answer rubrics: a short list of yes/no items for scenarios that are graded on the advice
(nothing to fix in the repo). Rubrics live in rubrics/<scenario>.yaml.

  python -m eval.git_scenarios.rubric auth_https_password --demo     grade two sample answers

Deterministic items (regex) are checked here; `llm` items go to the judge in ONE call that
returns {"<id>": {"ok": true/false, "why": "..."}}. Unreadable judge output fails the item.
"""

import json
import re
import sys
from pathlib import Path

import yaml

RUBRICS = Path(__file__).resolve().parent / "rubrics"
TYPES = {"llm", "regex_none", "regex_any", "every_step_why"}

JUDGE_PROMPT = """You check a git assistant's answer against rubric items. For each item,
decide strictly whether the answer satisfies it. Return JSON only:
{{"<item id>": {{"ok": true|false, "why": "short reason"}}, ...}}

ITEMS
{items}

ANSWER
cause: {cause}
explanation: {explanation}
steps:
{steps}
warnings: {warnings}"""


class RubricError(ValueError):
    pass


def load(scenario_id: str, folder: Path = RUBRICS) -> dict:
    path = folder / f"{scenario_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        raise RubricError(f"{path.name}: no items")
    seen = set()
    for it in items:
        if it.get("type") not in TYPES:
            raise RubricError(f"{path.name}: item {it.get('id')!r} has type {it.get('type')!r}, expected one of {sorted(TYPES)}")
        if not it.get("id") or it["id"] in seen:
            raise RubricError(f"{path.name}: missing or duplicate id {it.get('id')!r}")
        if not str(it.get("text", "")).strip():
            raise RubricError(f"{path.name}: item {it['id']} has no text")
        if it["type"] == "every_step_why":
            if not isinstance(it.get("min_words"), int) or it["min_words"] < 1:
                raise RubricError(f"{path.name}: item {it['id']} needs min_words >= 1")
        elif it["type"] != "llm":
            try:
                re.compile(it.get("pattern", ""))
            except re.error as e:
                raise RubricError(f"{path.name}: item {it['id']} bad pattern: {e}")
            if not it.get("pattern"):
                raise RubricError(f"{path.name}: item {it['id']} needs a pattern")
        seen.add(it["id"])
    return data


def _answer_text(ans: dict) -> str:
    return " ".join([ans.get("cause", ""), ans.get("explanation", ""),
                     *[s["command"] + " " + s.get("why", "") for s in ans.get("steps", [])],
                     *ans.get("warnings", [])])


def grade(ans: dict, rubric: dict, judge_fn, verdicts: dict | None = None) -> list[dict]:
    """judge_fn(prompt) -> raw text. Returns one result per item, in rubric order.
    `verdicts` ({item id: {"ok", "why"}}) skips the judge call when another call already
    graded the llm items (the end-to-end benchmark grades diagnosis and rubric together)."""
    results, llm_items = {}, [it for it in rubric["items"] if it["type"] == "llm"]
    commands = [s["command"] for s in ans.get("steps", []) if s.get("command")]
    for it in rubric["items"]:
        if it["type"] == "regex_none":
            hits = [c for c in commands if re.search(it["pattern"], c)]
            results[it["id"]] = {"ok": not hits, "why": f"matched: {hits[0][:80]}" if hits else "no match"}
        elif it["type"] == "every_step_why":
            short = [s["command"] for s in ans.get("steps", []) if s.get("command")
                     and len(str(s.get("why", "")).split()) < it["min_words"]]
            ok = bool(commands) and not short
            results[it["id"]] = {"ok": ok, "why": f"too short or missing for: {short[0][:60]}" if short
                                 else "no command proposed" if not commands else "every command explained"}
        elif it["type"] == "regex_any":
            ok = bool(re.search(it["pattern"], _answer_text(ans), re.I))
            results[it["id"]] = {"ok": ok, "why": "found" if ok else "not found"}
    if llm_items and verdicts is not None:
        for it in llm_items:
            v = verdicts.get(it["id"])
            results[it["id"]] = ({"ok": bool(v.get("ok")), "why": str(v.get("why", ""))} if isinstance(v, dict)
                                 else {"ok": False, "why": "judge gave no verdict for this item"})
    elif llm_items:
        steps = "\n".join(f"{i}. {s['command'] or '(manual)'} -- {s.get('why', '')}" for i, s in enumerate(ans.get("steps", []), 1))
        raw = judge_fn(JUDGE_PROMPT.format(
            items="\n".join(f"- {it['id']}: {it['text']}" for it in llm_items),
            cause=ans.get("cause", ""), explanation=ans.get("explanation", ""),
            steps=steps or "(none)", warnings=ans.get("warnings", [])))
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            verdicts = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            verdicts = {}
        for it in llm_items:
            v = verdicts.get(it["id"])
            results[it["id"]] = ({"ok": bool(v.get("ok")), "why": str(v.get("why", ""))} if isinstance(v, dict)
                                 else {"ok": False, "why": "judge gave no verdict for this item"})
    return [{"id": it["id"], "type": it["type"], **results[it["id"]]} for it in rubric["items"]]


def summary(results: list[dict]) -> str:
    return f"{sum(r['ok'] for r in results)}/{len(results)}"


DEMO = {
    "good": {"cause": "GitHub stopped accepting your account password for git over HTTPS in 2021.",
             "explanation": "You need a personal access token or an SSH key instead of the password.",
             "steps": [{"command": "", "why": "On GitHub, create a personal access token yourself (Settings > Developer settings)."},
                       {"command": "git config --global credential.helper manager", "why": "Let Git Credential Manager store it safely."},
                       {"command": "git push origin main", "why": "Paste the token at the password prompt, it is stored by the manager."}],
             "warnings": ["Never put the token in the remote URL or share it in chat."]},
    "bad": {"cause": "Your password is wrong.", "explanation": "Use a token.",
            "steps": [{"command": "git remote set-url origin https://me:TOKEN@github.com/example-user/demo.git", "why": "embed the token"}],
            "warnings": ["Send me your token and I will set it up."]},
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sid = args[0] if args else "auth_https_password"
    rubric = load(sid)
    from common.llm import chat
    from eval.run_knowledge_eval import JUDGES
    judge = lambda prompt: chat([{"role": "user", "content": prompt}], task_id="rubric-demo", agent="judge",
                                temperature=0.0, json_mode=True, provider=JUDGES["groq"][0], model=JUDGES["groq"][1])
    for name, ans in DEMO.items():
        res = grade(ans, rubric, judge)
        print(f"\n{name} answer: {summary(res)}")
        for r in res:
            print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['id']:28} {r['why'][:90]}")


if __name__ == "__main__":
    main()
