"""Implementation B: LLM computes tax at query time.

The LLM is given the relevant rule text (the YAML spec) on every request and
asked to return the tax under both regimes as JSON. Nothing is cached between
requests, so this measures the true per-request cost, latency and determinism
of a query-time LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import TaxpayerProfile
from ..spec import load_spec
from .client import BaseLLM, Usage

SYSTEM = (
    "You are an Indian income-tax calculator for salaried individuals. "
    "Compute strictly from the rule text provided. Do not use outside knowledge. "
    "Reply with a single JSON object and nothing else."
)

PROMPT = """\
RULES (authoritative; do not deviate):
{rules}

TAXPAYER:
{profile}

Compute, for FY {fy}:
- income under the head Salaries,
- total income under each regime,
- tax payable to the rupee under the old regime and the new regime,
applying the standard deduction, HRA exemption (old regime), ss.80C, 80CCD(1B),
80D, s.24(b) (old regime), slab rates, surcharge with marginal relief, the s.87A
rebate with the marginal-relief proviso only where the rules mark it, and the 4%
cess. Round total income and tax to the nearest ten rupees as the Act requires.

Return JSON exactly:
{{"old_tax": <number>, "new_tax": <number>, "best_regime": "old"|"new"|"either",
  "old_total_income": <number>, "new_total_income": <number>}}
"""


@dataclass
class LLMCalcResult:
    fy: str
    old_tax: Optional[float]
    new_tax: Optional[float]
    best_regime: Optional[str]
    raw: str = ""
    error: Optional[str] = None
    usage: Usage = field(default_factory=Usage)


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


class LLMQueryTimeCalculator:
    """Implementation B. One LLM call per (profile, FY) request."""

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.usage = Usage()

    def _rules_text(self, fy: str) -> str:
        # The whole spec is the "relevant rule text" for this FY; passing it
        # every request is the cost this implementation must justify.
        return json.dumps(load_spec(fy), indent=1, default=str)

    def compute(self, profile: TaxpayerProfile, fy: str = "2024-25") -> LLMCalcResult:
        prompt = PROMPT.format(
            rules=self._rules_text(fy),
            profile=json.dumps(profile.to_dict(), indent=1),
            fy=fy,
        )
        resp = self.llm.complete(prompt, system=SYSTEM, temperature=0.0)
        self.usage.add(resp)
        data = _extract_json(resp.text)
        if not data:
            return LLMCalcResult(fy=fy, old_tax=None, new_tax=None, best_regime=None,
                                 raw=resp.text, error="unparseable JSON")
        try:
            return LLMCalcResult(
                fy=fy,
                old_tax=float(data["old_tax"]),
                new_tax=float(data["new_tax"]),
                best_regime=str(data.get("best_regime", "")),
                raw=resp.text,
                usage=Usage(calls=1),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return LLMCalcResult(fy=fy, old_tax=None, new_tax=None, best_regime=None,
                                 raw=resp.text, error=f"bad fields: {exc}")
