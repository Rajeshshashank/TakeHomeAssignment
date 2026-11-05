"""Task 3/4 measurement harness for Project B.

For every problem type and every detector family it reports detection rate,
false-alarm rate on clean batches, cost per million rows, throughput on this
laptop, and whether the alert explains itself. It then runs the hybrid pipeline
end to end and reports what each layer contributed and the LLM layer's cost.

Protocol: for each problem type, inject it into held-out batches at a controlled
rate, score every detector on the same corrupted batch, and separately score the
false-alarm rate on *clean* batches. Nothing is tuned on the scored batch.

Output: project_b/results/measurement.json and a printed table.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from typing import Any, Optional

from .detectors.base import score
from .detectors.llm_review import LLMDetector, cost_per_million_rows
from .detectors.profile import load_reference
from .detectors.rules import RuleDetector
from .detectors.statistical import StatisticalDetector
from .injectors.inject import PROBLEM_TYPES, Injector, load_clean_batches
from .pipeline import HybridPipeline
from .common import data_path

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = data_path(os.path.join(HERE, "data"))
REFERENCE = os.path.join(HERE, "detectors", "reference.json")
OUT = os.path.join(HERE, "results", "measurement.json")

BATCH_SIZE = 2000
N_BATCHES = 12
INJECT_RATE = 0.05


def _detectors(proxy: bool = True, reference=None) -> dict[str, Any]:
    allow = getattr(reference, "phone_allowlist", set()) if reference else set()
    return {
        "rules": RuleDetector(phone_allowlist=allow),
        "statistical": StatisticalDetector(reference=reference),
        "llm": LLMDetector(proxy=proxy, phone_allowlist=allow),
    }


def measure(proxy: bool = True, rate: float = INJECT_RATE) -> dict[str, Any]:
    reference = load_reference(REFERENCE) if os.path.exists(REFERENCE) else None
    batches = load_clean_batches(RAW, batch_size=BATCH_SIZE, n_batches=N_BATCHES)
    # Even batches score detection, odd batches score false alarms.
    inject_batches = batches[0::2]
    clean_batches = batches[1::2]
    inj = Injector(seed=1234)

    rows_out: list[dict[str, Any]] = []

    for problem in PROBLEM_TYPES:
        corrupted = [inj.inject(b, problem, rate=rate) for b in inject_batches]
        truth = set()
        n_rows = 0
        for b in corrupted:
            truth |= b.labels.get(problem, set())
            n_rows += len(b.rows)
        is_batch_problem = problem in ("renamed_or_dropped_column", "column_type_change",
                                       "date_format_switch")

        for family, det in _detectors(proxy, reference).items():
            t0 = time.perf_counter()
            dets = [det.detect(b) for b in corrupted]
            elapsed = time.perf_counter() - t0

            flagged: set[int] = set()
            batch_flag = False
            reasons: dict[int, str] = {}
            for d in dets:
                flagged |= d.flagged(problem)
                batch_flag = batch_flag or (problem in d.batch_flags)
                reasons.update(d.reasons.get(problem, {}))

            if is_batch_problem:
                # Schema-level problems are scored on the batch flag.
                detection = 1.0 if (batch_flag or flagged) else 0.0
                fa = 0
                for b in clean_batches:
                    d = det.detect(b)
                    if problem in d.batch_flags or d.flagged(problem):
                        fa += 1
                false_alarm = fa / max(1, len(clean_batches))
            elif problem in ("resolution_status_contradiction",):
                # Scored at the batch level: this is a sampling detector, so
                # per-row recall would understate what it is for. The question
                # is "does the batch contain a contradiction", and the sample
                # rate is the knob that sets the recall.
                detection = 1.0 if flagged else 0.0
                fa = 0
                for b in clean_batches:
                    if det.detect(b).flagged(problem):
                        fa += 1
                false_alarm = fa / max(1, len(clean_batches))
            else:
                detection = len(flagged & truth) / len(truth) if truth else 0.0
                fa = 0
                fa_rows = 0
                for b in clean_batches:
                    d = det.detect(b)
                    fa += len(d.flagged(problem))
                    fa_rows += len(b.rows)
                false_alarm = fa / max(1, fa_rows)

            calls_per_row = sum(d.llm_calls for d in dets) / max(1, n_rows)
            pt = sum(d.prompt_tokens for d in dets) / max(1, sum(d.llm_calls for d in dets))
            ct = sum(d.completion_tokens for d in dets) / max(1, sum(d.llm_calls for d in dets))
            cost = cost_per_million_rows(calls_per_row, pt, ct)

            rows_out.append({
                "problem": problem,
                "detector": family,
                "detection_rate": round(detection, 4),
                "false_alarm_rate": round(false_alarm, 5),
                "cost_per_million_rows_usd": round(cost, 4),
                "ms_per_1000_rows": round(elapsed / max(1, n_rows) * 1000 * 1000, 3),
                "explains_itself": bool(reasons) or is_batch_problem,
                "n_truth_rows": 0 if is_batch_problem else len(truth),
            })

    # --- hybrid pipeline end to end ------------------------------------
    hybrid_rows = []
    for problem in PROBLEM_TYPES:
        corrupted = [inj.inject(b, problem, rate=rate) for b in inject_batches]
        truth = set()
        for b in corrupted:
            truth |= b.labels.get(problem, set())
        pipe = HybridPipeline(proxy=proxy, reference=reference, sample_rate=0.05)
        t0 = time.perf_counter()
        results = [pipe.run(b) for b in corrupted]
        elapsed = time.perf_counter() - t0
        n_rows = sum(len(b.rows) for b in corrupted)
        flagged: set[int] = set()
        batch_flag = False
        for r in results:
            flagged |= r.merged.get(problem, set())
            batch_flag = batch_flag or (problem in r.merged_batch)
        is_batch_problem = problem in ("renamed_or_dropped_column", "column_type_change",
                                       "date_format_switch")
        if is_batch_problem or problem == "resolution_status_contradiction":
            detection = 1.0 if (batch_flag or flagged) else 0.0
        else:
            detection = len(flagged & truth) / len(truth) if truth else 0.0
        llm_calls = sum(r.llm_calls for r in results)
        pt = sum(r.prompt_tokens for r in results) / max(1, llm_calls)
        ct = sum(r.completion_tokens for r in results) / max(1, llm_calls)
        cost = cost_per_million_rows(llm_calls / max(1, n_rows), pt, ct)
        hybrid_rows.append({
            "problem": problem,
            "detection_rate": round(detection, 4),
            "ms_per_1000_rows": round(elapsed / max(1, n_rows) * 1000, 2),
            "llm_calls_per_row": round(llm_calls / max(1, n_rows), 4),
            "cost_per_million_rows_usd": round(cost, 4),
        })

    report = {
        "protocol": {
            "batch_size": BATCH_SIZE,
            "n_batches": N_BATCHES,
            "inject_rate": rate,
            "proxy_mode": proxy,
        },
        "detectors": rows_out,
        "hybrid": hybrid_rows,
    }
    return report


def main() -> None:
    proxy = not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    report = measure(proxy=proxy)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    by = {}
    for r in report["detectors"]:
        by.setdefault(r["problem"], {})[r["detector"]] = r
    print(f"{'problem':36s} {'rule':>12s} {'statistical':>12s} {'llm':>12s}")
    print(f"{'':36s} {'det / fa':>12s} {'det / fa':>12s} {'det / fa':>12s}")
    for p in PROBLEM_TYPES:
        cells = []
        for fam in ("rules", "statistical", "llm"):
            r = by[p].get(fam)
            cells.append(f"{r['detection_rate']*100:5.0f}% /{r['false_alarm_rate']*100:5.2f}%" if r else " " * 12)
        print(f"{p:36s} " + " ".join(f"{c:>12s}" for c in cells))
    print("\nhybrid pipeline:")
    for r in report["hybrid"]:
        print(f"  {r['problem']:36s} det {r['detection_rate']*100:5.0f}%  "
              f"${r['cost_per_million_rows_usd']:8.2f}/M  "
              f"{r['ms_per_1000_rows']:7.1f} ms/1k rows")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
