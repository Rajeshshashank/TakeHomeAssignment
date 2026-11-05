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
                        marginal_relief: bool = True, rounding: str = "ten",
                        surcharge_relief: str = "statutory") -> dict:
    """Return the tax payable on a given total income.

    Three knobs exist so the engine can be compared, line for line, with the
    Income Tax Department's *basic* calculator:

    ``marginal_relief``
        Whether the s.87A marginal-relief proviso applies (new regime only; the
        spec already encodes this, so passing ``False`` reproduces a model that
        omits it entirely).
    ``rounding``
        ``"ten"`` is statutory (ss.288A/288B, nearest ten rupees); ``"rupee"``
        floors to the rupee, which is what the portal's basic model does.
    ``surcharge_relief``
        ``"statutory"`` applies surcharge marginal relief at every crossed
        threshold; ``"first_band"`` applies it only at the first (50,00,000)
        threshold, which is the behaviour the portal's basic model exhibits;
        ``"none"`` omits it.

    The default is the full statutory computation: marginal relief on, both
    total income and tax rounded to the nearest ten rupees (ss.288A / 288B).
    """
    spec = load_spec(fy)
    ti = round_to_ten(float(total_income)) if rounding == "ten" else float(total_income)
    brackets = _brackets_for(spec, regime, age)
    tax, rows = slab_tax(ti, brackets)

    if marginal_relief and surcharge_relief == "statutory":
        surcharge, relief, _ = surcharge_with_relief(tax, ti, spec, regime, brackets)
    else:
        rate = 0.0
        for band in spec["surcharge"]["bands"]:
            if ti > band["above"]:
                rate = band["rate"]
        if regime == "new":
            rate = min(rate, spec["surcharge"]["new_regime_max_rate"])
        surcharge, relief = tax * rate, 0.0
        if surcharge_relief == "first_band" and rate > 0:
            first = spec["surcharge"]["bands"][0]["above"]
            if ti > first:
                tax_at = slab_tax(first, brackets)[0]
                rate_at = 0.0
                for b in spec["surcharge"]["bands"]:
                    if first >= b["above"] and first > b["above"]:
                        rate_at = b["rate"]
                if regime == "new":
                    rate_at = min(rate_at, spec["surcharge"]["new_regime_max_rate"])
                cap = tax_at + tax_at * rate_at + (ti - first)
                if tax + surcharge > cap:
                    relief = (tax + surcharge) - cap
                    surcharge -= relief

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
