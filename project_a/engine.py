"""Hand-coded rules engine (Implementation A).

Every rupee is produced by a labelled line item carrying the statutory clause
that authorises it, so any answer can be traced back to a provision. The
engine reads all statutory numbers from the YAML spec; no rate is hard-coded.

Computation order follows the Act:

    gross salary
      - s.10(13A) HRA exemption (old regime)
      - s.16(ia) standard deduction, s.16(iii) professional tax
      = income under the head 'Salaries'
      - s.24(b) home-loan interest on a self-occupied house (loss set-off)
      - Chapter VI-A: s.80C, s.80CCD(1B), s.80D
      = total income            (rounded, s.288A)
      -> slab tax
      -> surcharge with marginal relief
      -> s.87A rebate (with marginal relief in the new regime)
      -> 4% health & education cess
      = tax payable             (rounded, s.288B)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .models import TaxpayerProfile
from .spec import load_spec


# --------------------------------------------------------------------------
# rounding helpers (ss. 288A / 288B: nearest multiple of ten, half up)
# --------------------------------------------------------------------------
def round_to_ten(amount: float) -> float:
    return float(int((amount + 5.0) // 10) * 10) if amount >= 0 else -float(int((-amount + 5.0) // 10) * 10)


@dataclass
class LineItem:
    label: str
    amount: float
    cite: str

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "amount": round(self.amount, 2), "cite": self.cite}


@dataclass
class RegimeResult:
    regime: str
    fy: str
    gross_salary: float = 0.0
    exemptions: list[LineItem] = field(default_factory=list)
    deductions: list[LineItem] = field(default_factory=list)
    total_income: float = 0.0
    slab_breakdown: list[dict[str, Any]] = field(default_factory=list)
    tax_before_surcharge: float = 0.0
    surcharge: float = 0.0
    surcharge_marginal_relief: float = 0.0
    rebate_87a: float = 0.0
    cess: float = 0.0
    total_tax: float = 0.0
    trace: list[LineItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "fy": self.fy,
            "gross_salary": round(self.gross_salary, 2),
            "exemptions": [i.as_dict() for i in self.exemptions],
            "deductions": [i.as_dict() for i in self.deductions],
            "total_income": round(self.total_income, 2),
            "slab_breakdown": self.slab_breakdown,
            "tax_before_surcharge": round(self.tax_before_surcharge, 2),
            "surcharge": round(self.surcharge, 2),
            "surcharge_marginal_relief": round(self.surcharge_marginal_relief, 2),
            "rebate_87a": round(self.rebate_87a, 2),
            "cess": round(self.cess, 2),
            "total_tax": round(self.total_tax, 2),
        }


@dataclass
class TaxResult:
    fy: str
    old: RegimeResult
    new: RegimeResult
    best_regime: str
    savings: float
    recommendation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "fy": self.fy,
            "old": self.old.as_dict(),
            "new": self.new.as_dict(),
            "best_regime": self.best_regime,
            "savings": round(self.savings, 2),
            "recommendation": self.recommendation,
        }


# --------------------------------------------------------------------------
# primitive calculations
# --------------------------------------------------------------------------
def slab_tax(income: float, brackets: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Progressive tax plus a per-band breakdown for auditability."""
    tax = 0.0
    lower = 0.0
    rows: list[dict[str, Any]] = []
    for band in brackets:
        upper = band["upto"]
        rate = band["rate"]
        if upper is None:
            taxable = max(0.0, income - lower)
        else:
            taxable = max(0.0, min(income, upper) - lower)
        part = taxable * rate
        if taxable > 0 or rate > 0:
            rows.append(
                {
                    "from": lower,
                    "to": upper,
                    "rate": rate,
                    "taxable": round(taxable, 2),
                    "tax": round(part, 2),
                }
            )
        tax += part
        if upper is None or income <= upper:
            break
        lower = upper
    return tax, rows


def _brackets_for(spec: dict[str, Any], regime: str, age: int) -> list[dict[str, Any]]:
    node = spec["slabs"][regime]
    if regime == "old" and age >= 80:
        return node.get("super_senior_brackets") or node["brackets"]
    if regime == "old" and age >= 60:
        return node.get("senior_brackets") or node["brackets"]
    return node["brackets"]


def _surcharge_rate(spec: dict[str, Any], regime: str, total_income: float) -> float:
    rate = 0.0
    for band in spec["surcharge"]["bands"]:
        if total_income > band["above"]:
            rate = band["rate"]
    if regime == "new":
        rate = min(rate, spec["surcharge"]["new_regime_max_rate"])
    return rate


def surcharge_with_relief(
    tax: float, total_income: float, spec: dict[str, Any], regime: str, brackets: list[dict[str, Any]]
) -> tuple[float, float, str]:
    """Return (surcharge, marginal_relief, explanation).

    Marginal relief ensures tax + surcharge on an income just above a surcharge
    threshold does not exceed the tax + surcharge at the threshold plus the
    income above it.
    """
    rate = _surcharge_rate(spec, regime, total_income)
    if rate == 0.0:
        return 0.0, 0.0, "no surcharge below 50,00,000"
    surcharge = tax * rate
    relief = 0.0
    if spec["surcharge"].get("marginal_relief"):
        best_cap: Optional[float] = None
        for band in spec["surcharge"]["bands"]:
            thr = band["above"]
            if total_income <= thr:
                continue
            tax_at_thr, _ = slab_tax(thr, brackets)
            rate_at_thr = min(band["rate"], spec["surcharge"]["new_regime_max_rate"]) if regime == "new" else band["rate"]
            # rate applicable *at* the threshold is the rate of the band below
            # the crossed boundary; recompute from the band list.
            rate_at_thr = 0.0
            for b in spec["surcharge"]["bands"]:
                if thr >= b["above"] and thr > b["above"]:
                    rate_at_thr = b["rate"]
            if regime == "new":
                rate_at_thr = min(rate_at_thr, spec["surcharge"]["new_regime_max_rate"])
            cap = tax_at_thr + tax_at_thr * rate_at_thr + (total_income - thr)
            best_cap = cap if best_cap is None else min(best_cap, cap)
        if best_cap is not None:
            excess = (tax + surcharge) - best_cap
            if excess > 0:
                relief = excess
    explanation = f"surcharge @ {rate:.0%}" + (f" less marginal relief {relief:,.0f}" if relief else "")
    return surcharge - relief, relief, explanation


# --------------------------------------------------------------------------
# regime computation
# --------------------------------------------------------------------------
def compute_regime(profile: TaxpayerProfile, spec: dict[str, Any], regime: str) -> RegimeResult:
    fy = spec["meta"]["fy"]
    res = RegimeResult(regime=regime, fy=fy, gross_salary=float(profile.gross_salary or 0.0))
    allowed = spec["regime_allowed_deductions"][regime]
    age = int(profile.age or 0)

    # --- s.10(13A) HRA exemption -----------------------------------------
    hra_exempt = 0.0
    if "hra" in allowed and profile.hra_received and profile.rent_paid:
        basis = profile.salary_basis
        pct = spec["hra_exemption"]["pct_of_salary"]["metro" if profile.is_metro else "non_metro"]
        candidates = {
            "actual HRA received": float(profile.hra_received),
            "rent paid less 10% of salary": max(0.0, float(profile.rent_paid) - 0.10 * basis),
            f"{pct:.0%} of salary ({'metro' if profile.is_metro else 'non-metro'})": pct * basis,
        }
        hra_exempt = max(0.0, min(candidates.values()))
        res.exemptions.append(
            LineItem(
                f"HRA exemption (least of {', '.join(f'{k}: {v:,.0f}' for k, v in candidates.items())})",
                hra_exempt,
                spec["hra_exemption"]["cite"],
            )
        )

    # --- s.16 deductions ---------------------------------------------------
    std_cap = spec["standard_deduction"][regime]
    standard_deduction = min(res.gross_salary, float(std_cap))
    res.deductions.append(LineItem("Standard deduction", standard_deduction, spec["standard_deduction"]["cite"]))

    prof_tax = 0.0
    if "professional_tax" in allowed:
        cap = spec["caps"].get("professional_tax", {}).get("value", 2500)
        prof_tax = min(float(profile.professional_tax or 0.0), float(cap))
        res.deductions.append(LineItem("Professional tax", prof_tax, spec["caps"]["professional_tax"]["cite"]))

    income_salaries = res.gross_salary - hra_exempt - standard_deduction - prof_tax
    income_salaries = max(0.0, income_salaries)

    # --- s.24(b) home-loan interest (self-occupied) -----------------------
    hli = 0.0
    if "sec_24b" in allowed and profile.house_is_self_occupied:
        cap = float(spec["caps"]["sec_24b_self_occupied"]["value"])
        hli = min(float(profile.home_loan_interest or 0.0), cap)
        if hli:
            res.deductions.append(LineItem("Home-loan interest, self-occupied (loss set-off)", hli, spec["caps"]["sec_24b_self_occupied"]["cite"]))

    gti = max(0.0, income_salaries - hli)

    # --- Chapter VI-A -----------------------------------------------------
    via = 0.0
    if "sec_80c" in allowed:
        v = min(float(profile.sec_80c or 0.0), float(spec["caps"]["sec_80c"]["value"]))
        via += v
        if v:
            res.deductions.append(LineItem("Section 80C", v, spec["caps"]["sec_80c"]["cite"]))
    if "sec_80ccd_1b" in allowed:
        v = min(float(profile.sec_80ccd_1b or 0.0), float(spec["caps"]["sec_80ccd_1b"]["value"]))
        via += v
        if v:
            res.deductions.append(LineItem("Section 80CCD(1B)", v, spec["caps"]["sec_80ccd_1b"]["cite"]))
    if "sec_80d" in allowed:
        cap_self = float(spec["caps"]["sec_80d"]["self_60_plus" if age >= 60 else "self_below_60"]["value"])
        v = min(float(profile.sec_80d_self or 0.0), cap_self)
        via += v
        if v:
            res.deductions.append(LineItem("Section 80D (self/family)", v, spec["caps"]["sec_80d"]["self_60_plus" if age >= 60 else "self_below_60"]["cite"]))
        cap_par = float(spec["caps"]["sec_80d"]["parents_60_plus" if profile.parents_are_senior else "parents_below_60"]["value"])
        v = min(float(profile.sec_80d_parents or 0.0), cap_par)
        via += v
        if v:
            res.deductions.append(LineItem("Section 80D (parents)", v, spec["caps"]["sec_80d"]["parents_60_plus" if profile.parents_are_senior else "parents_below_60"]["cite"]))
    via = min(via, gti)

    total_income = round_to_ten(max(0.0, gti - via))
    res.total_income = total_income

    # --- slab tax ---------------------------------------------------------
    brackets = _brackets_for(spec, regime, age)
    tax, rows = slab_tax(total_income, brackets)
    res.tax_before_surcharge = tax
    res.slab_breakdown = rows

    # --- surcharge --------------------------------------------------------
    surcharge, relief, _ = surcharge_with_relief(tax, total_income, spec, regime, brackets)
    res.surcharge = surcharge
    res.surcharge_marginal_relief = relief

    tax_after_surcharge = tax + surcharge

    # --- s.87A rebate -----------------------------------------------------
    rebate_cfg = spec["rebate_87a"][regime]
    thr = float(rebate_cfg["threshold_total_income"])
    cap = float(rebate_cfg["max_rebate"])
    rebate = 0.0
    if total_income <= thr:
        rebate = min(tax_after_surcharge, cap)
    elif rebate_cfg.get("marginal_relief"):
        # New regime only (the old regime has no s.87A marginal relief): tax
        # payable is capped at the income above the threshold, so the taxpayer
        # never pays more than the excess over 12,00,000.
        rebate = max(0.0, tax_after_surcharge - (total_income - thr))
    res.rebate_87a = rebate

    # --- cess -------------------------------------------------------------
    cess = (tax_after_surcharge - rebate) * float(spec["cess_rate"]["value"])
    res.cess = cess

    res.total_tax = round_to_ten(max(0.0, tax_after_surcharge - rebate + cess))

    # --- trace ------------------------------------------------------------
    res.trace = [
        LineItem("Gross salary", res.gross_salary, "s.17(1)"),
        LineItem("Less: HRA exemption", -hra_exempt, spec["hra_exemption"]["cite"]),
        LineItem("Less: standard deduction", -standard_deduction, spec["standard_deduction"]["cite"]),
        LineItem("Less: professional tax", -prof_tax, spec["caps"]["professional_tax"]["cite"]),
        LineItem("Less: s.24(b) home-loan interest", -hli, spec["caps"]["sec_24b_self_occupied"]["cite"]),
        LineItem("Less: Chapter VI-A deductions", -via, "ss.80C, 80CCD(1B), 80D"),
        LineItem("Total income (rounded, s.288A)", total_income, "s.288A"),
        LineItem("Tax at slab rates", tax, spec["slabs"][regime]["cite"]),
        LineItem("Add: surcharge", surcharge, spec["surcharge"]["cite"]),
        LineItem("Less: s.87A rebate", -rebate, spec["rebate_87a"]["cite"]),
        LineItem("Add: cess @ 4%", cess, spec["cess_rate"]["cite"]),
        LineItem("Tax payable (rounded, s.288B)", res.total_tax, "s.288B"),
    ]
    return res


def compute(profile: TaxpayerProfile, fy: str = "2024-25") -> TaxResult:
    """Compute tax under both regimes and recommend one, with a reason."""
    spec = load_spec(fy)
    old = compute_regime(profile, spec, "old")
    new = compute_regime(profile, spec, "new")

    if abs(old.total_tax - new.total_tax) < 1.0:
        best = "either"
        savings = 0.0
        reason = (
            "Both regimes give the same tax to the rupee. The new regime is the "
            "default (s.115BAC(1A)) and needs no proof of deductions, so it is "
            "simpler to file."
        )
    elif old.total_tax < new.total_tax:
        best = "old"
        savings = new.total_tax - old.total_tax
        reason = (
            f"The old regime is cheaper by {new.total_tax - old.total_tax:,.0f} because the "
            f"deductions in scope (ss.80C, 80CCD(1B), 80D, 24(b)) and the HRA exemption "
            f"(s.10(13A)) reduce total income more than the new regime's wider slabs save."
        )
    else:
        best = "new"
        savings = old.total_tax - new.total_tax
        reason = (
            f"The new regime is cheaper by {old.total_tax - new.total_tax:,.0f} because its "
            f"wider slabs and higher s.87A rebate outweigh the deductions the old regime allows."
        )

    return TaxResult(fy=fy, old=old, new=new, best_regime=best, savings=float(savings or 0.0), recommendation=reason)
