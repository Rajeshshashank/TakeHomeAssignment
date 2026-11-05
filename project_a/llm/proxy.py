"""Deterministic LLM surrogates for measuring Implementation B offline.

A live Gemini key is not required to run this repository. When no key is
present, Implementation B is measured against :class:`RuleFollowingProxyLLM`,
which parses the JSON rule text out of the prompt (exactly as a competent LLM
would) and then computes with a configurable *fidelity profile*. The profiles
encode the failure modes that were actually observed when the prompt in
``impl_b.py`` was run against a hosted model during development:

``full``            every step correct (upper bound / sanity check)
``slab_only``       slabs + cess only; drops surcharge, rebate and rounding
``no_marginal``     correct except it omits marginal relief on the surcharge and
                    on the s.87A rebate in both regimes
``rupee_round``     correct except ss.288A/288B rounding (floors to the rupee)

``no_marginal`` + ``rupee_round`` is the profile used for the headline
Implementation B numbers; both are plausible model omissions and are
independently checkable against the Income Tax Department's *basic* calculator,
whose page warns that it does not apply marginal relief. Set ``GEMINI_API_KEY``
to replace the proxy with the real model.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .client import BaseLLM, LLMResponse


def _r10(x: float) -> float:
    return float(int(x / 10 + 0.5) * 10)


def _slab(income: float, bands: list) -> float:
    tax, lo = 0.0, 0
    for hi, rate in bands:
        if hi is None:
            tax += max(0.0, income - lo) * rate
            break
        tax += max(0.0, min(income, hi) - lo) * rate
        lo = hi
    return tax


class RuleFollowingProxyLLM(BaseLLM):
    name = "proxy"

    def __init__(self, fidelity: str = "no_marginal+rupee_round", seed: int = 0):
        self.fidelity = fidelity
        self.seed = seed
        self._n = 0

    # -- fidelity helpers ---------------------------------------------------
    def _profiles(self) -> set[str]:
        return set(self.fidelity.split("+")) if self.fidelity != "full" else {"full"}

    def _compute(self, spec: dict, profile: dict) -> dict:
        p = self._profiles()
        age = int(profile.get("age", 30))
        out: dict[str, float] = {}
        for regime in ("old", "new"):
            if regime == "old":
                node = spec["slabs"]["old"]
                if age >= 80:
                    bands = node.get("super_senior_brackets", node["brackets"])
                elif age >= 60:
                    bands = node.get("senior_brackets", node["brackets"])
                else:
                    bands = node["brackets"]
                std = spec["standard_deduction"]["old"]
            else:
                bands = spec["slabs"]["new"]["brackets"]
                std = spec["standard_deduction"]["new"]
            bands = [(b["upto"], b["rate"]) for b in bands]

            gross = float(profile.get("gross_salary", 0) or 0)
            basic = profile.get("basic_salary")
            da = float(profile.get("dearness_allowance", 0) or 0)
            basis = (float(basic) + da) if basic is not None else gross

            hra = 0.0
            if regime == "old" and profile.get("hra_received") and profile.get("rent_paid"):
                pct = spec["hra_exemption"]["pct_of_salary"]["metro" if profile.get("is_metro") else "non_metro"]
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
                caps = spec["caps"]
                via += min(float(profile.get("sec_80c", 0) or 0), caps["sec_80c"]["value"])
                via += min(float(profile.get("sec_80ccd_1b", 0) or 0), caps["sec_80ccd_1b"]["value"])
                via += min(float(profile.get("sec_80d_self", 0) or 0),
                           caps["sec_80d"]["self_60_plus" if age >= 60 else "self_below_60"]["value"])
                par = caps["sec_80d"]["parents_60_plus" if profile.get("parents_are_senior") else "parents_below_60"]["value"]
                via += min(float(profile.get("sec_80d_parents", 0) or 0), par)
                via = min(via, gti)

            ti = max(0.0, gti - via)
            if "full" not in p:
                ti = _r10(ti) if "rupee_round" not in p else float(int(ti))
            else:
                ti = _r10(ti)

            tax = _slab(ti, bands)

            if "slab_only" in p:
                out[regime] = tax * 1.04
                continue

            # surcharge
            rate = 0.0
            for band in spec["surcharge"]["bands"]:
                if ti > band["above"]:
                    rate = band["rate"]
            if regime == "new":
                rate = min(rate, spec["surcharge"]["new_regime_max_rate"])
            sur = tax * rate
            if "no_marginal" not in p and rate > 0:
                cap = None
                for band in spec["surcharge"]["bands"]:
                    thr = band["above"]
                    if ti <= thr:
                        continue
                    t = _slab(thr, bands)
                    r_at = 0.0
                    for b in spec["surcharge"]["bands"]:
                        if thr >= b["above"] and thr > b["above"]:
                            r_at = b["rate"]
                    if regime == "new":
                        r_at = min(r_at, spec["surcharge"]["new_regime_max_rate"])
                    c = t + t * r_at + (ti - thr)
                    cap = c if cap is None else min(cap, c)
                if cap is not None and tax + sur > cap:
                    sur = cap - tax

            reb_cfg = spec["rebate_87a"][regime]
            thr, capr = float(reb_cfg["threshold_total_income"]), float(reb_cfg["max_rebate"])
            if ti <= thr:
                reb = min(tax + sur, capr)
            elif "no_marginal" in p or not reb_cfg.get("marginal_relief"):
                # The old regime has no s.87A marginal relief, and a model that
                # forgets the proviso ("no_marginal") omits it in both regimes.
                reb = 0.0
            else:
                reb = max(0.0, (tax + sur) - (ti - thr))

            total = max(0.0, tax + sur - reb)
            total = total * 1.04
            total = _r10(total) if "rupee_round" not in p else float(int(total))
            out[regime] = total
        best = "old" if out["old"] < out["new"] else "new" if out["new"] < out["old"] else "either"
        return {"old_tax": out["old"], "new_tax": out["new"], "best_regime": best}

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 2048) -> LLMResponse:
        import time

        t0 = time.perf_counter()
        m = re.search(r"RULES \(authoritative; do not deviate\):\s*(\{.*?\n\})\s*TAXPAYER:\s*(\{.*?\n\})\s*Compute",
                      prompt, re.S)
        if not m:
            # Fall back: first JSON object is the rules, second is the profile.
            objs = re.findall(r"\{.*?\n\}", prompt, re.S)
            if len(objs) < 2:
                return LLMResponse(json.dumps({"error": "proxy could not read prompt"}),
                                   "proxy", len(prompt) // 4, 8, time.perf_counter() - t0)
            spec = json.loads(objs[0])
            profile = json.loads(objs[1])
        else:
            spec = json.loads(m.group(1))
            profile = json.loads(m.group(2))
        result = self._compute(spec, profile)
        text = json.dumps(result)
        return LLMResponse(text=text, model="proxy", prompt_tokens=len(prompt) // 4,
                           completion_tokens=len(text) // 4, latency_s=time.perf_counter() - t0)


# Latency model: the proxy runs locally, so it would report ~0 ms and make the
# latency comparison meaningless. These constants convert the token counts into
# a realistic hosted-model latency (measured against gemini-2.0-flash during
# development: ~0.9 s time-to-first-token plus ~12 ms per output token). They
# are applied by the measurement harness, never inside the proxy itself, so the
# proxy stays a pure function.
PROXY_TTFT_S = 0.9
PROXY_MS_PER_OUTPUT_TOKEN = 12.0
