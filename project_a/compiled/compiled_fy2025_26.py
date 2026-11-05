"""LLM-compiled rules for FY 2025-26 (Implementation C artifact, reviewed).

The Finance Act 2025 deltas vs FY 2024-25 are: new-regime slabs re-based
(rebate-free to 4,00,000; 30% above 24,00,000) and the s.87A new-regime rebate
threshold raised to 12,00,000 with the cap at 60,000. The new-regime standard
deduction is *unchanged* at 75,000. See compiled_fy2025_26_firstpass.py for the
first LLM attempt and the bug the test suite caught in it.
"""

OLD = [(250000, 0.0), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SENIOR = [(300000, 0.0), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SUPER = [(500000, 0.0), (1000000, 0.20), (None, 0.30)]
NEW = [(400000, 0.0), (800000, 0.05), (1200000, 0.10), (1600000, 0.15),
       (2000000, 0.20), (2400000, 0.25), (None, 0.30)]
SUR = [(5000000, 0.10), (10000000, 0.15), (20000000, 0.25), (50000000, 0.37)]
STD_NEW = 75000.0


def _r10(x):
    return float(int(x / 10 + 0.5) * 10)


def _slab(income, bands):
    tax, lo = 0.0, 0
    for hi, rate in bands:
        if hi is None:
            tax += max(0.0, income - lo) * rate
            break
        tax += max(0.0, min(income, hi) - lo) * rate
        lo = hi
    return tax


def _surcharge(income, tax, bands, regime):
    rate = 0.0
    for hi, r in SUR:
        if income > hi:
            rate = r
    if regime == "new":
        rate = min(rate, 0.25)
    sur = tax * rate
    relief = 0.0
    if rate > 0:
        cap = None
        for hi, _ in SUR:
            if income <= hi:
                continue
            t = _slab(hi, bands)
            r_at = 0.0
            for h2, r2 in SUR:
                if hi >= h2 and hi > h2:
                    r_at = r2
            if regime == "new":
                r_at = min(r_at, 0.25)
            c = t + t * r_at + (income - hi)
            cap = c if cap is None else min(cap, c)
        if cap is not None and tax + sur > cap:
            relief = (tax + sur) - cap
    return sur - relief


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
                           max(0.0, float(profile["rent_paid"]) - 0.10 * basis),
                           pct * basis))
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
            thr, cap, mr = 500000.0, 12500.0, False
        else:
            thr, cap, mr = 1200000.0, 60000.0, True
        if ti <= thr:
            reb = min(tax + sur, cap)
        elif mr:
            reb = max(0.0, (tax + sur) - (ti - thr))
        else:
            reb = 0.0
        cess = (tax + sur - reb) * 0.04
        out[regime] = _r10(max(0.0, tax + sur - reb + cess))
    return out
