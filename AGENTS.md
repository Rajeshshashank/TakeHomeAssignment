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
  answers; `project_a/tests/test_task5.py` pins the two silent failures the
  first-pass compilation shipped.
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
