"""Structured input model for the tax engine.

A :class:`TaxpayerProfile` is the single structured representation that every
front end (hand-written rules, LLM-at-query-time, LLM-compiled rules, and the
plain-language parser) must produce before tax is computed. Keeping one model
means the three implementations are compared on equal footing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional


@dataclass
class TaxpayerProfile:
    """Salary-only taxpayer facts needed to compute tax under both regimes.

    All monetary values are annual rupees. ``None`` means "the user did not
    tell us"; the engine treats it as 0 but the parser is responsible for
    flagging that it was missing so a clarifying question can be asked instead
    of silently guessing.
    """

    # --- identity / eligibility -------------------------------------------
    age: int = 30
    is_resident: bool = True

    # --- salary ------------------------------------------------------------
    gross_salary: float = 0.0
    basic_salary: Optional[float] = None
    dearness_allowance: float = 0.0
    hra_received: float = 0.0

    # --- rent / metro for HRA (s.10(13A)) ---------------------------------
    rent_paid: float = 0.0
    is_metro: bool = False

    # --- deductions (old regime only) -------------------------------------
    professional_tax: float = 0.0
    sec_80c: float = 0.0
    sec_80ccd_1b: float = 0.0
    sec_80d_self: float = 0.0
    sec_80d_parents: float = 0.0
    parents_are_senior: bool = False
    home_loan_interest: float = 0.0
    house_is_self_occupied: bool = True

    # --- bookkeeping from the parser --------------------------------------
    missing_fields: list[str] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaxpayerProfile":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def salary_basis(self) -> float:
        """Basic + DA, the base Rule 2A uses for HRA and the 10% test."""
        if self.basic_salary is not None:
            return float(self.basic_salary) + float(self.dearness_allowance or 0.0)
        return float(self.gross_salary)
