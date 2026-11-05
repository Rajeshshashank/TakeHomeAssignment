# Rules vs LLMs — when to use which, with the measurements to prove it

**Fire AI take-home assignment.** Two systems, each built three ways —
rules-only, LLM-only, and hybrid — measured on the same inputs, and a defended
final design. The thesis: use an LLM only where a cheaper check measurably
fails, and put the LLM where it can be tested.

| Project | What it is | Where |
|---|---|---|
| **A** | Indian income-tax engine (FY 2024-25 → FY 2025-26), rupee-exact, salary income only | `project_a/` |
| **B** | Data-quality layer in front of dashboards, NYC 311 service requests 2023 | `project_b/` |

Results: [`RESULTS_A.md`](RESULTS_A.md) · [`RESULTS_B.md`](RESULTS_B.md) ·
[`DECISION_MATRIX.md`](DECISION_MATRIX.md) · [`AI_USAGE.md`](AI_USAGE.md)

## The one-line answer

> Rules for anything with an exact answer or a contract; statistics for
> anything distributional; an LLM only for semantic judgement, and only on the
> rows the cheap layers could not decide.

In Project A that means **zero LLM calls at query time** (the LLM compiles the
rules once, offline, under review). In Project B it means **one problem type out
of twelve** ever reaches the model, cutting the naive LLM cost from $16.7 to
$0.94 per million rows with no loss of detection.

## Reproduce everything with one command

```bash
pip install -r requirements.txt
bash scripts/run_all.sh
```

`run_all.sh` is offline by default: the LLM layers run against deterministic
proxies, so every number below is reproducible with no API key. Export
`GEMINI_API_KEY` to re-run the LLM layers against a real model; the result files
record which mode was used. The final step runs **674 tests**, including a suite
that asserts every number quoted in the write-ups matches the JSON artifacts.
Requires Python 3.10+.

Every detection rate, false-alarm rate and cost figure is exact and reproducible.
The only fields that move run to run are the wall-clock latencies (a few percent
on a laptop), so a re-run may leave `project_b/results/measurement.json` and
`project_a/tests/cases/measurement.json` with latency-only diffs; the accuracy,
cost and detection numbers will not change.

The shipped repo carries the 108 MB raw extract gzipped
(`project_b/data/nyc311_2023_sample.jsonl.gz`); `run_all.sh` expands it on first
run. To re-fetch instead: `python3 scripts/fetch_nyc311.py`.

## Test suites

```bash
python3 -m pytest project_a/tests project_b/tests tests -q
```

**674 tests, all green.** 650 Project A tests (the frozen 1042-answer corpus,
the parser corpus, the citation invariant, the FY 2025-26 change and its silent
failures), 10 Project B tests (injector labelling, clean-batch invariants, each
detector family on the problem it owns, the pipeline's cost bound), and 14
cross-cutting tests that assert every figure quoted in these write-ups matches
the regenerated JSON artifacts.

## Headline numbers

| | Accuracy | Latency | Cost | Deterministic |
|---|---|---:|---:|---|
| **A** rules engine | 100.0% (1042/1042) | 0.0 ms | $0.0000 / 1k | yes |
| **B** LLM at query time | 73.5% (766/1042) | 185 ms | $0.1199 / 1k | not guaranteed |
| **C** LLM-compiled rules | 100.0% (1042/1042) | 0.02 ms | $0.0000 / 1k | yes |

| | Best family | Detection / false-alarm |
|---|---|---|
| Schema, ranges, dates, PII | rules | 100% / 0.00% |
| Duplicates, null spikes, drift | statistics | 100% / 0.00–0.58% |
| Status vs resolution text | LLM | 100% / 0.00% ($16.61 / M rows) |
| Hybrid pipeline, all 12 problems | hybrid | 99–100% at $0.94–1.76 / M rows |

## Layout
```
project_a/
  engine.py  spec.py  models.py  tax_stage.py   the rules engine (YAML-driven)
  oracle.py  oracle_2025.py                     independent oracle that froze the corpus
  nlp/parser.py                                 plain-language → structured profile
  llm/impl_b.py  llm/impl_c.py                  the two LLM implementations
  compiled/                                     reviewed LLM-compiled rules + the first-pass fixture
  taxspec/fy2024_25.yaml  fy2025_26.yaml        the only place statutory numbers live
  tests/                                        corpus, parser tests, FY-2025-26 change tests
project_b/
  injectors/inject.py                           the 12-problem corruption injector
  detectors/{rules,statistical,llm_review}.py   the three detector families
  detectors/profile.py  reference.json          the frozen reference distribution
  pipeline.py                                   the hybrid pipeline
  measure_b.py                                  the measurement harness
scripts/
  run_all.sh                                    one command, every number
  publish.sh                                    create the private repo and push it
  fetch_nyc311.py  profile_raw.py  build_reference.py
tests/
  test_reported_numbers.py                      every quoted figure vs the JSON artifacts
```
