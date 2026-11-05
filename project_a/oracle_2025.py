"""FY 2025-26 oracle: the FY 2024-25 oracle with the Finance Act 2025 deltas.

Kept separate so the "how hard was the change?" question (Task 5) has a
concrete, diffable answer: the whole FY 2025-26 rule set is these constants.
"""
from __future__ import annotations

from typing import Any

from .oracle import _marginal_sur, _r10, _tax

NEW_25 = [(400000, 0.00), (800000, 0.05), (1200000, 0.10), (1600000, 0.15), (2000000, 0.20), (2400000, 0.25), (None, 0.30)]
OLD_25 = [(250000, 0.00), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SENIOR_25 = [(300000, 0.00), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SUPER_25 = [(500000, 0.00), (1000000, 0.20), (None, 0.30)]


def compute_oracle_2025(profile: dict[str, Any]) -> dict[str, float]:
    age = int(profile.get("age", 30))
    gross = float(profile.get("gross_salary", 0) or 0)
    basic = profile.get("basic_salary")
    da = float(profile.get("dearness_allowance", 0) or 0)
    salary_basis = (float(basic) + da) if basic is not None else gross

    out: dict[str, float] = {}
    for regime in ("old", "new"):
        if regime == "old":
            bands = OLD_SUPER_25 if age >= 80 else OLD_SENIOR_25 if age >= 60 else OLD_25
            std = 50000.0
        else:
            bands = NEW_25
            std = 100000.0

        hra = 0.0
        if regime == "old" and profile.get("hra_received") and profile.get("rent_paid"):
            pct = 0.50 if profile.get("is_metro") else 0.40
            hra = max(0.0, min(
                float(profile["hra_received"]),
                max(0.0, float(profile["rent_paid"]) - 0.10 * salary_basis),
                pct * salary_basis,
            ))
        ptax = min(float(profile.get("professional_tax", 0) or 0), 2500.0) if regime == "old" else 0.0
        sal = max(0.0, gross - hra - min(gross, std) - ptax)

        hli = 0.0
        if regime == "old" and profile.get("house_is_self_occupied", True):
            hli = min(float(profile.get("home_loan_interest", 0) or 0), 200000.0)
        gti = max(0.0, sal - hli)

        via = 0.0
        if regime == "old":
            via += min(float(profile.get("sec_80c", 0) or 0), 150000.0)
            via += min(float(profile.get("sec_80ccd_1b", 0) or 0), 50000.0)
            via += min(float(profile.get("sec_80d_self", 0) or 0), 50000.0 if age >= 60 else 25000.0)
            par = 50000.0 if profile.get("parents_are_senior") else 25000.0
            via += min(float(profile.get("sec_80d_parents", 0) or 0), par)
            via = min(via, gti)

        ti = _r10(max(0.0, gti - via))
        tax = _tax(ti, bands)
        sur, _relief = _marginal_sur(ti, tax, bands, regime)

        if regime == "old":
            thr, cap = 500000.0, 12500.0
        else:
            thr, cap = 1200000.0, 60000.0
        if ti <= thr:
            reb = min(tax + sur, cap)
        else:
            reb = max(0.0, (tax + sur) - (ti - thr))
        cess = (tax + sur - reb) * 0.04
        out[regime] = _r10(max(0.0, tax + sur - reb + cess))
    return out
