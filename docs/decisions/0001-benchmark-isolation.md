# 0001: Benchmark data is never indexed

**Context.** The benchmark is 30-40 real failed GitHub Actions runs; the commit that fixed each
run is the answer key. If any of that data (logs, fixing commits, PRs) reached the knowledge base,
retrieval could "find" the answer and the scores would be meaningless (data leakage).

**Decision.** Benchmark data lives only in `eval/ci_runs/`. The ingestion code refuses any path
inside `eval/` (a hard check, not a convention), and the corpus manifest states the rule.

**Consequence.** The agent must diagnose each failure from the run itself plus general docs,
exactly as it would on a new, unseen failure.
