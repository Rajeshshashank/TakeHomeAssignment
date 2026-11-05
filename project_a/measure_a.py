"""Task 3 measurement harness for Project A.

Measures all three implementations on the same 242-case corpus:

  A  rules engine                (project_a.engine)
  B  LLM at query time           (project_a.llm.impl_b)
  C  LLM-compiled rules          (project_a.compiled)

Reported per implementation: accuracy against the rules engine's answer (which
is itself checked against the official calculator in Task 2), mean and p95
latency, token counts, and cost per 1,000 requests. The LLM implementations
report *measured* usage from the client; when no API key is present the
deterministic proxy supplies token counts and the latency model converts them
to hosted-model latency, and the report says so.

Output: project_a/tests/cases/measurement.json and a printed table.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .engine import compute
from .llm import get_default_llm
from .llm.client import Usage
from .llm.impl_b import LLMQueryTimeCalculator
from .llm.impl_c import LLMCompiledCalculator
from .llm.proxy import PROXY_MS_PER_OUTPUT_TOKEN, PROXY_TTFT_S, RuleFollowingProxyLLM
from .models import TaxpayerProfile

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "tests", "cases", "cases.json")
OUT = os.path.join(HERE, "tests", "cases", "measurement.json")


@dataclass
class ImplScore:
    name: str
    n: int = 0
    correct: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    notes: str = ""

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    def latency_stats(self, proxy_mode: bool) -> dict[str, float]:
        if not self.latencies:
            return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
        if proxy_mode and self.usage.completion_tokens:
            # The proxy runs in-process, so its wall time is ~0 ms and says
            # nothing about a hosted model. Model the hosted latency instead:
            # time-to-first-token plus a per-output-token cost, using the
            # measured token counts. This is the one modelled (not measured)
            # number in the report and is labelled as such in RESULTS_A.md.
            per_call = (PROXY_TTFT_S
                        + PROXY_MS_PER_OUTPUT_TOKEN * self.usage.completion_tokens
                        / max(1, self.usage.calls)) / 1000.0
            lat = [per_call] * self.n
        else:
            lat = sorted(self.latencies)
        return {
            "mean_ms": 1000 * statistics.mean(lat),
            "p50_ms": 1000 * lat[len(lat) // 2],
            "p95_ms": 1000 * lat[min(len(lat) - 1, int(0.95 * len(lat)))],
        }

    def as_dict(self, proxy_mode: bool) -> dict[str, Any]:
        return {
            "name": self.name,
            "cases": self.n,
            "correct": self.correct,
            "errors": self.errors,
            "accuracy": round(self.accuracy, 4),
            "latency": {k: round(v, 2) for k, v in self.latency_stats(proxy_mode).items()},
            "usage": self.usage.as_dict(),
            "cost_per_1000_requests_usd": round(self.usage.cost_usd / max(1, self.n) * 1000, 4),
            "notes": self.notes,
        }


def _truth(profile: TaxpayerProfile, fy: str) -> tuple[float, float]:
    r = compute(profile, fy=fy)
    return r.old.total_tax, r.new.total_tax


def _expand(cases: list[dict], fy_filter: Optional[str]) -> list[tuple[dict, str, dict]]:
    """Flatten cases.json into (case, fy, expected) rows."""
    rows = []
    for case in cases:
        expected = case.get("expected", {})
        for fy in (expected.keys() or [case.get("fy", "2024-25")]):
            if fy_filter and fy != fy_filter:
                continue
            rows.append((case, fy, expected.get(fy, {})))
    return rows


def fidelity_sweep(fy_filter: Optional[str] = None) -> list[dict[str, Any]]:
    """Accuracy of Implementation B under each observed LLM failure mode.

    Each profile is a different *kind* of plausible model error, so the sweep
    shows not just "how accurate is the LLM" but "which single omission costs
    the most", which is what drives the hybrid design in Task 6.
    """
    raw = json.load(open(CASES))
    cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    rows = _expand(cases, fy_filter)
    out = []
    for fidelity in ("full", "no_marginal", "rupee_round", "no_marginal+rupee_round", "slab_only"):
        calc = LLMQueryTimeCalculator(RuleFollowingProxyLLM(fidelity=fidelity))
        correct = 0
        for case, fy, _ in rows:
            profile = TaxpayerProfile.from_dict(case.get("profile", {}))
            eng = compute(profile, fy=fy)
            r = calc.compute(profile, fy=fy)
            if (r.old_tax is not None and abs(r.old_tax - eng.old.total_tax) < 1
                    and r.new_tax is not None and abs(r.new_tax - eng.new.total_tax) < 1):
                correct += 1
        out.append({"fidelity": fidelity, "cases": len(rows), "correct": correct,
                    "accuracy": round(correct / max(1, len(rows)), 4)})
    return out


def measure(proxy: bool = True, fy_filter: Optional[str] = None) -> dict[str, Any]:
    raw = json.load(open(CASES))
    cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    rows = _expand(cases, fy_filter)

    if proxy:
        llm_b = RuleFollowingProxyLLM(fidelity="no_marginal")
        llm_b_note = ("offline proxy: every step correct except marginal relief on surcharge and "
                      "s.87A; the same model the ITD basic calculator implements")
    else:
        llm_b = get_default_llm()
        llm_b_note = f"live model: {getattr(llm_b, 'name', 'unknown')}"

    scores = {
        "A_rules": ImplScore("A: hand-written rules engine",
                             notes="deterministic; validated against the official calculator"),
        "B_llm_query": ImplScore("B: LLM at query time", notes=llm_b_note),
        "C_llm_compiled": ImplScore("C: LLM-compiled rules", notes="no LLM at query time"),
    }

    calc_b = LLMQueryTimeCalculator(llm_b)
    calc_c = LLMCompiledCalculator()
    # Warm the compiled modules so the first case does not pay import cost.
    try:
        calc_c.compute(TaxpayerProfile(gross_salary=1000000), fy="2024-25")
        calc_c.compute(TaxpayerProfile(gross_salary=1000000), fy="2025-26")
    except Exception:  # noqa: BLE001
        pass

    for case, fy, expected in rows:
        profile = TaxpayerProfile.from_dict(case.get("profile", case.get("input", {})))
        eng = compute(profile, fy=fy)
        truth = (eng.old.total_tax, eng.new.total_tax)
        if expected:
            # The recorded expectation was produced by the oracle and checked
            # against the official calculator; assert they still agree.
            assert abs(expected["old"] - truth[0]) < 1 and abs(expected["new"] - truth[1]) < 1, \
                f"engine disagrees with recorded case {case.get('id')} {fy}"

        scores["A_rules"].n += 1
        scores["A_rules"].correct += 1

        s = scores["B_llm_query"]
        s.n += 1
        t0 = time.perf_counter()
        rb = calc_b.compute(profile, fy=fy)
        s.latencies.append(time.perf_counter() - t0)
        if rb.error or rb.old_tax is None:
            s.errors += 1
        elif abs(rb.old_tax - truth[0]) < 1 and abs(rb.new_tax - truth[1]) < 1:
            s.correct += 1

        s = scores["C_llm_compiled"]
        s.n += 1
        t0 = time.perf_counter()
        rc = calc_c.compute(profile, fy=fy)
        s.latencies.append(time.perf_counter() - t0)
        if rc.error or rc.old_tax is None:
            s.errors += 1
        elif abs(rc.old_tax - truth[0]) < 1 and abs(rc.new_tax - truth[1]) < 1:
            s.correct += 1

    scores["B_llm_query"].usage = calc_b.usage
    report = {
        "cases": max(s.n for s in scores.values()),
        "fy_filter": fy_filter,
        "proxy_mode": proxy,
        "implementations": [s.as_dict(proxy) for s in scores.values()],
        "fidelity_sweep": fidelity_sweep(fy_filter),
    }
    return report


def determinism_check(proxy: bool, repeats: int = 10, n_cases: int = 20) -> list[dict[str, Any]]:
    """Task 4: run the same input ``repeats`` times and count distinct answers.

    A deterministic implementation answers once. The LLM-at-query-time row is
    the interesting one: at temperature 0 it is *often* stable, but it is not
    guaranteed to be, and that difference is the reason the shipped design does
    not put a model in the per-request path.
    """
    raw = json.load(open(CASES))
    cases = (raw.get("cases", raw) if isinstance(raw, dict) else raw)[:n_cases]

    if proxy:
        llm_b = RuleFollowingProxyLLM(fidelity="no_marginal")
    else:
        llm_b = get_default_llm()
    impls = {
        "A: hand-written rules engine": lambda p, fy: _truth(p, fy),
        "B: LLM at query time": lambda p, fy: _llm_pair(LLMQueryTimeCalculator(llm_b), p, fy),
        "C: LLM-compiled rules": lambda p, fy: _llm_pair(LLMCompiledCalculator(), p, fy),
    }

    out = []
    for name, fn in impls.items():
        unstable = 0
        for case in cases:
            profile = TaxpayerProfile.from_dict(case.get("profile", {}))
            seen = {fn(profile, "2024-25") for _ in range(repeats)}
            if len(seen) > 1:
                unstable += 1
        out.append({
            "implementation": name,
            "cases": len(cases),
            "repeats": repeats,
            "cases_with_more_than_one_answer": unstable,
            "deterministic": unstable == 0,
        })
    return out


def _llm_pair(calc, profile: TaxpayerProfile, fy: str) -> tuple:
    r = calc.compute(profile, fy=fy)
    return (r.old_tax, r.new_tax)


def main() -> None:
    proxy = os.environ.get("GEMINI_API_KEY") is None and os.environ.get("OPENAI_API_KEY") is None
    report = measure(proxy=proxy)
    report["determinism"] = determinism_check(proxy)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    print(f"\n{'implementation':34s} {'acc':>7s} {'mean ms':>9s} {'p95 ms':>9s} "
          f"{'tokens/req':>11s} {'$/1k':>9s}")
    for impl in report["implementations"]:
        tok = (impl["usage"]["prompt_tokens"] + impl["usage"]["completion_tokens"]) / max(1, impl["cases"])
        print(f"{impl['name']:34s} {impl['accuracy']*100:6.1f}% "
              f"{impl['latency']['mean_ms']:9.1f} {impl['latency']['p95_ms']:9.1f} "
              f"{tok:11.0f} {impl['cost_per_1000_requests_usd']:9.4f}")
    print("\nImplementation B fidelity sweep (which single omission costs most):")
    for row in report["fidelity_sweep"]:
        print(f"  {row['fidelity']:28s} {row['correct']:4d}/{row['cases']:<4d} "
              f"{row['accuracy']*100:5.1f}%")
    print("\nDeterminism (same input x10):")
    for row in report["determinism"]:
        verdict = "deterministic" if row["deterministic"] else "NON-deterministic"
        print(f"  {row['implementation']:34s} {row['cases_with_more_than_one_answer']}"
              f"/{row['cases']} cases varied -> {verdict}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
