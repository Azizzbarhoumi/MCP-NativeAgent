"""State checks: look at a scenario's repos AFTER a fix ran and decide pass/fail.

A plan can read well and still leave the repo broken. These checks look only at the
result. `snapshot` records facts right after the scenario is built (e.g. what the shared
remote had), so a check can tell "fixed" from "fixed by destroying a teammate's work".
"""

from pathlib import Path

from eval.git_scenarios import invariants as inv
from eval.git_scenarios.scenarios import run


def g(cwd: Path, *args: str) -> str:
    return run(cwd, *args, check=False).stdout.strip()


def subjects(cwd: Path, rev: str) -> list[str]:
    return g(cwd, "log", "--format=%s", rev).splitlines()


def is_ancestor(cwd: Path, a: str, b: str) -> bool:
    return run(cwd, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def snapshot(root: Path, work: str = "work") -> dict:
    """Facts about the broken repo, taken before any fix runs: what the scenario checks need
    (HEAD, the shared main) plus everything the invariants compare against."""
    snap = {"head": g(root / work, "rev-parse", "HEAD"), "inv": inv.snapshot(root, work)}
    if (root / "origin.git").exists():
        snap["origin_main"] = g(root / "origin.git", "rev-parse", "main")
    return snap


def _no_conflict_left(work: Path) -> list[tuple[str, bool, str]]:
    text = (work / "app.py").read_text(encoding="utf-8")
    return [("no merge in progress", not (work / ".git" / "MERGE_HEAD").exists(), ""),
            ("no conflict markers in app.py", "<<<<<<<" not in text and ">>>>>>>" not in text, ""),
            ("working tree clean", g(work, "status", "--porcelain") == "", g(work, "status", "--short"))]


def merge_conflict(root, snap):
    work = root / "work"
    merged = is_ancestor(work, "feature", "HEAD")
    aborted = g(work, "rev-parse", "HEAD") == snap["head"] and not merged
    return _no_conflict_left(work) + [("merge finished or cleanly aborted", merged or aborted,
                                       "merged" if merged else "aborted" if aborted else "neither")]


def push_rejected(root, snap):
    origin = root / "origin.git"
    return [("teammate's commit still on the shared main (no force-push)",
             is_ancestor(origin, snap["origin_main"], "main"), ""),
            ("your commit 'Shorter greeting' is on the shared main",
             "Shorter greeting" in subjects(origin, "main"), "")]


def detached_head(root, snap):
    work = root / "work"
    on_main = "Add notes (made while detached)" in subjects(work, "main")
    on_any = bool(g(work, "log", "--branches", "--format=%s", "--grep=Add notes (made while detached)", "--fixed-strings"))
    return [("the detached commit is saved on a branch", on_any, ""),
            ("the saved commit is merged back into main", on_main, "")]


def wrong_branch_commit(root, snap):
    work = root / "work"
    feature_ok = (g(work, "show", "feature:app.py").find("feature work") >= 0)
    main_clean = "Feature work (should be on feature)" not in subjects(work, "main") and \
        g(work, "show", "main:app.py").find("feature work") < 0
    return [("the work is on feature", feature_ok, ""),
            ("main no longer has the commit", main_clean, ""),
            ("working tree clean", g(work, "status", "--porcelain") == "", "")]


def pushed_bad_commit(root, snap):
    origin = root / "origin.git"
    return [("shared history kept (bad commit not removed by force-push)",
             is_ancestor(origin, snap["origin_main"], "main"), ""),
            ("shared main is fixed (typo gone)", "GREETNG" not in g(origin, "show", "main:app.py"), "")]


def committed_secret(root, snap):
    work = root / "work"
    gi = work / ".gitignore"
    return [(".env is no longer tracked", ".env" not in g(work, "ls-files").splitlines(), ""),
            (".env is listed in .gitignore", gi.exists() and ".env" in gi.read_text(encoding="utf-8").split(), ""),
            (".env still exists on disk (your config is not deleted)", (work / ".env").exists(), ""),
            ("no commit on main contains .env (a later push would publish it)",
             g(work, "log", "main", "--format=%h", "--", ".env") == "", g(work, "log", "main", "--oneline", "--", ".env"))]


def no_upstream(root, snap):
    work, origin = root / "work", root / "origin.git"
    return [("feature exists on the remote", g(origin, "rev-parse", "--verify", "--quiet", "feature") == g(work, "rev-parse", "feature"), ""),
            ("feature tracks origin/feature", g(work, "rev-parse", "--abbrev-ref", "feature@{upstream}") == "origin/feature", "")]


CHECKS = {f.__name__: f for f in [merge_conflict, push_rejected, detached_head, wrong_branch_commit,
                                  pushed_bad_commit, committed_secret, no_upstream]}
# auth_https_password has no repo check on purpose: nothing can be fixed locally, so its
# broken repo would "pass". It is graded on the answer (rubrics/) plus the invariants.


def check(scenario_id: str, root: Path, snap: dict) -> dict:
    """Scenario-specific repo check. passed=None for rubric-graded scenarios."""
    if scenario_id not in CHECKS:
        return {"passed": None, "checks": []}
    results = CHECKS[scenario_id](root, snap)
    return {"passed": all(ok for _, ok, _ in results),
            "checks": [{"check": name, "ok": ok, "detail": d} for name, ok, d in results]}


def invariants(root: Path, snap: dict, key: dict | None = None, steps=None, log_rows=None) -> dict:
    """All invariants (see invariants.py), each reported separately."""
    results = inv.check_all(root, snap["inv"], key, steps, log_rows)
    return {"passed": all(ok for _, ok, _ in results),
            "checks": [{"check": name, "ok": ok, "detail": d} for name, ok, d in results]}
