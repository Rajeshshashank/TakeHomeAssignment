"""Implementation C: LLM-compiled rules.

An LLM converts the rule text into an executable Python module **once**. The
module is reviewed, committed, and then runs with no LLM at query time.

``compile_rules(llm, fy)`` performs the one-time compilation. ``LLMCompiledCalculator``
runs the committed artifact. The committed artifacts under
``project_a/compiled/`` are the reviewed output of this prompt (see AI_USAGE.md
for provenance).
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Optional

from ..models import TaxpayerProfile
from ..spec import load_spec
from .client import BaseLLM, Usage

COMPILED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "compiled")

SYSTEM = (
    "You convert Indian income-tax rules into a single self-contained Python module. "
    "The module must expose compute(profile: dict, fy: str) -> dict with keys "
    "'old' and 'new' holding the tax payable to the rupee. Hard-code the statutory "
    "numbers given in the rules. Output only Python code."
)

PROMPT = """\
Convert these rules for FY {fy} into one Python module.

RULES:
{rules}

Requirements:
- expose `compute(profile: dict, fy: str) -> dict` returning {{'old': <float>, 'new': <float>}}.
- `profile` keys: gross_salary, basic_salary, dearness_allowance, hra_received,
  rent_paid, is_metro, age, professional_tax, sec_80c, sec_80ccd_1b, sec_80d_self,
  sec_80d_parents, parents_are_senior, home_loan_interest, house_is_self_occupied.
- apply: standard deduction, HRA exemption (old), ss.80C/80CCD(1B)/80D (old),
  s.24(b) (old), slabs, surcharge with marginal relief, the s.87A rebate with the
  marginal-relief proviso *only* where `rebate_87a.<regime>.marginal_relief` is
  true, 4% cess; round total income and tax to the nearest ten rupees.
- no imports beyond the standard library; no external data files.
Output only the code.
"""


def compile_rules(llm: BaseLLM, fy: str, out_dir: str = COMPILED_DIR) -> str:
    """One-time LLM compilation of the rule text into an executable module."""
    prompt = PROMPT.format(fy=fy, rules=_pretty_spec(fy))
    resp = llm.complete(prompt, system=SYSTEM, temperature=0.0, max_tokens=4096)
    code = _strip_code_fence(resp.text)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"compiled_fy{fy.replace('-', '_')}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    return path


def _pretty_spec(fy: str) -> str:
    import json

    return json.dumps(load_spec(fy), indent=1, default=str)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines)
    return t + "\n"


@dataclass
class CompiledResult:
    fy: str
    old_tax: Optional[float]
    new_tax: Optional[float]
    error: Optional[str] = None


class LLMCompiledCalculator:
    """Implementation C. Runs the compiled artifact; no LLM at query time."""

    def __init__(self):
        self.usage = Usage()  # stays zero: no LLM calls at query time

    def _module(self, fy: str):
        name = f"project_a.compiled.compiled_fy{fy.replace('-', '_')}"
        return importlib.import_module(name)

    def compute(self, profile: TaxpayerProfile, fy: str = "2024-25") -> CompiledResult:
        try:
            mod = self._module(fy)
        except ModuleNotFoundError:
            return CompiledResult(fy=fy, old_tax=None, new_tax=None,
                                  error=f"no compiled module for FY {fy}")
        try:
            out: dict[str, Any] = mod.compute(profile.to_dict(), fy)
            return CompiledResult(fy=fy, old_tax=float(out["old"]), new_tax=float(out["new"]))
        except Exception as exc:  # noqa: BLE001 - the compiled artifact may be wrong
            return CompiledResult(fy=fy, old_tax=None, new_tax=None, error=repr(exc))
