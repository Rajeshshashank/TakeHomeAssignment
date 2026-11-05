"""Task 5: the FY 2025-26 change, and the silent failures it produced.

Two artifacts are compared against the frozen corpus:

* ``compiled_fy2025_26_firstpass`` — the un-reviewed output of the one-time LLM
  compilation. It updates the slabs and the rebate threshold but misses the
  standard-deduction change and mis-applies the s.87A marginal-relief proviso.
* ``compiled_fy2025_26`` — the reviewed module, committed after the test corpus
  caught both defects.

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
    the only thing that notices. ~31% of regime-answers are wrong."""
    wrong = _wrong(firstpass)
    assert wrong > 200, "expected a large silent-error count from the first pass"


def test_firstpass_standard_deduction_defect_is_isolated():
    """BUG 1: new-regime standard deduction left at 75,000 instead of 1,00,000.
    A plain salary case at 15,00,000 shows the 25,000 under-deduction."""
    profile = {"gross_salary": 1500000, "age": 30}
    got = firstpass.compute(profile, "2025-26")["new"]
    want = reviewed.compute(profile, "2025-26")["new"]
    assert got != want
    assert got > want, "under-deducting standard deduction must over-tax"


def test_firstpass_marginal_relief_defect_is_isolated():
    """BUG 2: without the s.87A marginal-relief proviso, a taxpayer ten rupees
    over the 12,00,000 rebate threshold is charged the full slab tax instead of
    roughly the excess. The reviewed module charges about 10 rupees; the first
    pass charges 66,300."""
    profile = {"gross_salary": 1300010, "age": 30}
    got = firstpass.compute(profile, "2025-26")["new"]
    want = reviewed.compute(profile, "2025-26")["new"]
    assert want <= 20, "statutory marginal relief caps the tax near the excess"
    assert got > 1000, "the unreviewed pass ignores the proviso"
