"""The hybrid pipeline for Project B.

Ordering is the whole design: run the cheapest check that can decide on *every*
row, then spend statistics on the batch, then spend an LLM call only on rows the
cheap checks already flagged (plus a small random sample to catch what the cheap
checks missed entirely).

    every row  -> rules          (schema, ranges, impossible values)
    per batch  -> statistics     (null spikes, duplicate surges, drift)
    flagged +  -> LLM review     (semantic contradictions, PII in prose)

The pipeline returns one :class:`Detection` per stage plus a merged view, so
``RESULTS_B.md`` can show what each layer contributed and what the LLM layer
cost on top of the free layers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .detectors.base import Detection
from .detectors.llm_review import LLMDetector
from .detectors.rules import RuleDetector
from .detectors.statistical import StatisticalDetector


@dataclass
class PipelineResult:
    stages: dict[str, Detection] = field(default_factory=dict)
    merged: dict[str, set[int]] = field(default_factory=dict)
    merged_batch: set[str] = field(default_factory=set)
    reasons: dict[str, dict[int, str]] = field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, det: Detection) -> None:
        self.stages[det.name] = det
        for problem, idxs in det.row_flags.items():
            self.merged.setdefault(problem, set()).update(idxs)
            self.reasons.setdefault(problem, {}).update(det.reasons.get(problem, {}))
        self.merged_batch |= det.batch_flags
        self.llm_calls += det.llm_calls
        self.prompt_tokens += det.prompt_tokens
        self.completion_tokens += det.completion_tokens


class HybridPipeline:
    # The LLM is only worth spending on problems that need semantic judgement.
    # A row flagged *only* for a schema or format problem is already decided by
    # the rule layer; sending it to the model would add cost and no information.
    LLM_PROBLEMS = ("resolution_status_contradiction", "complaint_descriptor_mismatch")

    def __init__(self, llm=None, sample_rate: float = 0.05, proxy: bool = True,
                 llm_only_flagged: bool = True, reference=None,
                 max_prefilter_frac: float = 0.10) -> None:
        allow = getattr(reference, "phone_allowlist", set()) if reference else set()
        self.rules = RuleDetector(phone_allowlist=allow)
        self.stats = StatisticalDetector(reference=reference)
        self.llm = LLMDetector(llm=llm, sample_rate=sample_rate,
                               only_flagged=llm_only_flagged, proxy=proxy,
                               phone_allowlist=allow)
        self.sample_rate = sample_rate
        self.max_prefilter_frac = max_prefilter_frac

    def run(self, batch) -> PipelineResult:
        result = PipelineResult()
        rules = self.rules.detect(batch)
        result.add(rules)
        stats = self.stats.detect(batch)
        result.add(stats)

        # Cheap-first: send rows the cheap layers flagged for a *semantic*
        # problem to the LLM, plus an independent random sample so a problem the
        # cheap layers cannot see still gets a chance to be caught. The prefilter
        # is capped so a runaway rule cannot turn a free layer into a large bill.
        combined = Detection("prefilter", "rule")
        cap = max(1, int(self.max_prefilter_frac * len(batch.rows)))
        for det in (rules, stats):
            for problem in self.LLM_PROBLEMS:
                for i in sorted(det.flagged(problem))[:cap]:
                    combined.flag(problem, i, det.reasons.get(problem, {}).get(i, ""))
        result.add(self.llm.detect(batch, preflags=combined))
        return result
