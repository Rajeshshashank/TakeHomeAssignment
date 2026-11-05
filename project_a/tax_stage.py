"""Tax-stage helper: tax on a *given* total income.

The Income Tax Department's public calculator takes total taxable income (per
regime) and returns tax. To cross-check our engine against it we need to
evaluate the same stage in isolation — slabs, surcharge, s.87A rebate, cess —
without re-deriving taxable income from salary. This module exposes exactly
that, and is used by scripts/verify_official_calculator.py.
"""
from __future__ import annotations

from .engine import _brackets_for, round_to_ten, slab_tax, surcharge_with_relief
from .spec import load_spec


def tax_on_total_income(total_income: float, regime: str, fy: str = "2024-25", age: int = 30,
                        marginal_relief: bool = True, rounding: str = "ten") -> dict:
    """Return the statutory tax payable on a given total income.

    ``marginal_relief`` and ``rounding`` exist so the engine can be compared
    with the Income Tax Department's *basic* calculator, which (per the warning
    on that page) omits marginal relief and floors the result to the rupee.
    The default is the full statutory computation: marginal relief on, rounding
    of total income and tax to the nearest ten rupees (ss.288A / 288B).
    """
    spec = load_spec(fy)
    ti = round_to_ten(float(total_income)) if rounding == "ten" else float(total_income)
    brackets = _brackets_for(spec, regime, age)
    tax, rows = slab_tax(ti, brackets)

    if marginal_relief:
        surcharge, relief, _ = surcharge_with_relief(tax, ti, spec, regime, brackets)
    else:
        rate = 0.0
        for band in spec["surcharge"]["bands"]:
            if ti > band["above"]:
                rate = band["rate"]
        if regime == "new":
            rate = min(rate, spec["surcharge"]["new_regime_max_rate"])
        surcharge, relief = tax * rate, 0.0

    rebate_cfg = spec["rebate_87a"][regime]
    thr = float(rebate_cfg["threshold_total_income"])
    cap = float(rebate_cfg["max_rebate"])
    if ti <= thr:
        rebate = min(tax + surcharge, cap)
    elif marginal_relief and rebate_cfg.get("marginal_relief"):
        rebate = max(0.0, (tax + surcharge) - (ti - thr))
    else:
        rebate = 0.0

    cess = (tax + surcharge - rebate) * float(spec["cess_rate"]["value"])
    raw = max(0.0, tax + surcharge - rebate + cess)
    total = round_to_ten(raw) if rounding == "ten" else float(int(raw))
    return {
        "total_income": ti,
        "regime": regime,
        "fy": fy,
        "slab_tax": tax,
        "slab_breakdown": rows,
        "surcharge": surcharge,
        "surcharge_marginal_relief": relief,
        "rebate_87a": rebate,
        "cess": cess,
        "total_tax": total,
    }
