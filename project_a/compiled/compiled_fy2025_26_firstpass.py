"""First-pass LLM compilation for FY 2025-26 — KEPT AS A FIXTURE.

This is the un-reviewed output of the compile_rules() prompt for the FY 2025-26
spec. It updates the new-regime slabs and the s.87A rebate threshold, but
silently misses the s.87A new-regime marginal-relief proviso: above the rebate
threshold it charges the full slab tax (62,400 at 12,00,010) instead of roughly
the excess over the threshold (about 10 rupees).

The failure is *silent*: the module returns a plausible rupee number with no
error. project_a/tests/test_task5.py demonstrates that the test corpus catches
it, which is the point of Task 5. The Finance Act 2025 did not change the
new-regime standard deduction (it stays at 75,000), so that is not a delta here.
"""
from __future__ import annotations

from .compiled_fy2025_26 import (  # noqa: F401  (slabs are correct in this pass)
    NEW, OLD, OLD_SENIOR, OLD_SUPER, SUR, _r10, _slab, _surcharge, _taxable as _taxable_ref,
)

STD_NEW = 75000.0


def _taxable(profile, regime):
    gross = float(profile.get("gross_salary", 0) or 0)
    basic = profile.get("basic_salary")
    da = float(profile.get("dearness_allowance", 0) or 0)
    basis = (float(basic) + da) if basic is not None else gross
    age = int(profile.get("age", 30))
    std = 50000.0 if regime == "old" else STD_NEW
    hra = 0.0
    if regime == "old" and profile.get("hra_received") and profile.get("rent_paid"):
        pct = 0.50 if profile.get("is_metro") else 0.40
        hra = max(0.0, min(float(profile["hra_received"]),
                           max(0.0, float(profile["rent_paid"]) - 0.10 * basis), pct * basis))
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
    return _r10(max(0.0, gti - via))


def compute(profile, fy="2025-26"):
    out = {}
    age = int(profile.get("age", 30))
    for regime in ("old", "new"):
        if regime == "old":
            bands = OLD_SUPER if age >= 80 else OLD_SENIOR if age >= 60 else OLD
        else:
            bands = NEW
        ti = _taxable(profile, regime)
        tax = _slab(ti, bands)
        sur = _surcharge(ti, tax, bands, regime)
        if regime == "old":
            thr, cap = 500000.0, 12500.0
        else:
            thr, cap = 1200000.0, 60000.0
        if ti <= thr:
            reb = min(tax + sur, cap)
        else:
            # BUG: the new-regime marginal-relief proviso is applied only within
            # one rupee of the threshold, so a taxpayer ten rupees over 12,00,000
            # is charged the full slab tax (62,400) instead of about 10 rupees.
            reb = max(0.0, (tax + sur) - (ti - thr)) if regime == "new" and ti <= thr + 1 else 0.0
        cess = (tax + sur - reb) * 0.04
        out[regime] = _r10(max(0.0, tax + sur - reb + cess))
    return out
