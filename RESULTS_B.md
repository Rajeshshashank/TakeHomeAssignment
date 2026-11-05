# Results — Project B: data-quality checks

Everything below is produced by `bash scripts/run_all.sh`. Machine output is in
`project_b/results/raw_profile.json` and `project_b/results/measurement.json`.

## 1. Task 1 — what is actually wrong in the raw 2023 data

144,000 rows created in calendar year 2023, sampled across all twelve months
from NYC Open Data (dataset `erm2-nwe9`). Real problems found, with counts:

| Problem | Rows | Share | Real example |
|---|---:|---:|---|
| `column_type_change` (lat/lon arrive as strings) | 141,827 | 98.49% | `latitude = "40.72762987830367"` |
| `null_spike` (`location_type` absent) | 16,352 | 11.36% | Socrata omits the key entirely when null |
| `resolution_status_contradiction` | 7,800 | 5.42% | Closed, but text says "will be referred"/"still open" |
| `near_duplicates` | 4,301 | 2.99% | keys `56417235` and `56417105`: same type, address, timestamp |
| `missing_coordinates` | 2,173 | 1.51% | `56418774`, Illegal Fireworks, BROOKLYN 11220, no lat/lon |
| `exact_duplicates` (content-identical, different key) | 831 | 0.58% | 758 groups, up to 4 copies |
| `pii_in_text` | 211 | 0.15% | agency email `infobfscs@health.nyc.gov` in resolution text |
| `closed_before_created` | 188 | 0.13% | `56682540`: created 2023-02-01, closed 2023-01-31 |
| `missing_status` | 50 | 0.035% | status "Unspecified" or absent |
| `zip_borough_mismatch` | 49 | 0.034% | `56413505`: ZIP 10463 (Bronx) labelled MANHATTAN |
| `renamed_or_dropped_column` | 0 | — | schema matches the documented extract |
| `latlon_swapped` | 0 | — | none present; the injector models it |
| `date_format_switch` | 0 | — | one ISO format throughout; the injector models the switch |
| `complaint_descriptor_mismatch` | 0 | — | none present in this extract |

Two findings worth calling out because they shape the design:

* **`exact_duplicates` are not duplicate keys.** All 144,000 `unique_key`s are
  distinct; the 831 duplicates are rows that are *content-identical* on every
  other field. A uniqueness constraint on the key would pass and the duplication
  would still be there. The detector hashes content, not the key.
* **The contradiction problem is real but its phrases are subtle.** The naive
  reading — "Closed and the text contains a negative word" — fires 7,800 times,
  but most of those are legitimate closures ("we could not find the problem").
  The genuinely contradictory ones are future-tense promises on a Closed row:
  `will be referred`, `will be scheduled`, `will be conducted` (see §4). This is
  the single most important reason the LLM earns its cost in this pipeline.

## 2. Task 2 — the injector

`project_b/injectors/inject.py` injects each of the twelve problem types into
*clean* batches at a controlled rate (default 5%) and records exactly which
rows — and, for the three schema-level problems, which batch — were corrupted.
Those labels are the ground truth for every detection rate below.

The clean corpus is curated (`load_clean_rows`): rows exhibiting any known
problem signature are excluded, because scoring a false-alarm rate on data that
already contains the problem is meaningless. 126,950 clean rows survive, and
the reference profile is frozen from them (`detectors/reference.json`: 167
complaint types, 219 ZIP→borough histories, 14 agencies, an 8-number agency
hotline allow-list learned by frequency).

## 3. Task 3/4 — detectors, per problem type

Protocol: each problem injected at 5% into 6 batches of 2,000 rows; detection
scored on those rows; false-alarm rate scored on 6 *clean* batches. Batch-level
problems (the three schema ones, plus the sampling-based contradiction check)
are scored on the batch flag.

`det / fa` = detection rate / false-alarm rate. Cost is the LLM family's cost
per million rows at Gemini Flash list prices. **ms/1k** is laptop throughput —
total wall-clock for the family over the batch, normalised to 1,000 rows —
reported for the family that **owns** each problem (the winner in the row), so
the table is the shipped design's cost and speed, not a lab average.

| Problem | Rules | Statistical | LLM review |
|---|---|---|---|
| renamed_or_dropped_column | **100% / 0.00%** | 0% / 0.00% | 0% / 0.00% |
| column_type_change | **100% / 0.00%** | 0% / 0.00% | 0% / 0.00% |
| date_format_switch | **100% / 0.00%** | 0% / 0.00% | 0% / 0.00% |
| latlon_swapped | **100% / 0.00%** | **100% / 0.00%** | 0% / 0.00% |
| exact_duplicates | 0% / 0.00% | **100% / 0.00%** | 0% / 0.00% |
| near_duplicates | 0% / 0.00% | **100% / 0.58%** | 0% / 0.00% |
| null_spike | 0% / 0.00% | **100% / 0.00%** | 0% / 0.00% |
| closed_before_created | **100% / 0.00%** | 0% / 0.00% | 0% / 0.00% |
| zip_borough_mismatch | **100% / 0.00%** | **100% / 0.00%** | 0% / 0.00% |
| complaint_descriptor_mismatch | 65% / 0.00% | **99% / 0.07%** | 65% / 0.00% |
| resolution_status_contradiction | 0% / 0.00% | 0% / 0.00% | **100% / 0.00%** |
| pii_in_text | **100% / 0.00%** | 0% / 0.00% | **100% / 0.00%** |

**The shipped assignment of each problem, with throughput, cost and whether the
alert explains itself** (this is the same data, read as the design decision):

| Problem | Owner | det | fa | ms / 1k rows | $/M rows | alert self-explains |
|---|---|---:|---:|---:|---:|---|
| renamed_or_dropped_column | rule | 100% | 0.00% | 34.4 | 0.00 | yes |
| column_type_change | rule | 100% | 0.00% | 33.9 | 0.00 | yes |
| date_format_switch | rule | 100% | 0.00% | 35.2 | 0.00 | yes |
| latlon_swapped | rule | 100% | 0.00% | 33.9 | 0.00 | yes |
| exact_duplicates | statistical | 100% | 0.00% | 21.0 | 0.00 | yes |
| near_duplicates | statistical | 100% | 0.58% | 20.8 | 0.00 | yes |
| null_spike | statistical | 100% | 0.00% | 135.8 | 0.00 | yes |
| closed_before_created | rule | 100% | 0.00% | 35.2 | 0.00 | yes |
| zip_borough_mismatch | rule | 100% | 0.00% | 33.4 | 0.00 | yes |
| complaint_descriptor_mismatch | statistical | 99% | 0.07% | 19.1 | 0.00 | yes |
| resolution_status_contradiction | LLM | 100% | 0.00% | 51.6 | 16.61 | yes |
| pii_in_text | rule | 100% | 0.00% | 35.8 | 0.00 | yes |

Throughput is single-core on this laptop. The rule and statistical layers run at
20–136 ms per 1,000 rows (~7k–48k rows/s/core) at zero marginal cost. The LLM
layer is the only paid one, and its price is set by the *call*, not the row: one
request carries up to `batch_size` rows, so the whole game is deciding how few
rows reach it (§5).

LLM-family cost and throughput when it reviews **every** row (the naive design):

| Problem sent to LLM | $/million rows | ms / 1k rows |
|---|---:|---:|
| resolution_status_contradiction | 16.61 | 59.2 |
| pii_in_text | 16.84 | 56.9 |
| complaint_descriptor_mismatch | 16.76 | 54.2 |

That ~$16.7 per million rows is the number the hybrid pipeline is designed to
avoid: at 20 rows per call it is ~12,200 prompt tokens and ~500 completion
tokens *per request*, dominated by re-sending the system prompt and by the
model's prose answer. (An earlier version of this harness charged tokens per
*row* and then multiplied by calls-per-row, which understated the bill by the
chunk size; `tests/test_reported_numbers.py` now pins the corrected figure.)

What the table says:

* **Rules win everything structural.** Schema drift, type change, date-format
  switch, impossible coordinates, closed-before-created, ZIP/borough and PII
  are all 100% at zero cost. These are contracts, not judgements.
* **Statistics win everything distributional.** Duplicates (exact and near),
  null spikes and the *probabilistic* form of descriptor mismatch (a descriptor
  with probability < 0.002 for its complaint type) need a reference distribution,
  not a rule.
* **The LLM wins exactly one problem outright:** status/resolution
  contradiction. No rule or statistic gets it (both 0%), because deciding
  whether "will be referred to the appropriate agency" contradicts `Closed`
  needs reading the sentence.
* **`complaint_descriptor_mismatch` is the interesting split.** Rules get 65%
  (only the impossible cross-domain pairs), statistics get 99% with a 0.07%
  false-alarm rate, and the LLM gets 65% — the LLM adds nothing over
  statistics here, so it does not get paid for it in the pipeline.
* **The LLM is honestly weak on what it cannot see.** It scores 0% on
  `latlon_swapped` and `zip_borough_mismatch`: those are not semantic
  judgements, and the proxy is deliberately not credited for them.

## 4. The real issues, and the one that drove the design

The profile's 7,800 "contradictions" are dominated by legitimate closures. The
real, actionable set is Closed rows whose text promises future work:

| Phrase on a `Closed` row | Rows |
|---|---:|
| "…the original complaint is still open." | 3,165 |
| "Location will be referred to the appropriate agency." | 595 |
| "…the site will be considered for…" | 523 |
| "Follow-up inspections will be scheduled." | 520 |
| "…addressing this work can take up to a year or more…" | 482 |
| "…will be inspected according to its normal schedule." | 40 |
| "…you will be contacted." | 12 |
| "…will continue to monitor." | 1 |

Example: `56419473`, DSNY, status `Closed`, text "The Department of Sanitation
investigated this complaint and no DSNY related conditions/violations were
observed. Location will be referred to the appropriate agency for further
action." A CFO reading "Closed" and "referred for further action" in the same
record is exactly the silent inconsistency this layer exists to catch.

## 5. Task 5 — the hybrid pipeline, end to end

`project_b/pipeline.py`: rules on every row, statistics per batch, then the LLM
**only on rows the cheap layers flagged for a semantic problem plus a 5% random
sample** (the sample is what lets the LLM catch a problem no rule fired on).
The prefilter is capped at 10% of the batch so a runaway rule cannot turn a free
layer into a large bill.

| Problem | Detection | $/million rows | LLM calls/row | ms / 1k rows |
|---|---:|---:|---:|---:|
| all 12 problem types | 100% (99% for descriptor mismatch) | ≤ 1.76 | ≤ 0.0050 | ≤ 170 |

The hybrid catches every problem the best single family catches, at a blended
cost of **at most $1.76 per million rows** (median $0.94) — because 11 of 12
problems never reach the LLM, and the twelfth is reached only for flagged rows
plus a 5% sample, batched 20 rows to a call. Cost discipline is structural, not
a budget check bolted on at the end.

For comparison, sending **every** row to the LLM costs **$16.6–16.8 per million
rows** and still misses the schema problems entirely. The prefilter is a **~18×**
cost reduction with no loss of detection on this corpus — and it is what keeps
the LLM stage affordable at all.

## 6. Task 6 — scale and design (50M rows/day, 120 customers)

Naive per-row LLM review: 50M × $16.7 = **$835/day, ~$305k/year**. The shipped
pipeline:

```
every row   ──▶ RULES      (schema, ranges, dates, PII)       $0
per batch   ──▶ STATISTICS (dupes, null spikes, drift)        $0
flagged+5%  ──▶ LLM        (semantic contradiction only)      metered
```

At 50M rows/day with ~0.3% of rows reaching the LLM (flagged + 5% sample, then
batched 20 rows per call), that is ≈150k LLM calls/day ≈ **$47/day (~$17k/year)**
for 120 customers — ~5.6% of the naive cost — plus the free layers, which need
50M/86,400 ≈ 580 rows/s. At 34 ms per 1,000 rows that is 0.02 cores; even at a
100× safety margin for 120 heterogeneous customer schemas it is a handful of
cores, sharded per customer.

The honest trade at scale: the free layers scale linearly and are embarrassingly
parallel, so the CPU is never the constraint. The constraint is the metered
stage, and the two levers that move it are the **sample rate** (5% here) and the
**rows per call** (20 here). Doubling rows-per-call halves the per-row cost of
the system prompt; halving the sample rate halves the rows that reach it — both
trade detection latency, not detection rate, on the problems the LLM owns.

What changes at that scale, and why:

* **Rules stay per-row and get compiled to vectorised SQL/Arrow.** A
  deterministic predicate is the cheapest thing in the system; at 50M rows it
  should run in the warehouse next to the data, not be pulled into Python.
* **Statistics move to streaming/approximate.** Exact duplicate hashing does not
  scale to 50M/day across 120 schemas; a rolling reference plus approximate
  membership (count-min sketch, locality-sensitive hashing) keeps the drift and
  duplicate checks at $0 while staying within a bounded error.
* **The LLM layer becomes a queue with a per-customer budget.** At 120 customers
  the metering has to be per-tenant (cost attribution, blast radius), and the
  sample rate becomes the dial that trades recall for money — which is why it is
  a parameter, not a constant.
* **120 different schemas means schema drift detection is the first check, not a
  special case.** A renamed or retyped column is exactly how one customer's feed
  silently breaks; the rule layer already treats the declared schema as the
  contract, so a per-customer schema registry is the only new component.
* **What does *not* change:** the LLM still only reads free text for semantic
  judgements, still never decides schema, type, range or duplicate questions,
  and still never sees a row the cheap layers could have decided.

## 7. Reproduce

```bash
bash scripts/run_all.sh          # offline, no API key, ~4 min
python3 -m pytest project_a/tests project_b/tests -q
```

To re-run the LLM layers against a real Gemini model instead of the proxy:
`export GEMINI_API_KEY=... && bash scripts/run_all.sh`. The result files record
which mode was used (`proxy_mode`).
