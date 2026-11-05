# Decision matrix

One row per component. The measurement column is the number that decided it;
the "why not the alternative" column is the number that ruled the other option
out. All figures come from `project_a/tests/cases/measurement.json`,
`project_a/tests/cases/official_calculator_checks.json` and
`project_b/results/measurement.json` (regenerate with
`bash scripts/run_all.sh`).

## Project A — income-tax engine

| Component | Decision | Measurement that justifies it | Why not the alternative |
|---|---|---|---|
| Slab/surcharge/rebate arithmetic | **rule** | 100.0% on 1042 answers, 0 ms, $0/1k, deterministic, every line cited | LLM at query time: 73.5% accurate, 185 ms, $0.12/1k, no line-level citations |
| Rule source of truth (slabs, caps, thresholds) | **rule** (YAML spec) | FY 2025-26 absorbed by editing 7 fields, 0 lines of Python | hard-coding would make the Budget a code change; a model re-deriving it per request is not auditable |
| Plain-language → structured profile | **rule** | 139/139 facts, 58/58 clarification decisions across 13 styles incl. Hinglish | an LLM here would add cost and non-determinism to a step rules already do perfectly; its role is limited to proposing a parse the user confirms |
| Missing/ambiguous input | **rule** (ask, don't guess) | 58/58 clarification decisions correct | guessing is explicitly disallowed by the brief; a model that guesses silently is the failure mode |
| "Why this number" narrative | **rule** (templates over the trace) | engine trace carries a `cite` per line; `test_every_rupee_has_a_citation` enforces it | an LLM could state a legal reason not present in the trace |
| FY change compilation | **hybrid** (LLM drafts, human reviews, rules run) | first unreviewed pass was silently wrong on 324/1042 (31.1%); reviewed pass 100% | pure-rules edits work but are slower to draft; pure-LLM at query time cannot be pinned by tests |

## Project B — data-quality checks

Detection = rate on injected rows (or batch flag); FA = false-alarm rate on
clean batches. `$ / M rows` is the LLM family's cost.

| Problem | Decision | Measurement | Why not the alternative |
|---|---|---|---|
| Renamed or dropped column | **rule** | 100% / 0.00% FA, $0 | statistics 0%, LLM 0% — a schema is a contract, not a judgement |
| Column type change | **rule** | 100% / 0.00% FA, $0 | statistics 0%, LLM 0% |
| Date format switch | **rule** | 100% / 0.00% FA, $0 | statistics 0%, LLM 0% |
| Latitude/longitude swapped | **rule** | 100% / 0.00% FA, $0 | statistical z-score also 100% but needs a reference; the LLM scores 0% (it cannot see the envelope) |
| Exact duplicates | **statistical** | 100% / 0.00% FA | a rule on `unique_key` fails: all 144,000 keys are distinct while 831 rows are content-identical |
| Near-duplicates | **statistical** | 100% / 0.58% FA | rules cannot express "same request, timestamp nudged"; the 0.58% FA is the honest price |
| Null spike | **statistical** | 100% / 0.00% FA | a fixed rule cannot know an agency's baseline null rate; the check is drift vs the frozen reference |
| Closed before created | **rule** | 100% / 0.00% FA, $0 | a total order on two timestamps needs no distribution |
| ZIP inconsistent with borough | **rule** + **statistical** | rules 100% / 0.00%; stats 100% / 0.00% | rule uses ZIP-prefix ranges; statistics uses per-ZIP borough history and catches ZIPs the prefix map omits |
| Complaint type vs Descriptor | **statistical** | 99% / 0.07% FA | rules 65% (only impossible cross-domain pairs), LLM 65% — statistics wins on both detection and cost |
| Resolution text vs Status | **LLM** | 100% / 0.00% FA, $16.61/M | rules 0%, statistics 0% — this is the one check that needs reading the sentence |
| Personal data in free text | **rule** | 100% / 0.00% FA, $0 | LLM also 100% but at $16.84/M; rules plus a frequency-learned agency allow-list are sufficient |
| Whole pipeline | **hybrid** | all 12 problems at ≤ $1.76/M rows (median $0.94), ≤ 0.2 ms/1k rows | all-rows-to-LLM = $16.7/M rows and still misses the schema problems |

## Cross-cutting trade-offs (stated honestly)

| Axis | Rules | Statistical | LLM |
|---|---|---|---|
| Auditability | total — each alert names the clause or the failed predicate | medium — "0.2% probability given the reference" needs the reference to be explained | low — the reason is prose; it can be wrong and fluent |
| Determinism | exact | exact (frozen reference) | not guaranteed; the proxy is stable but a hosted model need not be |
| Maintenance | edit a spec/rule; the FY change was 7 fields | rebuild the reference when the data genuinely drifts (or the detector chases its own tail) | prompt and model both change under you; needs a frozen eval set |
| Cost | ~0 | ~0 | $16.6–16.8 per million rows when it sees every row; $0.94–1.76 in the hybrid |
| Where it fails | anything requiring judgement | anything with no reference distribution, or a genuinely new value | anything requiring exactness, or that it cannot see |

The design principle that falls out of the matrix: **spend the LLM only where
the cheaper families measurably score 0%, and cap what reaches it.** That is
exactly one problem in Project B (status/resolution contradiction) and zero in
Project A at query time.
