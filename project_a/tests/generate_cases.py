"""Generate the Project A test corpus.

Emits ``project_a/tests/cases/cases.json``: 200+ structured cases with the
expected tax under both regimes for both financial years. Expected values are
frozen from the independent oracle (:mod:`project_a.oracle`), so the frozen
corpus is genuine evidence that the YAML-driven engine matches a second
implementation written from the statute.

The corpus is deliberately built around thresholds, not sampled at random, so
the boundary behaviour that Budget changes tend to break is pinned down.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..models import TaxpayerProfile
from ..oracle import compute_oracle

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "cases", "cases.json")
FYS = ["2024-25", "2025-26"]

# Thresholds that matter, per regime / FY. The generator probes each one with
# offsets of -11, -10, -1, 0, +1, +10, +11 to catch rounding and marginal-relief
# off-by-ten bugs as well as plain off-by-one slab bugs.
OLD_THRESHOLDS = [250000, 300000, 500000, 1000000, 5000000, 10000000, 20000000, 50000000]
NEW_THRESHOLDS = {
    "2024-25": [300000, 700000, 1000000, 1200000, 1500000, 5000000, 10000000, 20000000, 50000000],
    "2025-26": [400000, 800000, 1200000, 1600000, 2000000, 2400000, 5000000, 10000000, 20000000, 50000000],
}
OFFSETS = [-11, -10, -1, 0, 1, 10, 11]


def _base(income: float, **kw: Any) -> dict[str, Any]:
    p = dict(
        age=30, gross_salary=float(income), basic_salary=None, dearness_allowance=0.0,
        hra_received=0.0, rent_paid=0.0, is_metro=False, professional_tax=0.0,
        sec_80c=0.0, sec_80ccd_1b=0.0, sec_80d_self=0.0, sec_80d_parents=0.0,
        parents_are_senior=False, home_loan_interest=0.0, house_is_self_occupied=True,
    )
    p.update(kw)
    return p


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(cid: str, tag: str, profile: dict[str, Any]) -> None:
        expected = {fy: compute_oracle(profile, fy=fy) for fy in FYS}
        cases.append({
            "id": cid,
            "tag": tag,
            "profile": profile,
            "expected": expected,
        })

    # 1. Threshold probes, old regime (salary chosen so gross == threshold,
    #    since no deductions are present the total income equals gross - std).
    n = 0
    for t in OLD_THRESHOLDS:
        for off in OFFSETS:
            n += 1
            add(f"old-thr-{n:03d}", f"old_threshold_{t}", _base(t + off))
    # senior / super-senior basic exemption boundaries
    for t in (300000, 500000):
        for off in (-1, 0, 1):
            n += 1
            add(f"old-sr-{n:03d}", f"old_senior_{t}", _base(t + off, age=65))
            n += 1
            add(f"old-ssr-{n:03d}", f"old_super_senior_{t}", _base(t + off, age=82))

    # 2. Threshold probes, new regime, both FYs.
    for fy in FYS:
        for t in NEW_THRESHOLDS[fy]:
            for off in OFFSETS:
                n += 1
                add(f"new-{fy}-thr-{n:03d}", f"new_{fy}_threshold_{t}", _base(t + off))

    # 3. Deduction-cap boundaries (old regime).
    for cap, field in ((150000, "sec_80c"), (50000, "sec_80ccd_1b"),
                       (25000, "sec_80d_self"), (25000, "sec_80d_parents"),
                       (200000, "home_loan_interest")):
        for off in (-1, 0, 1, 100000):
            n += 1
            add(f"cap-{field}-{n:03d}", f"cap_{field}", _base(1200000, **{field: cap + off}))

    # 4. HRA Rule 2A: each of the three limbs binding.
    hra_scenarios = [
        _base(1200000, basic_salary=600000, hra_received=300000, rent_paid=360000, is_metro=True),
        _base(1200000, basic_salary=600000, hra_received=300000, rent_paid=120000, is_metro=True),
        _base(1200000, basic_salary=600000, hra_received=100000, rent_paid=600000, is_metro=True),
        _base(1200000, basic_salary=600000, hra_received=300000, rent_paid=360000, is_metro=False),
        _base(1200000, basic_salary=600000, dearness_allowance=200000, hra_received=400000, rent_paid=500000, is_metro=True),
        _base(900000, basic_salary=450000, hra_received=180000, rent_paid=200000, is_metro=False),
    ]
    for i, p in enumerate(hra_scenarios, 1):
        add(f"hra-{i:03d}", "hra_rule_2a", p)

    # 7. Marginal-relief probes.
    #
    # The threshold probes above move *gross* salary, but standard deduction
    # (s.16(ia)) means total income lands 50k-100k below the boundary, so those
    # cases never actually exercise marginal relief. These families move gross
    # by the standard deduction first, so total income sits exactly on the
    # surcharge / rebate boundary where marginal relief is the operative rule.
    # This is the corner the official basic calculator gets wrong.
    STD = {("2024-25", "old"): 50000, ("2024-25", "new"): 75000,
           ("2025-26", "old"): 50000, ("2025-26", "new"): 100000}
    relief_offsets = [0, 1, 10, 100, 1000, 10000, 50000, 100000, 200000]
    for fy in FYS:
        for regime, thresholds in (
            ("old", [500000, 1000000, 5000000, 10000000, 20000000, 50000000]),
            ("new", NEW_THRESHOLDS[fy]),
        ):
            for t in thresholds:
                for off in relief_offsets:
                    n += 1
                    add(f"mr-{fy}-{regime}-{n:04d}", f"marginal_relief_{regime}",
                        _base(t + STD[(fy, regime)] + off))

    # 8. Combined realistic profiles (deductions + HRA + home loan).
    for i, gross in enumerate((600000, 1000000, 1500000, 2000000, 3000000, 5000000, 5000001, 10000001, 20000001), 1):
        add(f"combined-{i:03d}", "combined", _base(
            gross, basic_salary=gross * 0.4, hra_received=gross * 0.2, rent_paid=gross * 0.25,
            is_metro=(i % 2 == 0), sec_80c=150000, sec_80ccd_1b=50000,
            sec_80d_self=25000, sec_80d_parents=50000, parents_are_senior=True,
            home_loan_interest=200000, professional_tax=2500,
        ))

    # 6. Senior citizens with 80D raised cap.
    for i, gross in enumerate((800000, 1200000, 2000000), 1):
        add(f"senior-80d-{i:03d}", "senior_80d", _base(
            gross, age=65, sec_80d_self=50000, sec_80d_parents=50000, parents_are_senior=True))

    # 7. Degenerate / zero-income cases.
    add("zero-001", "degenerate", _base(0))
    add("tiny-001", "degenerate", _base(1000))
    add("std-only-001", "degenerate", _base(50000))

    return cases


def main() -> None:
    cases = build_cases()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"fys": FYS, "cases": cases}, fh, indent=1)
    tags: dict[str, int] = {}
    for c in cases:
        tags[c["tag"]] = tags.get(c["tag"], 0) + 1
    print(f"wrote {len(cases)} cases -> {OUT}")
    for k in sorted(tags):
        print(f"  {k:35s} {tags[k]}")


if __name__ == "__main__":
    main()
