"""Step 0: build knowledge/corpus/ from openly licensed documentation repos.

For each source: shallow + sparse git clone (only the folders we need), pick the most
relevant files, clean them lightly, copy them to knowledge/corpus/<source>/, and record
everything in knowledge/corpus_manifest.yaml (repo, commit, license, folders, file count).

Usage:
  python -m knowledge.acquire                  all doc sources (needs git)
  python -m knowledge.acquire --collection git_docs   only the sources of one collection
  python -m knowledge.acquire --own-repo owner/repo   also fetch that repo's README via GitHub MCP

Selection (why ~60 files per source): we score each file by how many CI/build/test/lint/job
keywords it contains, drop tiny files (<300 bytes) and release notes/changelogs, and keep the
top MAX_FILES. That keeps the corpus small enough to re-ingest in minutes.
"""

import argparse
import fnmatch
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
MANIFEST = ROOT / "corpus_manifest.yaml"
MAX_FILES = 60
MIN_BYTES = 300
DOC_EXTS = (".md", ".rst", ".adoc", ".asc")   # AsciiDoc: git docs (.adoc) and Pro Git (.asc)

KEYWORDS = [
    "workflow", "job", "runner", "step", "cache", "artifact", "matrix", "secret", "error",
    "fail", "build", "dockerfile", "image", "layer", "compose", "test", "fixture", "assert",
    "pytest", "lint", "rule", "config", "task", "worker", "retry", "timeout", "exit code",
    "dependency", "install", "python", "environment", "debug", "troubleshoot", "log",
]

SOURCES = [
    {
        "name": "github_actions_docs",
        "repo": "https://github.com/github/docs.git",
        "license": "CC-BY-4.0 (docs content); code MIT",
        "attribution": "GitHub Docs, (c) GitHub, Inc., licensed CC BY 4.0, https://github.com/github/docs",
        "folders": ["content/actions/"],
        "exclude": ["*/index.md"],  # index pages are mostly link lists
        "url": "https://docs.github.com/en/{path_no_ext}",
        "url_strip": "content/",
    },
    {
        "name": "docker_docs",
        "repo": "https://github.com/docker/docs.git",
        "license": "Apache-2.0",
        "attribution": "Docker Docs, (c) Docker Inc., Apache License 2.0, https://github.com/docker/docs",
        "folders": ["content/manuals/build/", "content/reference/dockerfile.md", "content/manuals/compose/"],
        "exclude": ["*release-notes*", "*/_index.md"],
        "url": "https://docs.docker.com/{path_no_ext}/",
        "url_strip": "content/manuals/",
    },
    {
        "name": "fastapi_docs",
        "repo": "https://github.com/fastapi/fastapi.git",
        "license": "MIT",
        "attribution": "FastAPI docs, (c) Sebastian Ramirez, MIT, https://github.com/fastapi/fastapi",
        "folders": ["docs/en/docs/"],
        "exclude": ["*release-notes*", "*/management*", "*/help-fastapi*", "*/contributing*"],
        "url": "https://fastapi.tiangolo.com/{path_no_ext}/",
        "url_strip": "docs/en/docs/",
    },
    {
        "name": "ruff_docs",
        "repo": "https://github.com/astral-sh/ruff.git",
        "license": "MIT",
        "attribution": "Ruff docs, (c) Charles Marsh / Astral, MIT, https://github.com/astral-sh/ruff",
        "folders": ["docs/"],
        "exclude": [],
        "url": "https://docs.astral.sh/ruff/{path_no_ext}/",
        "url_strip": "docs/",
    },
    {
        "name": "pytest_docs",
        "repo": "https://github.com/pytest-dev/pytest.git",
        "license": "MIT",
        "attribution": "pytest docs, (c) Holger Krekel and others, MIT, https://github.com/pytest-dev/pytest",
        "folders": ["doc/en/how-to/", "doc/en/reference/", "doc/en/explanation/", "doc/en/getting-started.rst"],
        "exclude": ["*changelog*", "*announce*", "*plugin_list*"],
        "url": "https://docs.pytest.org/en/stable/{path_no_ext}.html",
        "url_strip": "doc/en/",
    },
    {
        "name": "celery_docs",
        "repo": "https://github.com/celery/celery.git",
        "license": "BSD-3-Clause",
        "attribution": "Celery docs, (c) Asif Saif Uddin, Ask Solem & contributors, BSD 3-Clause, https://github.com/celery/celery",
        "folders": ["docs/userguide/"],
        "exclude": [],
        "url": "https://docs.celeryq.dev/en/stable/userguide/{path_no_ext}.html",
        "url_strip": "docs/userguide/",
    },
]

# Keywords for the beginner Git/GitHub assistant: the errors and fixes it must explain.
GIT_KEYWORDS = [
    "push", "pull", "fetch", "merge", "conflict", "rebase", "branch", "commit", "detached",
    "head", "reset", "revert", "restore", "stash", "remote", "upstream", "rejected",
    "non-fast-forward", "force", "authentication", "token", "ssh", "credential", "password",
    "permission", "secret", "sensitive", "undo", "amend", "reflog", "checkout", "switch",
    "pull request", "clone", "tracking", "error", "fatal",
]

# Official git docs: an explicit list, because the folder holds ~600 pages (internals,
# plumbing, mail tools) a beginner assistant never needs.
GIT_PAGES = [
    "user-manual", "gittutorial", "gittutorial-2", "giteveryday", "gitglossary", "gitworkflows",
    "gitrevisions", "gitcredentials", "gitignore", "git-add", "git-commit", "git-status", "git-log",
    "git-diff", "git-branch", "git-checkout", "git-switch", "git-restore", "git-merge",
    "git-mergetool", "git-rebase", "git-reset", "git-revert", "git-cherry-pick", "git-stash",
    "git-fetch", "git-pull", "git-push", "git-remote", "git-clone", "git-init", "git-reflog",
    "git-rm", "git-mv", "git-clean", "git-tag", "git-config", "git-credential", "git-bisect",
    "git-blame", "git-show", "git-rerere", "git-filter-branch", "git-gc", "git-fsck",
]

SOURCES += [
    {
        "name": "git_official_docs",
        "collection": "git_docs",
        "repo": "https://github.com/git/git.git",
        "license": "GPL-2.0",
        "attribution": "Git documentation, (c) the Git contributors, GPL-2.0, https://github.com/git/git",
        "folders": [f"Documentation/{p}.adoc" for p in GIT_PAGES],
        "exclude": [],
        "keywords": GIT_KEYWORDS,
        "url": "https://git-scm.com/docs/{path_no_ext}",
        "url_strip": "Documentation/",
    },
    {
        "name": "github_git_docs",
        "collection": "git_docs",
        "repo": "https://github.com/github/docs.git",
        "license": "CC-BY-4.0 (docs content); code MIT",
        "attribution": "GitHub Docs, (c) GitHub, Inc., licensed CC BY 4.0, https://github.com/github/docs",
        "folders": ["content/get-started/using-git/", "content/get-started/git-basics/",
                    "content/pull-requests/collaborating-with-pull-requests/",
                    "content/authentication/"],
        "exclude": ["*/index.md", "*security-log*", "*sudo-mode*", "*anonymized-urls*",
                    "*ip-addresses*", "*verifying-new-devices*", "*sessions*"],
        "keywords": GIT_KEYWORDS,
        "liquid": True,
        "url": "https://docs.github.com/en/{path_no_ext}",
        "url_strip": "content/",
    },
    {
        "name": "progit_book",
        "collection": "git_docs",
        "repo": "https://github.com/progit/progit2.git",
        "license": "CC-BY-NC-SA-3.0 (NON-COMMERCIAL: remove before any commercial use)",
        "attribution": "Pro Git, 2nd ed., Scott Chacon and Ben Straub, CC BY-NC-SA 3.0, https://github.com/progit/progit2",
        "folders": ["book/01-introduction/", "book/02-git-basics/", "book/03-git-branching/",
                    "book/05-distributed-git/", "book/06-github/", "book/07-git-tools/"],
        "exclude": [],
        "keywords": GIT_KEYWORDS,
        "url": "https://github.com/progit/progit2/blob/main/{path_no_ext}.asc",
        "url_strip": "",
    },
]

# GitHub docs use Liquid templating. Replace the common variables with their text,
# then drop any remaining {% ... %} / {{ ... }} tags.
LIQUID_VARS = {
    "variables.product.prodname_actions": "GitHub Actions",
    "variables.product.github": "GitHub",
    "variables.product.prodname_dotcom": "GitHub",
    "variables.product.prodname_ghe_server": "GitHub Enterprise Server",
    "variables.product.prodname_ghe_cloud": "GitHub Enterprise Cloud",
    "variables.actions.hosted_runner": "GitHub-hosted runner",
    "variables.product.prodname_cli": "GitHub CLI",
    "variables.product.pat_generic": "personal access token",
    "variables.product.prodname_marketplace": "GitHub Marketplace",
    "variables.product.pat_v1": "personal access token (classic)",
    "variables.product.pat_v2": "fine-grained personal access token",
    "variables.product.prodname_github_app": "GitHub App",
    "variables.product.prodname_github_apps": "GitHub Apps",
    "variables.product.prodname_oauth_app": "OAuth app",
    "variables.product.prodname_mobile": "GitHub Mobile",
    "variables.product.prodname_desktop": "GitHub Desktop",
    "variables.product.company_short": "GitHub",
    "variables.product.product_url": "github.com",
    "variables.location.product_location": "GitHub",
}


def strip_liquid(text: str) -> str:
    def var(m):
        return LIQUID_VARS.get(m.group(1).strip(), "")
    text = re.sub(r"\{%-?\s*data\s+([\w.]+)\s*-?%\}", var, text)
    text = re.sub(r"\{%-?.*?-?%\}", "", text, flags=re.S)
    # Keep ${{ ... }}: those are GitHub Actions expressions, not templating.
    return re.sub(r"(?<!\$)\{\{.*?\}\}", "", text, flags=re.S)


def relevance(text: str, keywords: list[str] = KEYWORDS) -> int:
    t = text.lower()
    return sum(t.count(k) for k in keywords)


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def acquire_source(src: dict, workdir: Path) -> dict:
    clone = workdir / src["name"]
    git("clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", src["repo"], str(clone))
    git("sparse-checkout", "set", "--no-cone", *["/" + f for f in src["folders"]], cwd=clone)
    commit = git("rev-parse", "HEAD", cwd=clone)

    candidates = []
    for f in src["folders"]:
        base = clone / f
        if not base.exists():          # a listed page that this git version does not have
            continue
        files = [base] if base.is_file() else [p for ext in DOC_EXTS for p in base.rglob(f"*{ext}")]
        for p in files:
            rel = p.relative_to(clone).as_posix()
            if p.stat().st_size < MIN_BYTES or any(fnmatch.fnmatch(rel, x) for x in src["exclude"]):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            candidates.append((relevance(text, src.get("keywords", KEYWORDS)) / (len(text) ** 0.5), rel, text))
            # divide by sqrt(length) so long pages don't win just by being long

    candidates.sort(reverse=True)
    out = CORPUS / src["name"]
    shutil.rmtree(out, ignore_errors=True)
    for _, rel, text in candidates[:MAX_FILES]:
        if src.get("liquid") or src["name"] == "github_actions_docs":
            text = strip_liquid(text)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    return {
        "name": src["name"], "collection": src.get("collection", "dev_docs"),
        "repo": src["repo"].removesuffix(".git"), "commit": commit,
        "license": src["license"], "attribution": src["attribution"], "folders": src["folders"],
        "files": min(len(candidates), MAX_FILES), "candidates_seen": len(candidates),
        "url_template": src["url"], "url_strip": src["url_strip"],
    }


async def acquire_own_repo(repo: str) -> dict:
    """README only, through the read-only GitHub MCP server (token stays in Docker)."""
    from orchestrator.mcp_client import github_tools
    owner, name = repo.split("/")
    async with github_tools() as tools:
        result = await tools.call("get_file_contents", {"owner": owner, "repo": name, "path": "README.md"})
    text = result if isinstance(result, str) else str(result.get("content", result))
    out = CORPUS / "own_repo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(text, encoding="utf-8")
    return {"name": "own_repo", "repo": f"https://github.com/{repo}", "commit": "default branch at fetch time",
            "license": "owner's own repo", "attribution": f"{repo} (project owner)", "folders": ["README.md"],
            "files": 1, "url_template": f"https://github.com/{repo}/blob/HEAD/{{path_no_ext}}.md", "url_strip": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--own-repo")
    ap.add_argument("--skip-docs", action="store_true")
    ap.add_argument("--collection", help="only fetch the sources of this collection, e.g. git_docs")
    a = ap.parse_args()

    manifest = yaml.safe_load(MANIFEST.read_text()) if MANIFEST.exists() else {}
    sources = {s["name"]: s for s in manifest.get("sources", [])}
    if not a.skip_docs:
        with tempfile.TemporaryDirectory() as tmp:
            for src in SOURCES:
                if a.collection and src.get("collection", "dev_docs") != a.collection:
                    continue
                print(f"fetching {src['name']} ...")
                sources[src["name"]] = acquire_source(src, Path(tmp))
    if a.own_repo:
        import asyncio
        sources["own_repo"] = asyncio.run(acquire_own_repo(a.own_repo))

    manifest = {
        "note": ("Raw documents are NOT committed (knowledge/corpus/ is git-ignored). Each source keeps "
                 "its original license; attribution below. Benchmark data in eval/ci_runs/ is never "
                 "part of this corpus (see docs/decisions/0001-benchmark-isolation.md)."),
        "sources": list(sources.values()),
    }
    MANIFEST.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    for s in manifest["sources"]:
        print(f"{s.get('collection', 'dev_docs'):9} {s['name']:22} {s['files']:3} files  {s['commit'][:12]}  {s['license']}")


if __name__ == "__main__":
    main()
