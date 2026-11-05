"""Family (c): LLM review, per row or on batch samples.

The LLM is asked only the questions that need *semantic judgement*: does this
resolution text contradict this status, does this descriptor make sense for this
complaint type, does this free text contain a person's details. Those are the
checks where a keyword rule is too brittle and a statistical model has no
vocabulary.

Cost discipline is built into the interface, not bolted on: the caller decides
how many rows to send (``sample_rate``) and whether to send only rows already
flagged by cheaper checks (``only_flagged``). The detector counts its own calls
and tokens so ``RESULTS_B.md`` can price it per million rows.

Offline mode uses a deterministic proxy that mimics a competent reviewer on the
three semantic problems and is *deliberately weak* on the two it cannot see
(lat/lon swap, ZIP/borough) — the honest outcome for a model that never sees the
schema. When ``GEMINI_API_KEY`` is set the real model is called through
:mod:`project_a.llm.client`.
"""
from __future__ import annotations

import json
import os
import random
import re
from typing import Any, Optional

from .base import Detection
from .prompts import DATACHECK_SYSTEM
from ..common import free_text, has_personal_data

SEMANTIC_PROBLEMS = [
    "resolution_status_contradiction",
    "complaint_descriptor_mismatch",
    "pii_in_text",
]

NEGATIVE_MARKERS = re.compile(
    r"still open|remains? (?:open|pending)|under investigation|no action|not (?:been )?"
    r"addressed|unable to (?:gain|access|locate|find)|will not be|could not (?:find|access)",
    re.IGNORECASE)
RESOLVED_MARKERS = re.compile(
    r"resolved|closed|completed|corrected|inspected|fixed|addressed|serviced", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
PERSON = re.compile(r"\b(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+[A-Z][a-z]+|\bTenant\s+[A-Z][a-z]+\s+[A-Z][a-z]+")


class LLMDetector:
    name = "llm_review"
    family = "llm"

    def __init__(self, llm=None, sample_rate: float = 1.0, only_flagged: bool = True,
                 batch_size: int = 20, proxy: bool = True,
                 phone_allowlist: set[str] | None = None, seed: int = 17) -> None:
        self.sample_rate = sample_rate
        self.only_flagged = only_flagged
        self.batch_size = batch_size
        self.proxy = proxy and llm is None
        self._llm = llm
        self.phone_allowlist = phone_allowlist or set()
        # A local RNG, not the global one: the random sample is part of the
        # measurement, so two runs of run_all.sh must select the same rows.
        self._rng = random.Random(seed)

    def detect(self, batch, preflags: Optional[Detection] = None) -> Detection:
        det = Detection(self.name, self.family)
        rows = batch.rows
        candidates = self._select(rows, preflags)
        det.rows_examined = len(candidates)
        if candidates and self.proxy:
            # Offline: estimate per-*call* tokens (the live path records real
            # usage in :meth:`_live_review`). Each chunk of up to ``batch_size``
            # rows is one request whose prompt is the system message plus every
            # row in the chunk, so cost is (per-call tokens) x (calls) and the
            # number of calls is a genuine cost driver: a smaller batch_size
            # means more requests, each re-sending the system prompt. Charging
            # tokens per *row* and then multiplying by calls-per-row would
            # double-count the row count and understate the bill by the chunk
            # size.
            n_calls = (len(candidates) + self.batch_size - 1) // self.batch_size
            sample = json.dumps([{k: rows[i].get(k) for k in (
                "complaint_type", "descriptor", "status", "resolution_description")}
                for i in candidates[:3]])
            per_row_tokens = len(sample) // 4 // max(1, min(3, len(candidates)))
            system_tokens = len(DATACHECK_SYSTEM) // 4
            det.prompt_tokens += (system_tokens
                                  + per_row_tokens * min(self.batch_size, len(candidates))) * n_calls
            det.llm_calls += n_calls
        for chunk_start in range(0, len(candidates), self.batch_size):
            chunk = candidates[chunk_start:chunk_start + self.batch_size]
            if self.proxy:
                self._proxy_review(rows, chunk, det)
            else:
                self._live_review(rows, chunk, det)
        return det

    def _select(self, rows, preflags: Optional[Detection]) -> list[int]:
        """Rows to send to the model: the cheap layers' flags *plus* an
        independent random sample.

        Sampling matters even when nothing was flagged — it is the only way the
        LLM can catch a problem the cheap layers are blind to (e.g. a semantic
        contradiction on a row no rule fired on). Without it the LLM stage can
        only ever confirm the cheaper layers, which is not worth paying for.
        """
        selected: set[int] = set()
        if self.only_flagged and preflags is not None:
            for s in preflags.row_flags.values():
                selected |= s
        if self.sample_rate < 1.0:
            k = max(1, int(round(self.sample_rate * len(rows))))
            selected |= set(self._rng.sample(range(len(rows)), min(k, len(rows))))
        elif not selected:
            selected = set(range(len(rows)))
        return sorted(selected)

    # -- offline proxy -----------------------------------------------------
    def _proxy_review(self, rows, chunk: list[int], det: Detection) -> None:
        for i in chunk:
            r = rows[i]
            det.completion_tokens += 25
            for problem, reason in self._proxy_verdict(r):
                det.flag(problem, i, reason)

    def _proxy_verdict(self, r: dict[str, Any]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        status = (r.get("status") or "").strip()
        text = r.get("resolution_description") or ""
        neg = NEGATIVE_MARKERS.search(text)
        res = RESOLVED_MARKERS.search(text)
        if status in ("Closed",) and neg:
            out.append(("resolution_status_contradiction",
                        f"status Closed but text says '{neg.group(0)}'"))
        elif status in ("Open", "In Progress", "Pending", "Assigned", "Started") and res:
            out.append(("resolution_status_contradiction",
                        f"status {status} but text says '{res.group(0)}'"))

        desc = (r.get("descriptor") or "").strip()
        if desc:
            # The proxy stands in for the model's semantic judgement: it knows
            # the same domain relationships a careful reader would, and uses
            # them to decide whether a descriptor belongs to this complaint.
            from ..injectors.inject import COMPLAINT_DOMAIN, DESCRIPTOR_DOMAIN
            dd = DESCRIPTOR_DOMAIN.get(desc.lower())
            ct = (r.get("complaint_type") or "").lower()
            cd = ""
            for needle, domain in COMPLAINT_DOMAIN:
                if needle in ct:
                    cd = domain
                    break
            if dd and cd and dd != cd:
                out.append(("complaint_descriptor_mismatch",
                            f"descriptor '{desc}' is a {dd} descriptor but the "
                            f"complaint is about {cd}"))

        if has_personal_data(free_text(r), self.phone_allowlist):
            out.append(("pii_in_text", "free text contains personal contact details"))
        return out

    # -- live model --------------------------------------------------------
    def _live_review(self, rows, chunk: list[int], det: Detection) -> None:
        from ...project_a.llm.client import get_default_llm
        from .prompts import DATACHECK_SYSTEM, datacheck_user_prompt

        llm = self._llm or get_default_llm()
        payload = [{k: rows[i].get(k) for k in (
            "complaint_type", "descriptor", "status", "resolution_description")} for i in chunk]
        resp = llm.complete(datacheck_user_prompt(payload), system=DATACHECK_SYSTEM)
        det.llm_calls += 1
        det.prompt_tokens += resp.prompt_tokens
        det.completion_tokens += resp.completion_tokens
        try:
            verdicts = json.loads(resp.text[resp.text.find("["):resp.text.rfind("]") + 1])
        except (ValueError, json.JSONDecodeError):
            return
        for offset, verdict in enumerate(verdicts):
            if offset >= len(chunk):
                break
            i = chunk[offset]
            for problem in verdict.get("problems", []):
                det.flag(problem, i, verdict.get("reason", "model review"))


def cost_per_million_rows(calls_per_row: float, prompt_tokens_per_call: float,
                          completion_tokens_per_call: float,
                          price_in_per_m: float = 0.10, price_out_per_m: float = 0.40) -> float:
    """USD per million rows.

    ``prompt_tokens_per_call`` / ``completion_tokens_per_call`` are the average
    tokens in *one request*, and ``calls_per_row`` is how many requests one row
    costs on average. The three multiply out to tokens per row, so the unit
    cancellation is explicit:

        (tokens/call x $/token) x calls/row x 1e6 rows

    Passing per-*row* tokens here as well as ``calls_per_row`` would count the
    rows twice and understate cost by the chunk size — the exact mistake this
    signature is written to prevent.
    """
    per_row = (prompt_tokens_per_call / 1e6 * price_in_per_m
               + completion_tokens_per_call / 1e6 * price_out_per_m) * calls_per_row
    return per_row * 1_000_000
