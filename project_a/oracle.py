"""Independent tax oracle used to cross-check the YAML-driven engine.

This file deliberately shares no code with :mod:`project_a.engine`. It
hard-codes the FY 2024-25 statutory numbers and uses a different algorithm
(accumulate tax band-by-band with explicit ``for`` loops and integer paise
handling) so that agreement between the two is real evidence, not a tautology.
The test corpus freezes this oracle's output; the engine is then required to
reproduce it to the rupee.

Only FY 2024-25 is hard-coded here. FY 2025-26 cross-checks are produced by
:mod:`project_a.oracle_2025`, which applies the Finance Act 2025 deltas.
"""
from __future__ import annotations

from typing import Any

# --- FY 2024-25, hard-coded from the Income Tax Department's published rates ---
OLD_24 = [(250000, 0.00), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SENIOR_24 = [(300000, 0.00), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SUPER_24 = [(500000, 0.00), (1000000, 0.20), (None, 0.30)]
NEW_24 = [(300000, 0.00), (700000, 0.05), (1000000, 0.10), (1200000, 0.15), (1500000, 0.20), (None, 0.30)]
SUR_24 = [(5000000, 0.10), (10000000, 0.15), (20000000, 0.25), (50000000, 0.37)]


def _r10(x: float) -> float:
    """Round to nearest 10, half up (ss.288A / 288B)."""
    return float(int(x / 10 + 0.5) * 10)


def _tax(income: float, bands: list[tuple]) -> float:
    total = 0.0
    lo = 0
    for hi, rate in bands:
        if hi is None:
            total += max(0.0, income - lo) * rate
            break
        total += max(0.0, min(income, hi) - lo) * rate
        lo = hi
    return total


def _sur(income: float, regime: str) -> float:
    r = 0.0
    for hi, rate in SUR_24:
        if income > hi:
            r = rate
    return min(r, 0.25) if regime == "new" else r


def _marginal_sur(income: float, tax: float, bands: list[tuple], regime: str) -> tuple[float, float]:
    rate = _sur(income, regime)
    sur = tax * rate
    relief = 0.0
    if rate > 0:
        cap = None
        for hi, _ in SUR_24:
            if income <= hi:
                continue
            tax_at = _tax(hi, bands)
            rate_at = 0.0
            for h2, r2 in SUR_24:
                if hi >= h2 and hi > h2:
                    rate_at = r2
            if regime == "new":
                rate_at = min(rate_at, 0.25)
            c = tax_at + tax_at * rate_at + (income - hi)
            cap = c if cap is None else min(cap, c)
        if cap is not None and tax + sur > cap:
            relief = (tax + sur) - cap
    return sur - relief, relief


def compute_oracle(profile: dict[str, Any], fy: str = "2024-25") -> dict[str, float]:
    """Return {'old': tax, 'new': tax} for a profile dict, rupee-exact."""
    if fy == "2025-26":
        from .oracle_2025 import compute_oracle_2025

        return compute_oracle_2025(profile)

    age = int(profile.get("age", 30))
    gross = float(profile.get("gross_salary", 0) or 0)
    basic = profile.get("basic_salary")
    da = float(profile.get("dearness_allowance", 0) or 0)
    salary_basis = (float(basic) + da) if basic is not None else gross

    out: dict[str, float] = {}
    for regime in ("old", "new"):
        if regime == "old":
            bands = OLD_SUPER_24 if age >= 80 else OLD_SENIOR_24 if age >= 60 else OLD_24
            std = 50000.0
        else:
            bands = NEW_24
            std = 75000.0

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

        # s.87A rebate
        if regime == "old":
            thr, cap = 500000.0, 12500.0
        else:
            thr, cap = 700000.0, 25000.0
        if ti <= thr:
            reb = min(tax + sur, cap)
        else:
            reb = max(0.0, (tax + sur) - (ti - thr))
        cess = (tax + sur - reb) * 0.04
        out[regime] = _r10(max(0.0, tax + sur - reb + cess))
    return out
