# Re-judged: knowledge_baseline_v2_20261123_011145.json

| baseline+v2 (judge groq) | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 92% | 8% | 28.1 | 1535 |
| exact_match | 11 | 100% | 100% | 91% | 9% | 30.5 | 1558 |
| multi_hop | 8 | 88% | 38% | 25% | 62% | 30.4 | 1639 |
| all | 31 | 97% | 84% | 74% | 23% | 29.6 | 1570 |

**Judge agreement mistral vs groq: 81%** (6 of 31 verdicts changed)

- **lo07**: mistral said correct, groq says partial: Provides the default delay (3 minutes) but omits the reference fact that it can be overridden with countdown.
- **ex07**: mistral said partial, groq says correct: Includes the required '# noqa: F841' inline comment and adds no contradictory information.
- **mu02**: mistral said partial, groq says incorrect: The answer fails to state the 6‑hour execution limit and the default timeout of 360 minutes, omitting the key facts.
- **mu04**: mistral said partial, groq says correct: Provides Celery and both required settings exactly as in reference.
- **mu05**: mistral said correct, groq says partial: Correctly describes the Dockerfile cache mount, but incorrectly states the GitHub cache eviction timeframe (does not mention the 7‑day rule).
- **mu06**: mistral said correct, groq says partial: Correctly states exit code 4 but fails to mention the --import-mode flag, contradicting the reference.
