## Overnight work: safety-first Git assistant, phases 1 to 8

**What this branch adds**
- **Invariants** (Phase 1): 8 checks after every scenario run: no data loss, no leaked
  secret, no force-push or `reset --hard` without approval, `git fsck`, and the expected
  branch and working tree.
- **25 broken-repo scenarios** (Phase 2): 19 tuned and 6 held out. Each has a state
  check, a rubric and a deterministic fix check. No eval data goes into the knowledge base.
- **End-to-end harness** (Phase 3): diagnose, propose, approve, execute, then the
  invariants, grader and judge. It resumes after rate limits.
- **Security hardening** (Phase 4): command and option injection, path traversal, hooks,
  unapproved high-risk commands, and secret redaction. Holes found were fixed and tested.
- **Query decomposition** (Phase 5): an optional `--decompose` flag.
- **`git_docs` collection** (Phase 6): Pro Git, git docs and GitHub docs, embedded with fastembed.
- **Graph viewer** (Phase 7): error → cause → command → concept → doc chains.
- **Approval page**: a local page where you approve a step with its code. The code never
  reaches the model.
- **Beginner rubric**: 5 plain-language items, reported next to pass/fail.

**Honest results so far** (answer model Groq gpt-oss-20b, judge gpt-oss-120b)
- **Tests on Windows:** 272 passed, 2 failed, 1 skipped. The 2 failures are real
  `git_docs` retrieval misses (stash and reflog questions). The tests were kept strict.
- **Oracle e2e:** 25/25 PASS.
- **Knowledge eval, run A** (v2 + checker): 87% correct (31 questions), with 2 wrong
  answers delivered. The earlier 77% baseline used Mistral, so the two aren't directly
  comparable.
- **Not finished, because the Groq free-tier daily token cap ran out:**
  - run B (decomposition)
  - the real assistant e2e run on the 25 scenarios, and the beginner score that comes from it
  - the averaged benchmark

  These numbers will be added here when the runs finish.

**Not in this PR:** no merge to main yet, and no playbook in the knowledge base.
