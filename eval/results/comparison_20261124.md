# Knowledge eval comparison (all graded by Groq gpt-oss-120b), 2026-11-24

**correct**

| type | baseline+v1 (judge groq) | baseline+v2 (judge groq) | hybrid+v2 (judge groq) |
|---|---|---|---|
| lookup | 58% | 92% | 83% |
| exact_match | 18% | 91% | 73% |
| multi_hop | 0% | 25% | 50% |
| all | 29% | 74% | 71% |

**retrieval hit**

| type | baseline+v1 (judge groq) | baseline+v2 (judge groq) | hybrid+v2 (judge groq) |
|---|---|---|---|
| lookup | 100% | 100% | 100% |
| exact_match | 100% | 100% | 100% |
| multi_hop | 88% | 88% | 88% |
| all | 97% | 97% | 97% |

**full coverage**

| type | baseline+v1 (judge groq) | baseline+v2 (judge groq) | hybrid+v2 (judge groq) |
|---|---|---|---|
| lookup | 100% | 100% | 100% |
| exact_match | 100% | 100% | 100% |
| multi_hop | 38% | 38% | 50% |
| all | 84% | 84% | 87% |

**Changed questions (baseline+v1 (judge groq) -> hybrid+v2 (judge groq))**

- **ex01** (exact_match): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **ex04** (exact_match): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **ex05** (exact_match): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **ex07** (exact_match): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **ex08** (exact_match): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **ex09** (exact_match): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **ex10** (exact_match): incorrect -> partial (fixed), hit True -> True, coverage 1.0 -> 1.0
- **lo02** (lookup): incorrect -> partial (fixed), hit True -> True, coverage 1.0 -> 1.0
- **lo05** (lookup): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **lo09** (lookup): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **lo12** (lookup): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **mu01** (multi_hop): incorrect -> partial (fixed), hit True -> True, coverage 0.5 -> 0.5
- **mu02** (multi_hop): incorrect -> correct (fixed), hit False -> True, coverage 0.0 -> 1.0
- **mu03** (multi_hop): incorrect -> partial (fixed), hit True -> True, coverage 0.5 -> 0.5
- **mu04** (multi_hop): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **mu05** (multi_hop): incorrect -> incorrect (same verdict), hit True -> False, coverage 0.5 -> 0.0
- **mu06** (multi_hop): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **mu07** (multi_hop): incorrect -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
- **mu08** (multi_hop): incorrect -> partial (fixed), hit True -> True, coverage 0.5 -> 0.5

## 4b (baseline+v2) -> hybrid+v2
**Changed questions (baseline+v2 (judge groq) -> hybrid+v2 (judge groq))**

- **ex10** (exact_match): correct -> partial (broke), hit True -> True, coverage 1.0 -> 1.0
- **ex11** (exact_match): correct -> incorrect (broke), hit True -> True, coverage 1.0 -> 1.0
- **lo02** (lookup): correct -> partial (broke), hit True -> True, coverage 1.0 -> 1.0
- **mu02** (multi_hop): incorrect -> correct (fixed), hit False -> True, coverage 0.0 -> 1.0
- **mu05** (multi_hop): partial -> incorrect (broke), hit True -> False, coverage 0.5 -> 0.0
- **mu06** (multi_hop): partial -> correct (fixed), hit True -> True, coverage 1.0 -> 1.0
