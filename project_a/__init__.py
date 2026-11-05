"""Project A: Indian income-tax engine for salaried individuals.

Public entry point:

    from project_a import compute, load_spec
    result = compute(profile, fy="2024-25")
    result.best_regime, result.old.total_tax, result.new.total_tax
"""
from .engine import RegimeResult, TaxResult, compute
from .models import TaxpayerProfile
from .spec import available_fys, load_spec

__all__ = [
    "compute",
    "load_spec",
    "available_fys",
    "TaxpayerProfile",
    "TaxResult",
    "RegimeResult",
]
