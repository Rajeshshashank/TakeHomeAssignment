"""Project A test suite: the YAML engine must reproduce the frozen corpus.

Run with ``pytest project_a/tests -q``. The corpus was frozen from the
independent oracle in :mod:`project_a.oracle`, so a pass means the rules
engine agrees rupee-for-rupee with a second implementation of the statute.
"""
from __future__ import annotations

import json
import os

import pytest

from project_a import TaxpayerProfile, compute

CASES = os.path.join(os.path.dirname(__file__), "cases", "cases.json")


def load_corpus():
    with open(CASES, encoding="utf-8") as fh:
        return json.load(fh)


CORPUS = load_corpus()
CASES_LIST = CORPUS["cases"]
IDS = [c["id"] for c in CASES_LIST]


def test_corpus_size():
    assert len(CASES_LIST) >= 200, "assignment requires at least 200 test cases"


@pytest.mark.parametrize("case", CASES_LIST, ids=IDS)
def test_engine_matches_frozen_oracle(case):
    profile = TaxpayerProfile.from_dict(case["profile"])
    for fy, expected in case["expected"].items():
        result = compute(profile, fy=fy)
        assert result.old.total_tax == pytest.approx(expected["old"], abs=0.5), (
            f"{case['id']} fy={fy} old regime"
        )
        assert result.new.total_tax == pytest.approx(expected["new"], abs=0.5), (
            f"{case['id']} fy={fy} new regime"
        )


def test_best_regime_is_the_cheaper_one():
    for case in CASES_LIST:
        profile = TaxpayerProfile.from_dict(case["profile"])
        for fy in case["expected"]:
            r = compute(profile, fy=fy)
            if r.old.total_tax < r.new.total_tax:
                assert r.best_regime == "old"
            elif r.new.total_tax < r.old.total_tax:
                assert r.best_regime == "new"
            else:
                assert r.best_regime in ("either", "old", "new")


def test_every_rupee_has_a_citation():
    """Task 4: every rupee in the answer must trace to a specific clause."""
    profile = TaxpayerProfile(gross_salary=2000000, basic_salary=800000,
                              hra_received=400000, rent_paid=480000, is_metro=True,
                              sec_80c=150000, sec_80ccd_1b=50000, sec_80d_self=25000,
                              home_loan_interest=200000, professional_tax=2500)
    for fy in ("2024-25", "2025-26"):
        result = compute(profile, fy=fy)
        for regime in (result.old, result.new):
            assert regime.trace, "trace must not be empty"
            for item in regime.trace:
                assert item.cite, f"{regime.regime}: '{item.label}' has no citation"
                assert item.cite.strip() != ""


def test_determinism_repeated_runs():
    """Same input ten times must give the identical rupee value."""
    profile = TaxpayerProfile(gross_salary=1275000, sec_80c=150000)
    for fy in ("2024-25", "2025-26"):
        values = {compute(profile, fy=fy).new.total_tax for _ in range(10)}
        assert len(values) == 1, f"non-deterministic result: {values}"


def test_new_regime_ignores_old_regime_deductions():
    """s.115BAC disallows 80C etc. in the new regime."""
    plain = compute(TaxpayerProfile(gross_salary=1500000), fy="2024-25").new.total_tax
    loaded = compute(TaxpayerProfile(gross_salary=1500000, sec_80c=150000,
                                     sec_80d_self=25000, home_loan_interest=200000),
                     fy="2024-25").new.total_tax
    assert plain == loaded


def test_fy2025_26_change_reduces_new_regime_tax():
    """Finance Act 2025 widened the new-regime slabs and rebate."""
    profile = TaxpayerProfile(gross_salary=1200000)
    old_fy = compute(profile, fy="2024-25").new.total_tax
    new_fy = compute(profile, fy="2025-26").new.total_tax
    assert new_fy < old_fy


def test_old_regime_unchanged_between_fys():
    """The Finance Act 2025 changes touched only the new regime."""
    profile = TaxpayerProfile(gross_salary=1500000, sec_80c=150000)
    assert compute(profile, fy="2024-25").old.total_tax == compute(profile, fy="2025-26").old.total_tax
