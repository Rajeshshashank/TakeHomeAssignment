"""LLM-compiled rules for FY 2024-25 (Implementation C artifact).

Produced by project_a.llm.impl_c.compile_rules() from taxspec/fy2024_25.yaml,
then reviewed line by line. It is self-contained: no imports beyond the
standard library, no access to the YAML spec, so it is a genuinely independent
execution path from the hand-coded engine.
"""

OLD = [(250000, 0.0), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SENIOR = [(300000, 0.0), (500000, 0.05), (1000000, 0.20), (None, 0.30)]
OLD_SUPER = [(500000, 0.0), (1000000, 0.20), (None, 0.30)]
NEW = [(300000, 0.0), (700000, 0.05), (1000000, 0.10), (1200000, 0.15), (1500000, 0.20), (None, 0.30)]
SUR = [(5000000, 0.10), (10000000, 0.15), (20000000, 0.25), (50000000, 0.37)]


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

    std = 50000.0 if regime == "old" else 75000.0
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


def compute(profile, fy="2024-25"):
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
            thr, cap = 700000.0, 25000.0
        if ti <= thr:
            reb = min(tax + sur, cap)
        else:
            reb = max(0.0, (tax + sur) - (ti - thr))
        cess = (tax + sur - reb) * 0.04
        out[regime] = _r10(max(0.0, tax + sur - reb + cess))
    return out
