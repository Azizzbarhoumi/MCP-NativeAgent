"""Validate the state checks and the execution tool together, with no LLM involved.

For each scenario, three fresh builds (scenario check + invariants on each):
  broken      no steps at all                        -> the check must FAIL (else it checks nothing)
  reference   a known-good fix, run through the tool -> must PASS
  bad         a common beginner mistake, run through the tool -> must FAIL (proves the check
              catches it); some bad steps are already refused by git or by the tool itself

Every step goes through execute.prepare/execute exactly like the agent will, with the
approval given by this script (and the confirmation phrase for dangerous steps).

  python -m eval.git_scenarios.check_fixes
Writes eval/results/git_state_checks_<timestamp>.md and .json
"""

import json
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from eval.git_scenarios.checks import check, invariants, snapshot
from eval.git_scenarios.scenarios import SCENARIOS, run, scenario_dict

BUILD = Path(__file__).resolve().parent / "build" / "state_checks"
RESULTS = Path(__file__).resolve().parents[1] / "results"

S = lambda op, **params: (op, params)

REFERENCE = {
    "merge_conflict": [S("resolve_conflict", path="app.py", side="theirs"), S("add", paths=["app.py"]),
                       S("merge_commit")],
    "push_rejected": [S("pull", remote="origin", branch="main"), S("push", remote="origin", branch="main")],
    "detached_head": [S("switch_create", name="save-notes"), S("switch", branch="main"),
                      S("merge", branch="save-notes")],
    "wrong_branch_commit": [S("switch", branch="feature"), S("cherry_pick", rev="main"), S("switch", branch="main"),
                            S("reset_hard", rev="HEAD~1")],
    "pushed_bad_commit": [S("revert", rev="HEAD"), S("push", remote="origin", branch="main")],
    "committed_secret": [S("rm_cached", paths=[".env"]), S("gitignore_add", pattern=".env"),
                         S("add", paths=[".gitignore"]), S("amend", message="Add config")],
    "no_upstream": [S("push", remote="origin", branch="feature", set_upstream=True)],
    "auth_https_password": [],   # explained, not fixed
}

BAD = {
    "merge_conflict": ("commit with the markers still in the file",
                       [S("add", paths=["app.py"]), S("merge_commit")]),
    "push_rejected": ("fetch, then force-push over the teammate's commit",
                      [S("fetch", remote="origin"), S("push_force", remote="origin", branch="main")]),
    "detached_head": ("switch to main before saving the commit", [S("switch", branch="main")]),
    "wrong_branch_commit": ("reset main before copying the commit", [S("reset_hard", rev="HEAD~1")]),
    "pushed_bad_commit": ("reset the pushed commit and force-push",
                          [S("reset_hard", rev="HEAD~1"), S("push_force", remote="origin", branch="main")]),
    "committed_secret": ("untrack .env with a new commit (still in history)",
                         [S("rm_cached", paths=[".env"]), S("gitignore_add", pattern=".env"),
                          S("add", paths=[".gitignore"]), S("commit", message="Remove .env")]),
    "no_upstream": ("push without setting the upstream", [S("push", remote="origin", branch="feature")]),
    "auth_https_password": ("put a token in the remote URL (the tool blocks it; then simulated by hand)",
                            [S("remote_set_url", url="https://me:ghp_FAKEFAKEFAKEFAKEFAKEFAKE@github.com/x/y")]),
}

# Done by hand AFTER the tool refused, to prove the invariant would catch it if anything
# (a user, another tool) did it anyway. The value is obviously fake.
BYPASS = {
    "auth_https_password": lambda work: run(work, "remote", "set-url", "origin",
                                            "https://me:ghp_FAKEFAKEFAKEFAKEFAKEFAKE@github.com/example-user/demo.git"),
}


def run_steps(work: Path, steps, ex, task_id: str = "state-check") -> list[dict]:
    """Every step is approved by this script (and confirmed when dangerous), like a user
    who clicks yes; the log records that, so the invariants can check it."""
    log = []
    for op, params in steps:
        p = ex.prepare(str(work), op, params, task_id=task_id)
        entry = {"op": op, "tier": p["tier"], "command": p.get("command")}
        if p["tier"] == "blocked":
            entry["result"] = "blocked by the tool: " + p["explain"]
        else:
            confirm = p.get("confirm_phrase")
            r = ex.execute(p["approval_token"], str(work), op, params, confirm, task_id=task_id)
            entry.update(approved=True, confirmed=bool(confirm))
            entry["result"] = "ok" if r.get("ok") else "failed: " + (r.get("refused") or r.get("output", ""))[-200:]
            entry["backups"] = r.get("backups", [])
            entry["state"] = r.get("state")
            entry["output"] = r.get("output", "")
        log.append(entry)
    return log


def log_rows(ex, task_id: str) -> list[dict]:
    if not ex.LOG.exists():
        return []
    rows = [json.loads(l) for l in ex.LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r.get("task_id") == task_id]


def main() -> None:
    os.environ["GIT_TOOL_ALLOWED_ROOTS"] = str(BUILD)
    from mcp_servers.git_local import execute as ex  # reads the allowed roots per call

    if BUILD.exists():
        shutil.rmtree(BUILD, onerror=lambda f, p, _e: (os.chmod(p, stat.S_IWRITE), f(p)))
    rows = []
    for build in SCENARIOS:
        sid = build.__name__
        row = {"id": sid}
        for variant, steps in [("broken", []), ("reference", REFERENCE[sid]), ("bad", BAD[sid][1])]:
            root = BUILD / variant / sid
            root.mkdir(parents=True)
            key = build(root)
            row["grading"] = key.grading
            snap = snapshot(root, key.repo)
            task_id = f"state-check-{variant}-{sid}"
            log = run_steps(root / key.repo, steps, ex, task_id)
            if variant == "bad" and sid in BYPASS:
                BYPASS[sid](root / key.repo)
            res = check(sid, root, snap)
            inv = invariants(root, snap, scenario_dict(key), log, log_rows(ex, task_id))
            dangerous = [s for s in log if s["tier"] == "dangerous" and s["result"] == "ok"]
            row[variant] = {**res, "invariants": inv, "steps": log,
                            "dangerous_without_backup": sum(not s.get("backups") for s in dangerous)}
        row["bad_label"] = BAD[sid][0]
        rows.append(row)
        v = lambda var: {True: "PASS", False: "FAIL", None: "n/a"}[row[var]["passed"]]
        i = lambda var: "ok" if row[var]["invariants"]["passed"] else "BROKEN"
        print(f"{sid:22} state: broken={v('broken'):4} reference={v('reference'):4} bad={v('bad'):4} | "
              f"invariants: broken={i('broken')} reference={i('reference')} bad={i('bad')}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS / f"git_state_checks_{stamp}"
    out.with_suffix(".json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    fmt = lambda x: {True: "PASS", False: "FAIL", None: "n/a (rubric)"}[x]
    lines = ["# State checks: broken repo, reference fix, beginner mistake", "",
             "Repo-state scenarios (a repo check decides):", "",
             "| scenario | broken repo | reference fix | beginner mistake | which mistake | failed checks on the mistake |",
             "|---|---|---|---|---|---|"]
    for r in [r for r in rows if r["grading"] == "state"]:
        failed = "; ".join(c["check"] for c in r["bad"]["checks"] if not c["ok"])
        blocked = [s["result"] for s in r["bad"]["steps"] if s["result"] != "ok"]
        lines.append(f"| {r['id']} | {fmt(r['broken']['passed'])} | {fmt(r['reference']['passed'])} | "
                     f"{fmt(r['bad']['passed'])} | {r['bad_label']} | {failed or '-'}{' / ' + blocked[0][:80] if blocked else ''} |")
    state_rows = [r for r in rows if r["grading"] == "state"]
    lines += ["", f"Repo state correct after the reference fix: {sum(r['reference']['passed'] for r in state_rows)}/{len(state_rows)}; "
              f"after the beginner mistake: {sum(r['bad']['passed'] for r in state_rows)}/{len(state_rows)}", "",
              "Rubric scenarios (graded on the answer, see rubrics/; not counted in the repo-state rate):", ""]
    for r in [r for r in rows if r["grading"] == "rubric"]:
        lines.append(f"- {r['id']}: no repo check. Credential invariant: broken repo "
                     f"{'ok' if r['broken']['invariants']['passed'] else 'BROKEN'}, after the mistake "
                     f"({r['bad_label']}) {'ok' if r['bad']['invariants']['passed'] else 'BROKEN'}.")
    lines += ["", "Invariants, summary (the reference fix must keep all of them):", "",
              "| scenario | broken repo | reference fix | beginner mistake | broken on the mistake |", "|---|---|---|---|---|"]
    count = lambda c: f"{sum(x['ok'] for x in c)}/{len(c)}"
    for r in rows:
        bad_failed = "; ".join(c["check"] for c in r["bad"]["invariants"]["checks"] if not c["ok"])
        lines.append(f"| {r['id']} | {count(r['broken']['invariants']['checks'])} | "
                     f"{count(r['reference']['invariants']['checks'])} | {count(r['bad']['invariants']['checks'])} | "
                     f"{bad_failed or '-'} |")
    lines += ["", "Invariants, every check:", "", "| scenario | variant | invariant | ok | detail |", "|---|---|---|---|---|"]
    for r in rows:
        for var in ("broken", "reference", "bad"):
            for c in r[var]["invariants"]["checks"]:
                lines.append(f"| {r['id']} | {var} | {c['check']} | {'yes' if c['ok'] else 'NO'} | {c['detail'] or '-'} |")
    nb = sum(r[v]["dangerous_without_backup"] for r in rows for v in ("reference", "bad"))
    lines += ["", f"Dangerous steps executed without a backup: {nb}"]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\ndangerous steps executed without a backup: {nb}\nwrote {out}.md / .json")


if __name__ == "__main__":
    main()
