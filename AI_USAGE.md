# AI usage note

## Which tools, for what

| Tool | Used for | Not used for |
|---|---|---|
| Claude / an LLM coding agent | scaffolding the module layout, drafting the YAML tax specs from the Finance Act text, drafting the 58-description corpus, drafting the injector's corruption vocabulary, writing docstrings and this set of markdown files | deciding any statutory number without a check, choosing which detector family owns a problem, or writing the numbers in the results tables — those are produced by `scripts/run_all.sh` |
| An LLM as the *subject* of the experiment | Implementation B (Project A) and the LLM detector family (Project B), both behind a deterministic proxy offline and a real Gemini call when `GEMINI_API_KEY` is set | the shipped computation path |
| An LLM as the *compiler* | Implementation C: one-shot conversion of the rule text into an executable module | running that module at query time |

The brief allows and expects AI tooling, so the honest framing is: an LLM wrote
much of the code, and the work that matters is the verification around it. The
deliverables are built so that "did the model get it right" is answered by a
test, not by reading the output.

## Where the AI was wrong, and how it was caught

### 1. The compiled FY 2025-26 rules were silently wrong

This is the central example, and it is deliberately preserved in the repo as
`project_a/compiled/compiled_fy2025_26_firstpass.py`.

The compile prompt was given the FY 2025-26 rule text and asked for one
self-contained Python module. The model returned clean, plausible code that
updated the new-regime slabs and the s.87A rebate threshold. It ran without
error. It was wrong: it did not implement the s.87A new-regime **marginal-relief
proviso**, so a taxpayer 10 rupees over 12,00,000 was charged the full slab tax
(62,400) instead of about 10 rupees.

Nothing about the output signalled a problem. **How it was caught:** the
1042-answer corpus was written *before* the compilation (Task 1 is "test suite
first"), so running the unreviewed module against it showed the wrong answers
with zero exceptions — the definition of a silent failure.
`project_a/tests/test_task5.py` now pins the defect, so the same mistake cannot
return unnoticed.

The lesson is not "the model is bad"; it is that a fluent, error-free artifact
is not evidence of correctness, and the only thing that turned this into a
30-second fix instead of a shipped bug was having the oracle and the corpus
already in place.

### 2. The cost model understated the LLM bill by ~20x

The first version of `cost_per_million_rows` in
`project_b/detectors/llm_review.py` was passed *per-row* token averages and then
multiplied by calls-per-row:

```python
# wrong: rows counted twice
per_row = (prompt_tokens_per_row / 1e6 * price_in
           + completion_tokens_per_row / 1e6 * price_out) * calls_per_row
```

It produced a tidy **$0.79 per million rows**, which went into RESULTS_B.md,
README.md and DECISION_MATRIX.md and looked like a great cost story. It was
wrong. `prompt_tokens_per_row` is *already* per row; multiplying by calls-per-row
(a further per-row factor) counts the row once too often and divides the real
per-call context cost by the chunk size (20).

**How it was caught:** deriving the units by hand for the Task 6 projection.
One call carries up to 20 rows, so its prompt is ~1,200 system tokens + 20 x ~550
row tokens ≈ 12,200 tokens, not ~610. Writing the multiplication out as
`(tokens/call x $/token) x calls/row x 1e6 rows` made the double-count obvious.

The corrected figure is **$16.6–16.8 per million rows** for the LLM reviewing
every row, which changes the scale story from "$39.5M/day" to **$835/day** at
50M rows/day — and the hybrid's advantage from "90x" to a still-decisive **~18x**.
The fix is the per-*call* signature, and
`tests/test_reported_numbers.py::test_cost_formula_is_per_call_not_per_row`
now fails if anyone reintroduces the per-row form. This is the failure mode the
brief warns about: an LLM-produced number that is self-consistent, plausible and
wrong, caught only by checking it against first principles rather than against
another model output.

### 3. The data profiler's null analysis crashed on an empty counter

The first version of `scripts/profile_raw.py` computed `null_counts` and then
took `null_counts.most_common(1)[0]`. On a column set where the counter came
back empty it raised `IndexError`. That is a hard failure — it stopped the run
— so it was cheap to find and is now guarded. Recorded here because it is the
*opposite* failure mode to example 1: loud, and therefore easy.

### 4. The first contradiction detector was too broad

The first cut of "resolution text contradicts status" used a negative-word
regex, which fired on **7,800 rows (5.42%)** — including legitimate closures
like "we could not find the problem you reported" and "officers were unable to
gain entry". Those are correct resolutions, not contradictions. Reporting 7,800
false alarms would have drowned the team the layer exists to protect.

**How it was caught:** reading the matched examples rather than trusting the
count. The narrowed signal is Closed rows that promise *future* work ("will be
referred", "will be scheduled", "will be conducted"), which is the set in
`RESULTS_B.md` §4. The lesson: a detection rate is only meaningful next to a
false-alarm rate, and the way to earn that is to look at what actually fired.

## What I checked by hand rather than trusting the tooling

* Every statutory number in `taxspec/*.yaml` against the Finance Act text, and
  the 30 official-calculator rows in
  `project_a/tests/cases/official_calculator_checks.json` — including the 8 rows
  that diverge from the statute (the s.87A new-regime edge and the three
  surcharge thresholds, plus rupee flooring) to confirm each is the portal's
  documented "basic calculator" limitation and not our bug.
* The 12 injected problem types against the injector's labels, via
  `project_b/tests/test_detectors.py::test_injector_labels_exactly_what_it_corrupts`.
* The agency hotline allow-list by frequency, so PII alerts are residents and
  not the city's own numbers.
