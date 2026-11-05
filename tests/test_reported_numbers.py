"""Every number quoted in the markdown must match the machine artifacts.

The write-ups are the deliverable, and a hand-typed table is the easiest place
for a report to drift from the measurement it claims to describe. This suite
parses the headline figures out of ``README.md``, ``RESULTS_A.md``,
``RESULTS_B.md`` and ``DECISION_MATRIX.md`` and asserts each one is present in
the JSON that ``scripts/run_all.sh`` produced.

It is deliberately a *string* check against the artifact, not a re-derivation:
if the report says "$0.0088 per million rows", the artifact must contain
0.0088. That catches a stale table even when the underlying code is right.
"""
from __future__ import annotations

import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A_MEASURE = os.path.join(ROOT, "project_a", "tests", "cases", "measurement.json")
A_CHECKS = os.path.join(ROOT, "project_a", "tests", "cases", "official_calculator_checks.json")
A_SPECDIFF = os.path.join(ROOT, "project_a", "tests", "cases", "spec_diff.json")
B_MEASURE = os.path.join(ROOT, "project_b", "results", "measurement.json")
B_PROFILE = os.path.join(ROOT, "project_b", "results", "raw_profile.json")


def _md(name: str) -> str:
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def a_measure() -> dict:
    with open(A_MEASURE, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def b_measure() -> dict:
    with open(B_MEASURE, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def b_profile() -> dict:
    with open(B_PROFILE, encoding="utf-8") as fh:
        return json.load(fh)


def _impl(measure: dict, prefix: str) -> dict:
    return next(i for i in measure["implementations"] if i["name"].startswith(prefix))


def test_project_a_accuracy_matches_docs(a_measure):
    a, b, c = _impl(a_measure, "A"), _impl(a_measure, "B"), _impl(a_measure, "C")
    assert a["accuracy"] == 1.0 and c["accuracy"] == 1.0
    assert abs(b["accuracy"] - 0.7486) < 1e-4
    for doc in ("README.md", "RESULTS_A.md"):
        text = _md(doc)
        assert "100.0%" in text and "74.9%" in text, doc
        assert "1042/1042" in text, doc


def test_project_a_latency_and_cost_match_docs(a_measure):
    b = _impl(a_measure, "B")
    # The report rounds to 185 ms and $0.1209/1k.
    assert abs(b["latency"]["mean_ms"] - 185.5) < 1.0
    usage = b["usage"]
    paid = (usage["prompt_tokens"] / 1e6 * 0.10 + usage["completion_tokens"] / 1e6 * 0.40)
    per_1k = paid / b["cases"] * 1000
    assert abs(per_1k - 0.1209) < 0.001
    assert "0.1209" in _md("RESULTS_A.md")
    assert "185" in _md("RESULTS_A.md")


def test_project_a_fidelity_sweep_matches_docs(a_measure):
    sweep = {r["fidelity"]: r for r in a_measure["fidelity_sweep"]}
    assert sweep["full"]["correct"] == 1042
    assert sweep["no_marginal"]["correct"] == 780
    assert sweep["slab_only"]["correct"] == 257
    text = _md("RESULTS_A.md")
    for n in ("1042/1042", "780/1042", "636/1042", "471/1042", "257/1042"):
        assert n in text, n


def test_project_a_determinism_matches_docs(a_measure):
    rows = {r["implementation"]: r for r in a_measure["determinism"]}
    assert len(rows) == 3
    for r in rows.values():
        assert r["repeats"] == 10
    assert "10 repeats" in _md("RESULTS_A.md") or "x10" in _md("RESULTS_A.md")


def test_official_calculator_agreement_matches_docs():
    with open(A_CHECKS, encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    assert len(rows) >= 30
    # Every row must reproduce the portal's own basic model exactly — no OR that
    # lets a wrong statutory answer pass by matching the other model.
    assert all(r["agrees"] for r in rows), "a row does not reproduce the official calculator"
    assert all(r["match_portal_old"] and r["match_portal_new"] for r in rows)
    # And the statutory answer must match the portal everywhere except the
    # marginal-relief boundaries the basic calculator does not apply.
    assert all(r["statutory_matches_portal"] for r in rows), \
        "statutory answer diverges from the portal away from a marginal-relief boundary"
    # The two regimes of the portal (old/new) must both be reproduced; the
    # marginal-relief boundaries must include the 87A new-regime edge, which is
    # the one the old code silently got wrong.
    assert 1200010 in {r["income"] for r in rows if r["at_marginal_relief_boundary"]}
    statutory = sum(1 for r in rows if r["match_statutory_old"] and r["match_statutory_new"])
    basic = sum(1 for r in rows if r["match_basic_old"] and r["match_basic_new"])
    text = _md("RESULTS_A.md")
    assert f"{statutory}/30" in text, statutory
    assert f"{basic}/30" in text, basic


def test_old_regime_87a_cliff_matches_the_official_calculator():
    """The specific defect the review caught: the old-regime s.87A rebate is a
    plain cliff. At 5,00,010 the portal charges the full slab tax (13,002 to the
    rupee) and so must we (13,000 to the nearest ten) — the engine must not apply
    the new regime's marginal-relief proviso here."""
    with open(A_CHECKS, encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    row = next(r for r in rows if r["income"] == 500010)
    assert row["official_old"] == 13002.0, row["official_old"]
    assert row["ours_old"] == 13000.0, row["ours_old"]
    assert row["statutory_matches_portal"], "old-regime 87A must be a cliff, not marginal relief"
    # And the pre-fix bug would have produced a near-zero answer here.
    assert row["ours_old"] > 1000, "the old-regime cliff must charge the full slab tax"



def test_spec_diff_delta_count_matches_docs():
    with open(A_SPECDIFF, encoding="utf-8") as fh:
        deltas = json.load(fh)["deltas"]
    assert len(deltas) == 3, "RESULTS_A.md says 3 substantive deltas"
    assert "3 substantive deltas" in _md("RESULTS_A.md")


def test_project_b_profile_counts_match_docs(b_profile):
    text = _md("RESULTS_B.md")
    for key, count in (("column_type_change", 141827), ("null_spike", 16352),
                       ("resolution_status_contradiction", 7800),
                       ("near_duplicates", 4301), ("exact_duplicates", 831),
                       ("pii_in_text", 211), ("closed_before_created", 188),
                       ("zip_borough_mismatch", 49)):
        assert b_profile["findings"][key]["count"] == count, key
        assert f"{count:,}" in text, f"{key}={count} not in RESULTS_B.md"


def test_project_b_detector_table_matches_docs(b_measure):
    by: dict[tuple[str, str], dict] = {}
    for r in b_measure["detectors"]:
        by[(r["problem"], r["detector"])] = r
    # The claims the decision matrix leans on hardest.
    assert by[("resolution_status_contradiction", "rules")]["detection_rate"] == 0.0
    assert by[("resolution_status_contradiction", "statistical")]["detection_rate"] == 0.0
    assert by[("resolution_status_contradiction", "llm")]["detection_rate"] == 1.0
    assert by[("complaint_descriptor_mismatch", "statistical")]["detection_rate"] >= 0.99
    assert abs(by[("complaint_descriptor_mismatch", "llm")]["detection_rate"] - 0.6492) < 0.01
    text = _md("RESULTS_B.md")
    assert "99% / 0.07%" in text
    assert "65% / 0.00%" in text


def test_project_b_hybrid_throughput_is_per_thousand_rows(b_measure):
    """The hybrid runs rules + statistics + LLM on every batch, so its cost per
    1,000 rows cannot be a thousandth of the rules family's. This guards the unit
    bug where the hybrid figure was milliseconds-per-*row* mislabelled as
    milliseconds-per-1,000-rows."""
    by = {(r["problem"], r["detector"]): r for r in b_measure["detectors"]}
    for h in b_measure["hybrid"]:
        base = by[(h["problem"], "rules")]["ms_per_1000_rows"]
        assert h["ms_per_1000_rows"] >= 0.5 * base, (
            f"{h['problem']}: hybrid {h['ms_per_1000_rows']} ms/1k is implausibly "
            f"below the rules family's {base} ms/1k")
        assert h["ms_per_1000_rows"] <= 50 * base, (h["problem"], h["ms_per_1000_rows"], base)


def test_project_b_hybrid_cost_bound_matches_docs(b_measure):
    costs = [r["cost_per_million_rows_usd"] for r in b_measure["hybrid"]]
    lo, hi = min(costs), max(costs)
    assert hi < 2.0, "the hybrid must stay under $2 per million rows"
    assert all(r["detection_rate"] >= 0.99 for r in b_measure["hybrid"])
    text = _md("RESULTS_B.md")
    # The docs must quote the artifact's own numbers, to two decimals, so a
    # change to either side breaks the build rather than drifting silently.
    assert f"{hi:.2f}" in text, f"RESULTS_B.md should quote hybrid max ${hi:.2f}/M"
    assert "0.94" in text, "RESULTS_B.md should quote the typical hybrid cost $0.94/M"


def test_project_b_llm_family_cost_matches_docs(b_measure):
    """The all-rows-to-LLM cost is the number the hybrid design exists to avoid."""
    llm_costs = [r["cost_per_million_rows_usd"] for r in b_measure["detectors"]
                 if r["detector"] == "llm"]
    assert llm_costs, "no LLM-family rows in the measurement"
    assert min(llm_costs) > 16.0, "an LLM row cost of $16+/M is the whole point"
    assert max(llm_costs) < 17.0
    text = _md("RESULTS_B.md")
    assert "16.61" in text and "16.84" in text
    assert "0.0088" not in text, "the old understated cost bound must be gone"
    assert "0.79" not in text, "the old understated cost figure must be gone"


def test_cost_formula_is_per_call_not_per_row():
    """Regression guard for the 20x cost understatement.

    Cost is (tokens per *call*) x (calls per row) x ($/token). Charging
    per-*row* tokens and then also multiplying by calls-per-row counts the rows
    twice and understates the bill by the chunk size.
    """
    from project_b.detectors.llm_review import cost_per_million_rows

    # One call per row, 1,000 prompt tokens at $0.10 per 1M tokens:
    # 1,000/1e6 x $0.10 = $0.0001 per row -> $100 per million rows.
    assert abs(cost_per_million_rows(1.0, 1000.0, 0.0) - 100.0) < 1e-9
    # Same call, but only 5% of rows reach it -> one twentieth of the cost.
    assert abs(cost_per_million_rows(0.05, 1000.0, 0.0) - 5.0) < 1e-9
    # Cost is linear in calls per row.
    assert abs(cost_per_million_rows(0.02, 1000.0, 0.0)
               - 2 * cost_per_million_rows(0.01, 1000.0, 0.0)) < 1e-12
    # Completion tokens are priced 4x prompt tokens at the default prices.
    assert cost_per_million_rows(1.0, 0.0, 1000.0) == 4 * cost_per_million_rows(1.0, 1000.0, 0.0)
    # The old bug: passing per-row tokens *and* calls-per-row double-counted the
    # rows. Here the correct per-call cost is $100/M; the buggy signature (1000
    # tokens per row, x1 call) happened to agree, but with 20 rows/call the buggy
    # form charged 20x too little per row of context.
    correct = cost_per_million_rows(0.05, 1000.0, 0.0)   # $5/M
    buggy = cost_per_million_rows(0.05, 1000.0 / 20, 0.0)  # $0.25/M
    assert abs(correct / buggy - 20.0) < 1e-9, "the chunk-size factor must be 20x"


def test_llm_prompt_tokens_charge_the_system_prompt_per_call():
    """The system prompt is re-sent on every request, so it must be charged
    once per call. Charging it once per row understates a 20-row call."""
    from project_b.detectors.llm_review import LLMDetector
    from project_b.detectors.prompts import DATACHECK_SYSTEM
    from project_b.injectors.inject import Batch

    rows = [{"complaint_type": "Noise - Residential", "descriptor": "Loud Music/Party",
             "status": "Closed", "resolution_description": "The case was closed."}
            for _ in range(50)]
    det = LLMDetector(proxy=True, sample_rate=1.0, batch_size=20)
    d = det.detect(Batch(rows=rows))
    system_tokens = len(DATACHECK_SYSTEM) // 4
    assert d.llm_calls == 3, f"50 rows at 20/call should be 3 calls, got {d.llm_calls}"
    assert d.prompt_tokens >= d.llm_calls * system_tokens, (
        "system prompt must be charged on every call, not once per batch")


MATRIX_NAMES = {
    "renamed_or_dropped_column": "Renamed or dropped column",
    "column_type_change": "Column type change",
    "date_format_switch": "Date format switch",
    "latlon_swapped": "Latitude/longitude swapped",
    "exact_duplicates": "Exact duplicates",
    "near_duplicates": "Near-duplicates",
    "null_spike": "Null spike",
    "closed_before_created": "Closed before created",
    "zip_borough_mismatch": "ZIP inconsistent with borough",
    "complaint_descriptor_mismatch": "Complaint type vs Descriptor",
    "resolution_status_contradiction": "Resolution text vs Status",
    "pii_in_text": "Personal data in free text",
}


def test_decision_matrix_covers_every_problem(b_measure):
    """Each of the twelve problems must have its own row in the matrix."""
    text = _md("DECISION_MATRIX.md")
    problems = {r["problem"] for r in b_measure["detectors"]}
    assert problems == set(MATRIX_NAMES), "matrix mapping is out of date"
    for problem, display in MATRIX_NAMES.items():
        assert display in text, f"{problem} has no row in DECISION_MATRIX.md"
    assert re.search(r"\b(rule|statistical|LLM|hybrid)\b", text)


def test_every_component_names_a_family():
    """No decision-matrix row may be left without a chosen family."""
    # Only the decision tables, not the cross-cutting comparison table below.
    text = _md("DECISION_MATRIX.md").split("## Cross-cutting")[0]
    families = ("rule", "statistical", "LLM", "hybrid")
    for line in text.splitlines():
        if line.startswith("|") and "---" not in line and "Decision" not in line \
                and "Axis" not in line and "| Rules |" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0]:
                assert any(f in cells[1] for f in families), line
