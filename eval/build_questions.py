"""Build eval/knowledge_questions.yaml from hand-written candidates, verifying every
evidence_quote against the actual ingested chunks (same adapter + cleaning + chunking as
ingestion). Fills in the exact section title of the chunk that contains each quote.
A question whose quote is not found is reported and NOT written."""

import re
import sys
from pathlib import Path

import yaml

from knowledge.config import load_config
from knowledge.ingest.chunk import chunk_document
from knowledge.ingest.parse import clean
from knowledge.sources.local_docs import all_adapters

GA = "github_actions_docs/content/actions/"
CANDIDATES = [
    # ---------------- lookup: answer inside one chunk ----------------
    ("lookup", "What is the default timeout for a GitHub Actions job if timeout-minutes is not set?",
     "360 minutes.", [(GA + "reference/workflows-and-actions/workflow-syntax.md",
      "The maximum number of minutes to let a job run before GitHub automatically cancels it. Default: 360")]),
    ("lookup", "How many jobs can a matrix generate at most per workflow run?",
     "256 jobs per workflow run (GitHub-hosted and self-hosted).", [(GA + "reference/workflows-and-actions/workflow-syntax.md",
      "A matrix will generate a maximum of 256 jobs per workflow run.")]),
    ("lookup", "After how many days without access does GitHub evict a cache entry, and what is the default cache size limit per repository?",
     "Entries not accessed in over 7 days are removed; the default limit is 10 GB per repository.",
     [(GA + "reference/workflows-and-actions/dependency-caching.md",
       "GitHub will remove any cache entries that have not been accessed in over 7 days."),
      (GA + "reference/workflows-and-actions/dependency-caching.md", "By default, the limit is 10 GB per repository")]),
    ("lookup", "What is the default pytest fixture scope and when is such a fixture destroyed?",
     "function scope; the fixture is destroyed at the end of the test.",
     [("pytest_docs/doc/en/how-to/fixtures.rst",
       "`function`: the default scope, the fixture is destroyed at the end of the test.")]),
    ("lookup", "Where should a pytest fixture be placed so tests in multiple test modules of a directory can use it?",
     "In a conftest.py file in that directory.",
     [("pytest_docs/doc/en/how-to/fixtures.rst",
       "The next example puts the fixture function into a separate `conftest.py` file")]),
    ("lookup", "What does setting strict=True on pytest.mark.xfail change?",
     "XPASS (unexpectedly passing) results from that test make the test suite fail.",
     [("pytest_docs/doc/en/how-to/skipping.rst",
       "This will make `XPASS` (\"unexpectedly passing\") results from this test to fail the test suite.")]),
    ("lookup", "What is the default delay before a Celery task is retried?",
     "3 minutes (Task.default_retry_delay); it can be overridden with countdown.",
     [("celery_docs/docs/userguide/tasks.rst", "attribute. By default this is set to 3 minutes."),
      ("celery_docs/docs/userguide/tasks.rst", "You can also provide the `countdown` argument to")]),
    ("lookup", "What is the default value of Celery's Task.max_retries and what does None mean?",
     "3 by default; None means the task will retry forever.",
     [("celery_docs/docs/userguide/tasks.rst",
       "A value of `None` means task will retry forever. By default, this option is set to `3`.")]),
    ("lookup", "What do you need to install before using FastAPI's TestClient?",
     "httpx.", [("fastapi_docs/docs/en/docs/tutorial/testing.md", "To use `TestClient`, first install [`httpx`]")]),
    ("lookup", "How do you replace a dependency with a fake one when testing a FastAPI app?",
     "Use app.dependency_overrides: a dict with the original dependency as key and the override function as value.",
     [("fastapi_docs/docs/en/docs/advanced/testing-dependencies.md",
       "To override a dependency for testing, you put as a key the original dependency (a function), and as the value, your dependency override (another function).")]),
    ("lookup", "How do you make a Docker build skip unnecessary files in the build context?",
     "Create a .dockerignore file in the root of the build context.",
     [("docker_docs/content/manuals/build/cache/optimize.md",
       "to create a `.dockerignore` file in the root of your build context.")]),
    ("lookup", "Can the retention-days of an uploaded artifact exceed the repository's retention limit?",
     "No, retention-days cannot exceed the limit set by the repository, organization, or enterprise.",
     [(GA + "tutorials/store-and-share-data.md",
       "The `retention-days` value cannot exceed the retention limit set by the repository, organization, or enterprise.")]),
    # ---------------- exact_match: names, flags, codes ----------------
    ("exact_match", "What does pytest exit code 4 mean?",
     "pytest command line usage error, including a plugin that cannot be found or a conftest.py that fails to import.",
     [("pytest_docs/doc/en/reference/exit-codes.rst",
       "Exit code 4: pytest command line usage error, including a plugin that cannot be found or a `conftest.py` that fails to import")]),
    ("exact_match", "Which secret or variable must be set to true to enable step debug logging in GitHub Actions?",
     "ACTIONS_STEP_DEBUG set to true.",
     [(GA + "how-tos/monitor-workflows/enable-debug-logging.md",
       "To enable step debug logging, set the following secret or variable in the repository that contains the workflow: `ACTIONS_STEP_DEBUG` to `true`.")]),
    ("exact_match", "Which secret or variable enables runner diagnostic logging in GitHub Actions?",
     "ACTIONS_RUNNER_DEBUG set to true.",
     [(GA + "how-tos/monitor-workflows/enable-debug-logging.md",
       "To enable runner diagnostic logging, set the following secret or variable in the repository that contains the workflow: `ACTIONS_RUNNER_DEBUG` to `true`.")]),
    ("exact_match", "What exit code does `ruff check` return when violations were found?",
     "1.", [("ruff_docs/docs/linter.md", "- `1` if violations were found.")]),
    ("exact_match", "What exit code does `ruff format --check` return if one or more files would be reformatted?",
     "1.", [("ruff_docs/docs/formatter.md",
             "- `1` if Ruff terminates successfully, and one or more files would be formatted if `--check` were")]),
    ("exact_match", "Which flag makes Ruff apply unsafe fixes too?",
     "--unsafe-fixes together with --fix (or the unsafe-fixes setting).",
     [("ruff_docs/docs/linter.md", "pass the `--unsafe-fixes` flag alongside `--fix`")]),
    ("exact_match", "How do you suppress Ruff rule F841 on a single line?",
     "Add `# noqa: F841` at the end of the line.",
     [("ruff_docs/docs/linter.md", "x = 1  # noqa: F841")]),
    ("exact_match", "Which pytest command stops the run after two failures?",
     "pytest --maxfail=2", [("pytest_docs/doc/en/how-to/failures.rst", "pytest --maxfail=2  # stop after two failures")]),
    ("exact_match", "Which pytest command lists the 10 slowest tests longer than 1 second?",
     "pytest --durations=10 --durations-min=1.0",
     [("pytest_docs/doc/en/how-to/usage.rst", "pytest --durations=10 --durations-min=1.0")]),
    ("exact_match", "Which exception does a Celery task catch to clean up before its hard time limit?",
     "celery.exceptions.SoftTimeLimitExceeded",
     [("celery_docs/docs/userguide/workers.rst", "from celery.exceptions import SoftTimeLimitExceeded")]),
    ("exact_match", "What Dockerfile RUN line caches pip downloads between builds?",
     "RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt",
     [("docker_docs/content/manuals/build/cache/optimize.md", "RUN --mount=type=cache,target=/root/.cache/pip")]),
    # ---------------- multi_hop: two+ documents, ideally different sources ----------------
    ("multi_hop", "A CI job runs pytest but finds no test files. What exit code does pytest return, and which workflow step setting lets the job still pass when that step fails?",
     "Exit code 5 (no tests were collected); set continue-on-error: true on the step.",
     [("pytest_docs/doc/en/reference/exit-codes.rst", "Exit code 5: No tests were collected"),
      (GA + "reference/workflows-and-actions/workflow-syntax.md",
       "Prevents a job from failing when a step fails. Set to `true` to allow a job to pass when this step fails.")]),
    ("multi_hop", "A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?",
     "GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.",
     [(GA + "reference/limits.md", "Each job in a workflow can run for up to 6 hours of execution time. If a job reaches this limit, the job is terminated and fails."),
      (GA + "reference/workflows-and-actions/workflow-syntax.md",
       "The maximum number of minutes to let a job run before GitHub automatically cancels it. Default: 360")]),
    ("multi_hop", "In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?",
     "ruff check exits 1 when violations are found; set continue-on-error: true on the step.",
     [("ruff_docs/docs/linter.md", "- `1` if violations were found."),
      (GA + "reference/workflows-and-actions/workflow-syntax.md",
       "Prevents a job from failing when a step fails. Set to `true` to allow a job to pass when this step fails.")]),
    ("multi_hop", "FastAPI docs suggest a bigger tool for heavy background work. Which tool, and what Celery settings make a task acknowledged only after it runs with prefetching of one task?",
     "Celery; task_acks_late = True and worker_prefetch_multiplier = 1.",
     [("fastapi_docs/docs/en/docs/tutorial/background-tasks.md",
       "you might benefit from using other bigger tools like [Celery]"),
      ("celery_docs/docs/userguide/optimizing.rst", "task_acks_late = True")]),
    ("multi_hop", "To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?",
     "Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.",
     [("docker_docs/content/manuals/build/cache/optimize.md", "RUN --mount=type=cache,target=/root/.cache/pip"),
      (GA + "reference/workflows-and-actions/dependency-caching.md",
       "GitHub will remove any cache entries that have not been accessed in over 7 days.")]),
    ("multi_hop", "A CI run fails because conftest.py cannot be imported. What pytest exit code does that produce, and which command-line flag controls how pytest imports test modules and conftest files?",
     "Exit code 4; the --import-mode flag (default prepend).",
     [("pytest_docs/doc/en/reference/exit-codes.rst",
       "Exit code 4: pytest command line usage error, including a plugin that cannot be found or a `conftest.py` that fails to import"),
      ("pytest_docs/doc/en/explanation/pythonpath.rst", "import process can be controlled through the --import-mode command-line flag")]),
    ("multi_hop", "A Celery task fails on a flaky service in CI. Which argument to self.retry() makes it wait 60 seconds before retrying, and how many retries does a task get by default?",
     "countdown=60, e.g. raise self.retry(exc=exc, countdown=60); the default max_retries is 3.",
     [("celery_docs/docs/userguide/tasks.rst", "raise self.retry(exc=exc, countdown=60)"),
      ("celery_docs/docs/userguide/tasks.rst",
       "A value of `None` means task will retry forever. By default, this option is set to `3`.")]),
    ("multi_hop", "Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?",
     "Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.",
     [("fastapi_docs/docs/en/docs/tutorial/testing.md", "To use `TestClient`, first install [`httpx`]"),
      (GA + "how-tos/monitor-workflows/enable-debug-logging.md",
       "To enable step debug logging, set the following secret or variable in the repository that contains the workflow: `ACTIONS_STEP_DEBUG` to `true`.")]),
]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def main():
    cfg = load_config()
    ch = cfg.chunking
    chunks_by_doc = {}
    for ad in all_adapters():
        for d in ad.documents(cfg.collection):
            d.text = clean(d.text, ch.max_code_lines)
            chunks_by_doc[f"{d.source}/{d.path}"] = chunk_document(d, ch.max_chars, ch.overlap_chars, ch.min_chars)

    out, problems, counts = [], [], {}
    for qtype, question, expected, docs in CANDIDATES:
        counts[qtype] = counts.get(qtype, 0) + 1
        qid = f"{qtype[:2]}{counts[qtype]:02d}"
        source_docs, quotes, ok = [], [], True
        for path, quote in docs:
            hit = next((c for c in chunks_by_doc.get(path, []) if norm(quote) in norm(c.text)), None)
            if hit is None:
                problems.append(f"{qid}: quote not found in {path}: {quote[:70]}")
                ok = False
                continue
            source_docs.append({"path": path, "section": hit.section})
            quotes.append(quote)
        if ok:
            out.append({"id": qid, "type": qtype, "question": question, "expected_answer": expected,
                        "source_docs": source_docs,
                        "evidence_quote": quotes[0] if len(quotes) == 1 else quotes,
                        "reviewed": True, "reviewed_by": "claude (quote-checked; not yet human-verified)"})
    Path("eval/knowledge_questions.yaml").write_text(
        "# Questions reviewed by Claude at the owner's request (quotes machine-verified). Set reviewed: false to exclude one.\n"
        + yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
    print(f"written {len(out)} questions; problems: {len(problems)}")
    for p in problems:
        print("  ", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
