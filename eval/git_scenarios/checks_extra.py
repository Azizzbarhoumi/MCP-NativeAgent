"""State checks for the Phase 2 scenarios (extra.py and heldout.py). Same contract as
checks.py: look only at the repos after the fix, return [(check name, ok, detail)].
"""

from pathlib import Path

from eval.git_scenarios.extra import ci_job, fake_token
from eval.git_scenarios.scenarios import run


def g(cwd: Path, *args: str) -> str:
    return run(cwd, *args, check=False).stdout.strip()


def ok(cwd: Path, *args: str) -> bool:
    return run(cwd, *args, check=False).returncode == 0


def subjects(cwd: Path, rev: str) -> list[str]:
    return g(cwd, "log", "--format=%s", rev).splitlines()


def branch(work: Path) -> str:
    return g(work, "symbolic-ref", "--quiet", "--short", "HEAD") or "(detached)"


def has_file(cwd: Path, rev: str, path: str) -> bool:
    return ok(cwd, "cat-file", "-e", f"{rev}:{path}")


def in_history(cwd: Path, path: str, *revs: str) -> bool:
    return bool(g(cwd, "log", *(revs or ("--all",)), "--format=%h", "--", path))


def ignored(work: Path, path: str) -> bool:
    return ok(work, "check-ignore", "-q", "--no-index", path)


def clean(work: Path) -> bool:
    return g(work, "status", "--porcelain") == ""


def no_rebase(work: Path) -> bool:
    return not (work / ".git" / "rebase-merge").exists() and not (work / ".git" / "rebase-apply").exists()


def no_markers(text: str) -> bool:
    return "<<<<<<<" not in text and ">>>>>>>" not in text


def kept_remote_branch(root: Path, snap: dict, remote: str, name: str) -> tuple[str, bool, str]:
    before = snap["inv"]["repos"][remote]["branches"].get(name)
    now = g(root / remote, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
    good = bool(before and now and ok(root / remote, "merge-base", "--is-ancestor", before, now))
    return (f"shared {name} history kept (fixed forward, no force-push)", good, "")


def uncommitted_on_wrong_branch(root, snap):
    work = root / "work"
    app = (work / "app.py").read_text(encoding="utf-8") if (work / "app.py").exists() else ""
    return [("on feature now", branch(work) == "feature", branch(work)),
            ("the app.py edit came along to feature",
             "login form" in app or "login form" in g(work, "show", "feature:app.py"), ""),
            ("login.py still exists", (work / "login.py").exists() or has_file(work, "feature", "login.py"), ""),
            ("main unchanged (nothing committed there)", g(work, "rev-parse", "main") == snap["head"], ""),
            ("no stash left holding the work", g(work, "stash", "list") == "", g(work, "stash", "list"))]


def big_files_staged(root, snap):
    work = root / "work"
    app = (work / "app.py").read_text(encoding="utf-8")
    return [("node_modules and build are not staged or committed",
             g(work, "ls-files", "node_modules", "build") == "" and not in_history(work, "node_modules")
             and not in_history(work, "build"), g(work, "ls-files", "build")),
            ("the files are still on disk", (work / "build" / "app.bin").exists()
             and (work / "node_modules" / "pkg0" / "index.js").exists(), ""),
            (".gitignore covers node_modules and build",
             ignored(work, "node_modules/pkg0/index.js") and ignored(work, "build/app.bin"), ""),
            ("the app.py change is kept", "hello, world" in app, "")]


def big_file_committed(root, snap):
    work, origin = root / "work", root / "origin.git"
    return [("export.py is on the shared main", has_file(origin, "main", "export.py"), ""),
            ("no commit on the remote contains data/dump.sql", not in_history(origin, "data/dump.sql"), ""),
            ("no commit on local main contains it (the next push would fail again)",
             not in_history(work, "data/dump.sql", "main"), ""),
            ("data/dump.sql is still on disk", (work / "data" / "dump.sql").exists(), ""),
            (".gitignore covers data/dump.sql", ignored(work, "data/dump.sql"), "")]


def rebase_conflict(root, snap):
    work = root / "work"
    app = (work / "app.py").read_text(encoding="utf-8")
    return [("no rebase in progress", no_rebase(work), ""),
            ("on feature", branch(work) == "feature", branch(work)),
            ("feature's work is still on feature", has_file(work, "feature", "docs.md")
             and "Add docs" in subjects(work, "feature"), ""),
            ("no conflict markers in app.py", no_markers(app), ""),
            ("working tree clean", clean(work), g(work, "status", "--short"))]


def rebase_dropped_commit(root, snap):
    work = root / "work"
    return [("on feature", branch(work) == "feature", branch(work)),
            ("the dropped validation work is back on feature", has_file(work, "feature", "validate.py"), ""),
            ("the other commits are still there", has_file(work, "feature", "parser.py")
             and has_file(work, "feature", "docs.md"), ""),
            ("working tree clean", clean(work), g(work, "status", "--short"))]


def push_protection_secret(root, snap):
    work, origin, token = root / "work", root / "origin.git", fake_token()
    deploy = (work / "deploy.py").read_text(encoding="utf-8") if (work / "deploy.py").exists() else ""
    return [("no commit on the remote contains the token", g(origin, "log", "--all", "--format=%h", "-S", token) == "", ""),
            ("no commit on local main contains it", g(work, "log", "main", "--format=%h", "-S", token) == "", ""),
            ("deploy.py is pushed (without the token)", has_file(origin, "main", "deploy.py")
             and token not in g(origin, "show", "main:deploy.py"), ""),
            ("deploy.py still exists locally, token-free", bool(deploy) and token not in deploy, "")]


def _ci(root, snap, remote_branch, job, required_file=None):
    origin = root / "origin.git"
    passed, out = ci_job(origin, remote_branch, job)
    checks = [(f"CI job '{job}' passes on the pushed {remote_branch}", passed, "" if passed else out[-200:]),
              kept_remote_branch(root, snap, "origin.git", remote_branch)]
    if required_file:
        checks.append((f"{required_file} is committed and pushed", has_file(origin, remote_branch, required_file), ""))
    return checks


def ci_test_failure(root, snap):
    work = root / "work"
    return _ci(root, snap, "feature", "test") + [
        ("local feature is pushed (PR shows the fix)",
         g(work, "rev-parse", "feature") == g(root / "origin.git", "rev-parse", "feature"), "")]


def ci_missing_file(root, snap):
    return _ci(root, snap, "main", "test", "helpers.py")


def ci_conflict_markers(root, snap):
    origin = root / "origin.git"
    return _ci(root, snap, "main", "lint") + [
        ("no conflict markers on the shared main", no_markers(g(origin, "show", "main:app.py")), "")]


def ho_wrong_branch_named(root, snap):
    work = root / "work"
    return [("the fix is on bugfix/login", has_file(work, "bugfix/login", "login.py"), ""),
            ("develop no longer has it", "Fix login redirect" not in subjects(work, "develop")
             and not has_file(work, "develop", "login.py"), ""),
            ("working tree clean", clean(work), "")]


def ho_merge_conflict_two_files(root, snap):
    work = root / "work"
    texts = [(work / f).read_text(encoding="utf-8") for f in ("app.py", "settings.py")]
    merged = ok(work, "merge-base", "--is-ancestor", "redesign", "HEAD")
    aborted = g(work, "rev-parse", "HEAD") == snap["head"] and not merged
    return [("no merge in progress", not (work / ".git" / "MERGE_HEAD").exists(), ""),
            ("no conflict markers in app.py or settings.py", all(no_markers(t) for t in texts), ""),
            ("working tree clean", clean(work), g(work, "status", "--short")),
            ("merge finished or cleanly aborted", merged or aborted, "merged" if merged else "aborted" if aborted else "neither")]


def ho_detached_two_commits(root, snap):
    work = root / "work"
    main = subjects(work, "main")
    return [("both detached commits are saved on a branch",
             all(g(work, "log", "--branches", "--format=%s", "--fixed-strings", f"--grep=Experiment {n}") for n in ("1", "2")), ""),
            ("both are merged back into main", "Experiment 1" in main and "Experiment 2" in main, "")]


def ho_push_rejected_upstream(root, snap):
    up = root / "upstream.git"
    return [kept_remote_branch(root, snap, "upstream.git", "develop"),
            ("your commit is on the shared develop", "Friendlier greeting" in subjects(up, "develop"), "")]


def ho_bad_commit_unpushed_feature(root, snap):
    work = root / "work"
    code = g(work, "show", "search:search.py")
    return [("search works again on the search branch", "return x in items" in code, code[:60]),
            ("'Add search' is kept", "Add search" in subjects(work, "search"), ""),
            ("working tree clean", clean(work), "")]


def ho_pushed_secret(root, snap):
    work, origin = root / "work", root / "origin.git"
    gi = g(origin, "show", "main:.gitignore")
    return [(".env is no longer tracked on the shared main", not has_file(origin, "main", ".env"), ""),
            (".gitignore on the shared main covers .env", ".env" in gi.split() or "/.env" in gi.split(), ""),
            (".env still exists on disk", (work / ".env").exists(), ""),
            ("local main does not track .env", not has_file(work, "main", ".env"), "")]


CHECKS_EXTRA = {f.__name__: f for f in [
    uncommitted_on_wrong_branch, big_files_staged, big_file_committed, rebase_conflict, rebase_dropped_commit,
    push_protection_secret, ci_test_failure, ci_missing_file, ci_conflict_markers, ho_wrong_branch_named,
    ho_merge_conflict_two_files, ho_detached_two_commits, ho_push_rejected_upstream,
    ho_bad_commit_unpushed_feature, ho_pushed_secret]}
