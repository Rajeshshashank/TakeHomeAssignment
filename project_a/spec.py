"""Load and validate the YAML tax specifications.

The specification is the *only* place statutory numbers live. Nothing in the
engine hard-codes a slab, cap or rate, which is what makes the FY 2025-26
change (Task 5) a data edit rather than a code change.
"""
from __future__ import annotations

import functools
import os
from typing import Any

import yaml

SPEC_DIR = os.path.join(os.path.dirname(__file__), "taxspec")


@functools.lru_cache(maxsize=None)
def load_spec(fy: str) -> dict[str, Any]:
    """Return the parsed spec for a financial year, e.g. ``"2024-25"``."""
    path = os.path.join(SPEC_DIR, f"fy{fy.replace('-', '_')}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no tax spec for FY {fy} ({path})")
    with open(path, encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)
    _validate(spec, fy)
    return spec


def available_fys() -> list[str]:
    fys = []
    for name in sorted(os.listdir(SPEC_DIR)):
        if name.startswith("fy") and name.endswith(".yaml"):
            fys.append(name[2:-5].replace("_", "-"))
    return fys


def _validate(spec: dict[str, Any], fy: str) -> None:
    required = ["meta", "slabs", "standard_deduction", "rebate_87a", "surcharge", "cess_rate", "caps"]
    missing = [k for k in required if k not in spec]
    if missing:
        raise ValueError(f"spec FY {fy} missing sections: {missing}")
    for regime in ("old", "new"):
        brackets = spec["slabs"][regime]["brackets"]
        # Brackets must be ascending and open-ended exactly once, or the
        # marginal-tax maths is undefined.
        prev = 0
        open_ended = 0
        for b in brackets:
            if b["upto"] is None:
                open_ended += 1
            else:
                if b["upto"] <= prev:
                    raise ValueError(f"spec FY {fy}/{regime}: brackets not ascending")
                prev = b["upto"]
        if open_ended != 1:
            raise ValueError(f"spec FY {fy}/{regime}: expected one open-ended bracket")
