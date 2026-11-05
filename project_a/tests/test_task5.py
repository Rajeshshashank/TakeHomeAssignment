"""Task 5: the FY 2025-26 change, and the silent failure it produced.

Two artifacts are compared against the frozen corpus:

* ``compiled_fy2025_26_firstpass`` — the un-reviewed output of the one-time LLM
  compilation. It updates the slabs and the rebate threshold but mis-applies the
  s.87A new-regime marginal-relief proviso.
* ``compiled_fy2025_26`` — the reviewed module, committed after the test corpus
  caught the defect.

Neither raises. Both return a plausible rupee number. That is what "silent
failure" means here, and these tests are the thing that caught it.
"""
from __future__ import annotations

import json
import os

import pytest

from project_a.compiled import compiled_fy2025_26 as reviewed
from project_a.compiled import compiled_fy2025_26_firstpass as firstpass

CASES = os.path.join(os.path.dirname(__file__), "cases", "cases.json")


def _cases():
    with open(CASES, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def _wrong(module) -> int:
    wrong = 0
    for case in _cases():
        expected = case["expected"]["2025-26"]
        got = module.compute(case["profile"], "2025-26")
        for regime in ("old", "new"):
            if abs(got[regime] - expected[regime]) > 0.5:
                wrong += 1
    return wrong


def test_reviewed_module_is_rupee_exact_on_the_corpus():
    assert _wrong(reviewed) == 0


def test_firstpass_fails_silently_on_the_corpus():
    """The unreviewed compilation is wrong but never raises: the test suite is
    the only thing that notices."""
    wrong = _wrong(firstpass)
    assert wrong > 5, "expected a non-trivial silent-error count from the first pass"


def test_firstpass_marginal_relief_defect_is_isolated():
    """The single defect: without the s.87A marginal-relief proviso, a taxpayer
    ten rupees over the 12,00,000 rebate threshold is charged the full slab tax
    instead of roughly the excess. The reviewed module charges about 10 rupees;
    the first pass charges 62,400."""
    profile = {"gross_salary": 1275010, "age": 30}  # new-regime TI = 12,00,010
    got = firstpass.compute(profile, "2025-26")["new"]
    want = reviewed.compute(profile, "2025-26")["new"]
    assert want <= 20, "statutory marginal relief caps the tax near the excess"
    assert got > 1000, "the unreviewed pass ignores the proviso"


def test_firstpass_standard_deduction_matches_the_reviewed_module():
    """The FY 2025-26 new-regime standard deduction did not change (75,000), so
    the two modules must agree on a plain salary case."""
    profile = {"gross_salary": 1500000, "age": 30}
    assert (firstpass.compute(profile, "2025-26")["new"]
            == reviewed.compute(profile, "2025-26")["new"])
