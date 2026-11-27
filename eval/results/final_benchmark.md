# Averaged benchmark runs

## Knowledge benchmark (correct answers; mean and min-max over runs)

| setting | answer model | judge | runs | lookup | exact_match | multi_hop | all | retrieval hit | wrong delivered | earlier (Mistral) |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline+v2_check | groq/openai/gpt-oss-20b | groq | 1 | 100% | 91% | 62% | 87% | 97% | 2.0/31 | 77% |

The 'earlier' column is from mistral (2026-11-23/24 runs): only comparable for the same answer model.

## Git assistant end to end (mean and min-max over runs)

**oracle**: answer model none (oracle), judge none, 1 run file(s)

| metric | tuned | held-out |
|---|---|---|
| passed | 100% | 100% |
| diagnosis_correct | n/a | n/a |
| repo_state_correct | 100% | 100% |
| invariants_all_kept | 100% | 100% |
| rubric_items_ok | 100% | 100% |
| risk_tier_agreement | 97% | 94% |
| dangerous_without_backup (mean per run file) | 0 | 0 |
| risk_under_rated_steps (mean per run file) | 1 | 1 |
| usd_estimate_total (mean per run file) | 0 | 0 |

