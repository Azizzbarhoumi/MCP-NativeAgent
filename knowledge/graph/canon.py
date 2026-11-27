"""Entity resolution: raw names -> one canonical node, with a merge log that can be undone.

Each rule folds spellings that mean the same thing. Every fold is logged with a stable id
(same rule, same names -> same id on every rebuild). To undo one, put its id under `undo:` in
knowledge/graph/merge_decisions.yaml and rebuild: the names become separate nodes again.

Rules (kept narrow on purpose: a wrong merge hides a distinction a beginner needs):
  manpage      git-rebase(1), git-rebase           -> git rebase
  flag_value   --depth=1                           -> --depth
  plural       pull requests, branches             -> pull request, branch   (concepts only)
  verb_form    rebasing, merging                   -> rebase, merge          (concepts only)
  error_line   a playbook message made of several lines -> each doc error equal to one of its lines

Not merged, and counted as remaining duplicates in the report: kebab vs snake settings
(fail-fast / fail_fast are different keys in different tools) and dotted paths vs their last
part (jobs.<job_id>.timeout-minutes / timeout-minutes).
"""

import hashlib
import re
from pathlib import Path

import yaml

DECISIONS = Path(__file__).resolve().parent / "merge_decisions.yaml"


def merge_id(rule: str, kept: str, raw: str) -> str:
    return "m" + hashlib.sha1(f"{rule}|{kept}|{raw}".encode()).hexdigest()[:10]


def load_decisions(path: Path = DECISIONS) -> dict:
    if not path.exists():
        return {"undo": [], "merge": []}
    d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"undo": list(d.get("undo") or []), "merge": list(d.get("merge") or [])}


IRREGULAR = {"branches": "branch", "indices": "index", "indexes": "index", "aliases": "alias",
             "patches": "patch", "stashes": "stash", "pushes": "push", "statuses": "status"}
ING = {"rebasing": "rebase", "merging": "merge", "stashing": "stash", "committing": "commit",
       "branching": "branch", "tagging": "tag", "cloning": "clone", "pushing": "push", "pulling": "pull",
       "fetching": "fetch", "staging": "stage", "resetting": "reset", "reverting": "revert",
       "cherry-picking": "cherry-pick", "ignoring": "ignore", "tracking": "track"}


def singular(word: str) -> str:
    if word in IRREGULAR:
        return IRREGULAR[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def concept_key(name: str) -> tuple[str, str | None]:
    """Lower-case concept name with its last word singular and -ing verbs as their base."""
    words = re.sub(r"[^\w\s'-]", " ", name.lower()).split()
    words = [w for w in words if w not in ("the", "a", "an")] or words
    rule = None
    if words and words[-1] in ING:
        words[-1], rule = ING[words[-1]], "verb_form"
    elif words:
        s = singular(words[-1])
        if s != words[-1]:
            words[-1], rule = s, "plural"
    return " ".join(words), rule


class Canon:
    """resolve(type, raw) -> (type, key). Records every fold in self.log."""

    def __init__(self, decisions: dict | None = None):
        d = decisions or {"undo": [], "merge": []}
        self.undo = set(d.get("undo", []))
        self.manual = {(m["type"], m["from"]): m["into"] for m in d.get("merge", [])}
        self.log: dict[str, dict] = {}

    def _fold(self, rule: str, typ: str, raw: str, kept: str) -> str:
        if raw == kept:
            return kept
        mid = merge_id(rule, f"{typ}:{kept}", f"{typ}:{raw}")
        if mid in self.undo:
            return raw
        e = self.log.setdefault(mid, {"id": mid, "rule": rule, "kept": f"{typ}:{kept}",
                                      "merged": f"{typ}:{raw}", "count": 0})
        e["count"] += 1
        return kept

    def resolve(self, typ: str, raw: str) -> tuple[str, str]:
        if (typ, raw) in self.manual:
            return typ, self._fold("manual", typ, raw, self.manual[(typ, raw)])
        if typ == "command":
            m = re.fullmatch(r"git-([a-z][a-z0-9-]*)(?:\(\d\))?", raw)
            if m:
                return typ, self._fold("manpage", typ, raw, f"git {m.group(1)}")
        if typ == "flag" and "=" in raw:
            return typ, self._fold("flag_value", typ, raw, raw.split("=", 1)[0])
        if typ == "concept":
            key, rule = concept_key(raw)
            if rule:
                return typ, self._fold(rule, typ, raw, key)
            return typ, key
        return typ, raw

    def alias(self, typ: str, raw: str, kept: str, rule: str) -> str:
        """An explicit fold decided by the builder (error lines of a playbook message)."""
        return self._fold(rule, typ, raw, kept)


# ---- duplicate measurement ---------------------------------------------------------------

def dup_key(name: str) -> str:
    """Deliberately looser than the rules above: what a person would call 'the same name'.
    Used only to measure the duplicate rate, never to merge."""
    s = name.lower()
    s = re.sub(r"\(\d\)$", "", s)
    s = re.sub(r"^--?", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s[:-1] if s.endswith("s") and len(s) > 4 else s


def duplicate_rate(names: list[str], sample: list[str] | None = None) -> tuple[float, list[tuple[str, str]]]:
    """Share of the sampled names that share their dup_key with another name in `names`,
    plus example pairs."""
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(dup_key(n), []).append(n)
    sample = sample if sample is not None else names
    dups = [n for n in sample if len(groups.get(dup_key(n), [])) > 1]
    pairs = []
    for n in dups[:40]:
        # the same name on two nodes (e.g. a `code` span and a setting) counts too
        other = next((x for x in groups[dup_key(n)] if x != n), f"{n} (same name, another node)")
        pairs.append((n, other))
    return (len(dups) / len(sample) if sample else 0.0), pairs
