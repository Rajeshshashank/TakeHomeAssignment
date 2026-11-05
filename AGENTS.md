# Repository notes for agents

## What this is

Fire AI take-home: two systems (an income-tax engine, a NYC 311 data-quality
pipeline), each built three ways — rules-only, LLM-only, hybrid — measured on
the same inputs, with a defended final design.

## Build / verify

```bash
pip install -r requirements.txt
bash scripts/run_all.sh        # regenerates every number; ends with the full suite
python3 -m pytest project_a/tests project_b/tests tests -q
```

`run_all.sh` is offline by default: the LLM layers run against deterministic
proxies, so no API key is needed. `export GEMINI_API_KEY` switches them to a
real model. `tests/test_reported_numbers.py` asserts that every figure quoted in
the markdown matches the JSON artifacts; if you change a number in code, either
regenerate the artifacts or fix the docs, and that suite will tell you which.

## Invariants worth not breaking

- `project_a/taxspec/*.yaml` is the only place statutory numbers live. The
  engine, the oracle and the compiler all read it.
- The rules engine and the LLM-compiled module must agree on all 1042 corpus
  answers; `project_a/tests/test_task5.py` pins the silent failure the
  first-pass compilation shipped (it omitted the s.87A new-regime
  marginal-relief proviso).
- The old-regime s.87A rebate is a plain cliff at 5,00,000 (no marginal relief);
  only the new regime has the proviso. The engine reads
  `rebate_87a.<regime>.marginal_relief` from the YAML; the oracle and the
  compiled modules encode the same rule, and the corpus test keeps them in sync.
- The FY 2025-26 new-regime standard deduction is 75,000 (unchanged by the
  Finance Act 2025). Do not "raise" it to 1,00,000; the salaried zero-tax
  ceiling of 12,75,000 depends on the 75,000 figure.
- Detection rates, false-alarm rates and costs in the artifacts are exact.
  Latency fields are wall-clock and may differ a few percent between runs.
- Cost is per *call*: `cost_per_million_rows(calls_per_row, prompt_tokens_per_call,
  completion_tokens_per_call)`. Do not reintroduce the per-row form; see
  `tests/test_reported_numbers.py::test_cost_formula_is_per_call_not_per_row`.

## Publishing

`scripts/publish.sh [repo-name]` creates the private repo, pushes the current
branch and prints the compare URL. It needs a token with `Administration:write`.
The sandbox's `GITHUB_TOKEN` is a GitHub App user token (`ghu_`) with
repository read/write but **no** repo-creation permission, so it can push to an
existing repo but cannot create one.
