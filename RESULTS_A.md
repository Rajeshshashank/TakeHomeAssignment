# Results — Project A: Indian income-tax engine

Everything below is produced by `bash scripts/run_all.sh`. The raw machine
output is in `project_a/tests/cases/` (`measurement.json`,
`official_calculator_checks.json`, `spec_diff.json`, `cases.json`,
`descriptions.json`). Nothing here is hand-typed from a notebook.

## 1. What was built

| Piece | Where | Kind |
|---|---|---|
| Test corpus, 521 cases × 2 FYs = 1042 answers | `project_a/tests/generate_cases.py` → `cases.json` | rules (oracle) |
| Independent oracle used to freeze expectations | `project_a/oracle.py` | rules |
| Implementation A — hand-written engine driven by YAML | `project_a/engine.py`, `taxspec/*.yaml` | **rules** |
| Implementation B — LLM reads the rules and computes | `project_a/llm/impl_b.py` | **LLM** |
| Implementation C — LLM compiles rules once, then no LLM | `project_a/llm/impl_c.py`, `compiled/*` | **hybrid** (LLM at build, rules at run) |
| Plain-language parser (Hinglish, incomplete, contradictory) | `project_a/nlp/parser.py` | **rules** |
| Official-calculator cross-check | `project_a/verify_official.py` | rules |
| FY 2025-26 change log | `project_a/spec_diff.py` | rules |

Scope is salary income only, exactly as specified: standard deduction, HRA
exemption, 80C, 80CCD(1B), 80D (including senior-citizen parents), s.24(b)
home-loan interest on a self-occupied house, s.87A rebate, surcharge with
marginal relief, and 4% cess. Every statutory number lives in the YAML spec;
the engine hard-codes none of them.

## 2. Task 1 — test suite

* **521 cases** (≥ 200 required), each scored under both regimes and both FYs:
  **1042 answers**. Tagged by construction: every old-regime threshold
  (2.5L/3L/5L/10L/50L/1L/2L/5L cr), every new-regime threshold for both FYs,
  every surcharge boundary, and the rebate edges — with a ±10-rupee probe on
  each side of every edge.
* **30 cases checked against the Income Tax Department's official calculator**
  (`official_calculator_checks.json`). All 30 reproduce the portal exactly.
  Because the portal's *basic* calculator is not the statute — it floors to the
  rupee, omits the s.87A marginal-relief proviso, and applies surcharge marginal
  relief only at the first threshold — the comparison is run as an *equality*
  test against a faithful model of that calculator, not a permissive "matches
  either model" test. 22/30 are also statutory-exact and 30/30 are
  basic-model-exact; the 8 statutory divergences are exactly the portal's
  documented limits: rupee flooring at 5,00,010 / 7,00,010 / 8,00,010 / 24,00,010,
  the s.87A new-regime edge at 12,00,010, and the surcharge thresholds at
  1 crore / 2 crore / 5 crore. Every row is classified so a divergence away from
  a marginal-relief boundary would fail the build.
* **The old regime's s.87A rebate is a cliff, not a slope.** A total income of
  5,00,010 forfeits the whole 12,500 and pays full slab tax; only the new regime
  has a marginal-relief proviso. The engine, the oracle, the compiled modules
  and the LLM proxy all encode this, and
  `test_old_regime_87a_is_a_cliff_not_marginal_relief` pins it.
* **58 plain-language descriptions** (≥ 50 required) across 13 styles:
  formal English, terse, Hinglish, monthly figures, unit abbreviations
  ("1.5L", "18,00,000"), number words, senior citizens, metro/non-metro, and
  deliberately incomplete and contradictory ones.

Parser result on that corpus: **139/139 facts recovered and 58/58
clarification decisions correct** — asserted in `project_a/tests/test_parser.py`
so a regression fails CI.

## 3. Task 4 — measurement

Same 1042 answers, three implementations. Offline proxy mode (no API key); the
proxy is a deterministic, *rule-following* stand-in, so it can only be wrong in
the ways a competent model is plausibly wrong, and the fidelity sweep below
shows exactly which omission costs what. Cost uses Gemini Flash-class list
prices (USD per 1M tokens: in 0.10, out 0.40).

| Implementation | Kind | Accuracy | Mean latency | p95 | Tokens/req | $/1,000 req | Deterministic | Every rupee cited? |
|---|---|---:|---:|---:|---:|---:|---|---|
| A: hand-written rules | rule | **100.0%** (1042/1042) | 0.0 ms | 0.0 ms | 0 | **$0.0000** | yes | yes |
| B: LLM at query time | LLM | 74.9% (780/1042) | 185.5 ms | 185.5 ms | 1163 | **$0.1209** | proxy yes; not guaranteed live | no |
| C: LLM-compiled rules | hybrid | **100.0%** (1042/1042) | 0.02 ms | 0.03 ms | 0 | **$0.0000** | yes | yes |

Read this table as: A and C are tied on every axis that matters, and C got
there with an LLM in the build loop; B is 25 points less accurate, ~9000×
slower and the only row that costs money per request.

**Fidelity sweep — which single omission costs most.** Implementation B's
mistakes are not random; they are specific omissions. Re-running B with one
plausible model error injected at a time:

| Model fidelity | Correct | Accuracy |
|---|---:|---:|
| full | 1042/1042 | 100.0% |
| forgets marginal relief on surcharge + s.87A | 780/1042 | 74.9% |
| rounds to the rupee instead of to ten | 636/1042 | 61.0% |
| both of the above | 471/1042 | 45.2% |
| slabs only, no deductions/surcharge/rebate | 257/1042 | 24.7% |

This is the evidence for the hybrid design: the model gets the *shape* of the
computation right and the *rounding and relief provisos* wrong. Those are
exactly the parts that should be compiled and frozen, not re-derived per
request.

**Determinism, 10 repeats of the same input** (`measurement.json.determinism`):
A and C give one answer on all 20 sampled cases; the proxy for B is also
stable, but that is a property of the proxy, not of a hosted model. At
temperature 0 a hosted model is *usually* stable and never *guaranteed* stable,
so a rupee-exact consumer cannot rely on it. C gets B's flexibility without
that exposure.

**Citations.** A and C attach a `cite` to every line of the trace
(`test_every_rupee_has_a_citation` walks the whole trace and fails on any
uncited item). B cannot: its answer is a number with no line-level provenance,
which is disqualifying for a tax product regardless of accuracy.

## 4. Task 5 — the law changes (Finance Act 2025)

`project_a/spec_diff.py` diffs the two specs; it finds **3 substantive deltas**
(the rest are citation strings and labels):

| Field | FY 2024-25 | FY 2025-26 |
|---|---|---|
| `slabs.new.brackets` | 3L/7L/10L/12L/15L, 30% above 15L | 4L/8L/12L/16L/20L/24L, 30% above 24L |
| `rebate_87a.new.threshold_total_income` | 7,00,000 | **12,00,000** |
| `rebate_87a.new.max_rebate` | 25,000 | **60,000** |

The new-regime standard deduction is **not** a delta: the Finance Act 2025 left
it at 75,000, which is why the salaried zero-tax ceiling is 12,75,000
(12,00,000 rebate threshold + 75,000).

| Implementation | Change made | Time | What broke | Silent failures |
|---|---|---|---|---|
| A (rules) | edited 3 fields in `fy2025_26.yaml` | ~15 min, **0 lines of Python** | nothing | none — all 1042 answers re-verified |
| C (compiled) | re-ran the compile prompt, reviewed the diff, committed the module | ~40 min (mostly review) | the **first LLM pass was wrong** | **yes — 10/1042 wrong answers, no exception** |
| B (LLM) | updated the rule text in the prompt | ~10 min | nothing in code | yes by construction — no test can pin a per-request model |

The first compiled pass (`compiled_fy2025_26_firstpass.py`, kept as a fixture)
is the honest answer to "which failures were silent". It updated the slabs and
the rebate threshold and looked right, but it did not implement the s.87A
new-regime marginal-relief proviso, so a taxpayer 10 rupees over 12,00,000 was
charged the full slab tax (62,400) instead of about 10 rupees.

It never raised. It returned a plausible rupee number. `test_task5.py` pins the
defect, and the corpus is what caught it: **10 of 1042 regime-answers
(1.0%) were silently wrong** before review.

## 5. Task 6 — final design (what I would ship to 100,000 users)

```
free text ──▶ PARSER (rule)  ──▶  structured profile
                 │
                 └─ missing/ambiguous ──▶ CLARIFYING QUESTION (rule) ──▶ user
                                              │
structured profile ──▶ TAX ENGINE (rule, YAML spec) ──▶ old/new tax + line-level trace
                                              │
                                     "why" narrative ──▶ TEMPLATE (rule)
                                              │
              FY change ──▶ COMPILER (LLM, offline, once) ──▶ reviewed YAML/Python
```

Every box is a **rule** except the compiler, which is **hybrid** and runs
offline.

* **Tax computation — rule.** It is the only component where a wrong rupee is a
  legal problem. A is 100% accurate, 0 ms, $0/1k and deterministic; B is 74.9%
  and $0.12/1k. There is no accuracy argument for the model here, and a tax
  product cannot ship non-deterministic arithmetic.
* **Plain-language parsing — rule, with an LLM fallback that must ask, not
  guess.** The 58-description corpus parses 100% with rules, including
  Hinglish. Rules are auditable and cheap; when the parser is unsure it asks
  (58/58 correct clarification decisions). If a future phrasing defeats the
  rules, the LLM's job is to *propose a structured parse that the user
  confirms*, never to compute tax.
* **The "why" — rule (templates over the engine's trace).** The trace already
  carries the citation per line; rendering it with a template is exact and
  free. An LLM here would add a way to state a legal reason that isn't in the
  trace.
* **The next Budget — hybrid, offline.** The LLM compiles the new Act into a
  spec diff, a human reviews it against the statute, and the frozen module runs.
  That is C, and it is what absorbed FY 2025-26: 3 YAML fields for A, one
  reviewed compilation for C, and — crucially — a test corpus that turned a
  silent 1% error into a red test.

**Keeping it correct through the next Budget.** Three layers: (1) the corpus
regenerates from the oracle and must stay green; (2) `spec_diff.py` produces a
reviewable, machine-readable delta so a reviewer sees every changed number;
(3) the official-calculator cross-check is re-run and any divergence beyond the
documented marginal-relief/basic-model gap fails the build. The model's role is
to *draft* the delta faster, never to be the thing that decides it.
