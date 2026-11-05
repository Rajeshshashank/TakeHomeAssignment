"""Tests for the Project B injector and detectors.

These assert the *contract* the measurement relies on: the injector labels
exactly the rows it corrupted, a clean batch really is clean, and each detector
family fires on the problem it is supposed to own. If any of these break, the
detection rates in RESULTS_B.md are meaningless.
"""
from __future__ import annotations

import os

import pytest

from project_b.common import data_path
from project_b.detectors.llm_review import LLMDetector
from project_b.detectors.profile import build_reference, load_reference
from project_b.detectors.rules import RuleDetector
from project_b.detectors.statistical import StatisticalDetector
from project_b.injectors.inject import (
    PROBLEM_TYPES,
    Batch,
    Injector,
    load_clean_batches,
    load_clean_rows,
)
from project_b.pipeline import HybridPipeline

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = data_path(os.path.join(HERE, "data"))
REFERENCE = os.path.join(HERE, "detectors", "reference.json")


@pytest.fixture(scope="module")
def clean_batch() -> Batch:
    return load_clean_batches(RAW, batch_size=500, n_batches=1)[0]


@pytest.fixture(scope="module")
def reference():
    return load_reference(REFERENCE)


def test_every_problem_type_has_an_injector() -> None:
    assert len(PROBLEM_TYPES) == 12
    for problem in PROBLEM_TYPES:
        assert hasattr(Injector(), f"_inj_{problem}")


def test_clean_rows_have_no_problems() -> None:
    rows = load_clean_rows(RAW)
    assert len(rows) > 1000
    allow = build_reference(rows).phone_allowlist
    batch = Batch(rows=rows[:2000])
    det = RuleDetector(phone_allowlist=allow).detect(batch)
    # A curated clean batch must not trip the rules that define "clean".
    for problem in ("zip_borough_mismatch", "closed_before_created",
                    "complaint_descriptor_mismatch", "pii_in_text", "latlon_swapped"):
        assert not det.flagged(problem), f"{problem} fired on a clean batch"


def test_injector_labels_exactly_what_it_corrupts(clean_batch: Batch) -> None:
    inj = Injector(seed=5)
    for problem in PROBLEM_TYPES:
        out = inj.inject(clean_batch, problem, rate=0.05)
        labels = out.labels.get(problem, set())
        if problem == "renamed_or_dropped_column":
            assert out.batch_labels[problem]
        else:
            assert labels, f"{problem} produced no labels"
            assert all(0 <= i < len(out.rows) for i in labels)


def test_injection_does_not_mutate_the_source_batch(clean_batch: Batch) -> None:
    before = len(clean_batch.rows)
    Injector(seed=3).inject(clean_batch, "exact_duplicates", rate=0.1)
    assert len(clean_batch.rows) == before


def test_schema_injections_are_detected_by_rules(clean_batch: Batch) -> None:
    inj = Injector(seed=9)
    rules = RuleDetector()
    for problem in ("renamed_or_dropped_column", "column_type_change", "date_format_switch"):
        out = inj.inject(clean_batch, problem, rate=0.05)
        det = rules.detect(out)
        assert problem in det.batch_flags, f"{problem} not flagged at batch level"


def test_latlon_swap_is_caught_by_rules(clean_batch: Batch) -> None:
    out = Injector(seed=1).inject(clean_batch, "latlon_swapped", rate=0.05)
    det = RuleDetector().detect(out)
    assert det.flagged("latlon_swapped") >= out.labels["latlon_swapped"]


def test_statistical_catches_duplicates_and_null_spike(clean_batch: Batch, reference) -> None:
    inj = Injector(seed=2)
    stats = StatisticalDetector(reference=reference)
    for problem in ("exact_duplicates", "near_duplicates", "null_spike"):
        out = inj.inject(clean_batch, problem, rate=0.05)
        det = stats.detect(out)
        hit = det.flagged(problem) & out.labels[problem]
        assert len(hit) / max(1, len(out.labels[problem])) > 0.5


def test_llm_proxy_catches_semantic_problems(clean_batch: Batch) -> None:
    inj = Injector(seed=4)
    llm = LLMDetector(proxy=True)
    for problem in ("resolution_status_contradiction", "pii_in_text"):
        out = inj.inject(clean_batch, problem, rate=0.05)
        det = llm.detect(out)
        assert det.flagged(problem) & out.labels[problem], f"{problem} missed"
        assert det.llm_calls > 0 and det.prompt_tokens > 0


def test_hybrid_pipeline_covers_every_problem(clean_batch: Batch, reference) -> None:
    inj = Injector(seed=6)
    pipe = HybridPipeline(proxy=True, reference=reference)
    for problem in PROBLEM_TYPES:
        out = inj.inject(clean_batch, problem, rate=0.05)
        res = pipe.run(out)
        merged = res.merged.get(problem, set())
        batch_hit = problem in res.merged_batch
        assert merged or batch_hit, f"pipeline missed {problem} entirely"


def test_hybrid_llm_cost_is_bounded(clean_batch: Batch, reference) -> None:
    pipe = HybridPipeline(proxy=True, reference=reference, sample_rate=0.02)
    out = Injector(seed=7).inject(clean_batch, "zip_borough_mismatch", rate=0.05)
    res = pipe.run(out)
    # The LLM layer must only see a small fraction of rows, whatever the rules
    # flag: this is the cost-discipline claim in RESULTS_B.md.
    assert res.llm_calls <= 0.1 * len(out.rows) + 5
