"""Typed relations by LLM, constrained to the schema, with a verbatim quote or nothing.

  python -m knowledge.graph.llm_relations --limit 60 --max-tokens 120000
  python -m knowledge.graph.llm_relations --dry-run          which chunks, how many tokens

Which chunks: git_docs chunks that hold an error line or a command written to be run, ranked
by how much troubleshooting they contain (error lines count most). The rules already give
mentions, options and risk; the LLM is asked only for what rules cannot see: which error is
caused by what, what fixes it, what undoes a command, which concept a command needs.

Safety net, in code, not in the prompt:
- relation and both types must be in SCHEMA (domain -> range), else the item is dropped;
- the quote must be found verbatim in the chunk (whitespace-insensitive) and be at most 25
  words, else dropped. No quote, no edge;
- names are cut to 80 characters; a command must start with "git ".
Cost control: each chunk's answer is cached under data/llm_cache/<sha256 of model + prompt
version + chunk text>.json, so a rerun pays only for new or changed chunks. A 429 is retried
with backoff by common/llm.py; if it persists the run stops and keeps what it has.
"""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from knowledge.graph import rules
from knowledge.graph.provenance import DATA, load_chunks

PROMPT_VERSION = "rel-v1"
TYPES = ("error", "cause", "command", "flag", "concept")
SCHEMA = {                       # relation: (allowed subject types, allowed object types)
    "CAUSED_BY": ({"error"}, {"cause"}),
    "FIXED_BY": ({"cause"}, {"command"}),
    "UNDONE_BY": ({"command"}, {"command"}),
    "REQUIRES": ({"command"}, {"concept"}),
    "HAS_OPTION": ({"command"}, {"flag"}),
    "EXPLAINED_IN": ({"concept"}, {"section"}),
}
# Per million tokens (input, output), as in eval/run_git_e2e.py.
PRICES = {"openai/gpt-oss-20b": (0.10, 0.50), "openai/gpt-oss-120b": (0.15, 0.75)}
TROUBLE = re.compile(r"\b(conflict|reject|undo|revert|abort|recover|lost|fix|resolve|detached|fail|error|"
                     r"denied|force|reset|reflog|restore)\w*", re.I)

SYSTEM = """You extract troubleshooting knowledge about Git from ONE documentation passage.
Return JSON only: {"relations": [ ... ]}. Each item:
{"subject": "...", "subject_type": "...", "relation": "...", "object": "...", "object_type": "...", "quote": "..."}

Allowed relations (subject_type -> object_type):
- CAUSED_BY: error -> cause        (an error message and why it happens)
- FIXED_BY: cause -> command        (a situation and the git command that fixes it)
- UNDONE_BY: command -> command     (a command and the command that undoes it)
- REQUIRES: command -> concept      (a command and the idea a beginner must understand to use it)
- HAS_OPTION: command -> flag       (a command and an option that changes what it does)
- EXPLAINED_IN: concept -> section  (a concept this passage explains; object is "this section")

Rules:
- Only what THIS passage states. No general knowledge. If nothing fits, return {"relations": []}.
- "quote": copy 5 to 25 consecutive words from the passage, exactly as written, that state the relation.
- command: the git command without arguments or with its key option, e.g. "git rebase", "git reset --hard".
- error: the message as printed, e.g. "fatal: refusing to merge unrelated histories".
- cause and concept: a short noun phrase, at most 8 words.
- At most 6 relations."""


def norm_ws(s: str) -> str:
    return " ".join(s.split()).lower()


def score(chunk: dict) -> int:
    text = chunk["text"]
    errors = len(rules.ERROR_LINE.findall(text))
    cmds = len(rules.command_lines(text))
    if not errors and not cmds:
        return 0
    return 3 * errors + min(cmds, 5) + min(len(TROUBLE.findall(text)), 5)


def select(chunks: list[dict], limit: int, collections=("git_docs",)) -> list[dict]:
    ranked = sorted((c for c in chunks if c["collection"] in collections and score(c) > 0),
                    key=lambda c: (-score(c), c["id"]))
    return ranked[:limit]


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}|{PROMPT_VERSION}|{text}".encode()).hexdigest()


def validate(items, chunk_text: str) -> tuple[list[dict], list[str]]:
    """Keep only schema-conforming items whose quote is in the passage. Returns (kept, reasons
    for every dropped item) so the report can say what the safety net removed."""
    kept, dropped = [], []
    hay = norm_ws(chunk_text)
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            dropped.append("not an object")
            continue
        rel = it.get("relation")
        st, ot = it.get("subject_type"), it.get("object_type")
        if rel not in SCHEMA or st not in SCHEMA[rel][0] or ot not in SCHEMA[rel][1]:
            dropped.append(f"outside schema: {st} -{rel}-> {ot}")
            continue
        q = str(it.get("quote") or "").strip().strip('"')
        if not q or len(q.split()) > 25:
            dropped.append("no quote" if not q else "quote over 25 words")
            continue
        if norm_ws(q) not in hay:
            dropped.append("quote not in the passage")
            continue
        subj = str(it.get("subject") or "").strip()[:80]
        obj = str(it.get("object") or "").strip()[:80] if ot != "section" else "this section"
        if not subj or not obj:
            dropped.append("empty name")
            continue
        if (st == "command" and not subj.lower().startswith("git ")) or \
           (ot == "command" and not obj.lower().startswith("git ")):
            dropped.append("command is not a git command")
            continue
        # the quote as it is written in the chunk (original spacing), for the evidence item
        m = re.search(r"\s+".join(map(re.escape, q.split())), chunk_text, re.I)
        kept.append({"subject": subj, "subject_type": st, "relation": rel, "object": obj, "object_type": ot,
                     "quote": m.group(0) if m else q})
    return kept, dropped


def parse(raw: str):
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)).get("relations", []) if m else []
    except (json.JSONDecodeError, AttributeError):
        return []


def extract_one(chunk: dict, provider: str, model: str, folder: Path) -> dict:
    """One chunk -> cached record. Calls the LLM only on a cache miss."""
    from common import llm
    path = folder / f"{cache_key(f'{provider}/{model}', chunk['text'])}.json"
    if path.exists():
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["cached"] = True
        return rec
    task = f"graph-extract-{chunk['id']}"
    t0 = time.perf_counter()
    raw = llm.chat([{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"Passage (section: {chunk['section']}):\n\n{chunk['text']}"}],
                   task_id=task, agent="graph_extract", temperature=0.0, provider=provider, model=model,
                   json_mode=True)
    usage = llm.USAGE.get(task, [])
    pin = sum(u[3] for u in usage)
    pout = sum(u[4] for u in usage)
    kept, dropped = validate(parse(raw), chunk["text"])
    rec = {"chunk_id": chunk["id"], "model": f"{provider}/{model}", "prompt": PROMPT_VERSION,
           "relations": kept, "dropped": dropped, "prompt_tokens": pin, "completion_tokens": pout,
           "seconds": round(time.perf_counter() - t0, 2)}
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    rec["cached"] = False
    return rec


def load_cached(folder: Path = DATA / "llm_cache") -> list[dict]:
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def cost(recs: list[dict]) -> tuple[int, int, float]:
    pin = sum(r["prompt_tokens"] for r in recs)
    pout = sum(r["completion_tokens"] for r in recs)
    usd = sum(r["prompt_tokens"] * PRICES.get(r["model"].split("/", 1)[1], (0, 0))[0] / 1e6 +
              r["completion_tokens"] * PRICES.get(r["model"].split("/", 1)[1], (0, 0))[1] / 1e6 for r in recs)
    return pin, pout, usd


def main() -> None:
    from common.console import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="chunks to send (best-ranked first)")
    ap.add_argument("--max-tokens", type=int, default=120_000, help="stop once this many tokens are spent")
    ap.add_argument("--provider", default="groq")
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    chunks = select(load_chunks(), a.limit)
    folder = DATA / "llm_cache"
    if a.dry_run:
        chars = sum(len(c["text"]) for c in chunks)
        print(f"{len(chunks)} chunks, about {chars // 4 + len(chunks) * 450} prompt tokens before answers")
        for c in chunks[:15]:
            print(f"  {score(c):3} {c['document_id']} | {c['section'][:70]}")
        return
    from common import llm
    done, spent = [], 0
    for i, c in enumerate(chunks, 1):
        if spent >= a.max_tokens:
            print(f"token budget reached ({spent}); {len(chunks) - i + 1} chunk(s) left for a later run")
            break
        try:
            rec = extract_one(c, a.provider, a.model, folder)
        except llm.ProviderUnavailable as e:
            print(f"stopping, nothing lost (cached so far stays): {e}")
            break
        except llm.ProviderAccessError as e:
            raise SystemExit(f"stopping: {e}")
        if not rec["cached"]:
            spent += rec["prompt_tokens"] + rec["completion_tokens"]
        done.append(rec)
        took = "cache" if rec["cached"] else f"{rec['seconds']}s"
        print(f"[{i}/{len(chunks)}] {took} kept {len(rec['relations'])} dropped {len(rec['dropped'])}  "
              f"{c['section'][:60]}")
    pin, pout, usd = cost([r for r in done if not r["cached"]])
    allrec = load_cached(folder)
    print(f"\nthis run: {pin} prompt + {pout} completion tokens, about ${usd:.3f} at paid-tier prices")
    print(f"cache: {len(allrec)} chunks, {sum(len(r['relations']) for r in allrec)} relations kept, "
          f"{sum(len(r['dropped']) for r in allrec)} dropped by the checks")


if __name__ == "__main__":
    main()
