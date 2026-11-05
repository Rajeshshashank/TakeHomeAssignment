"""Shared detector interface for Project B.

Every detector — rule, statistical, or LLM — returns the same
:class:`Detection`: the rows it flags per problem type, the batch-level
problems it flags, a human-readable reason per flagged row, and the resources it
consumed (LLM calls and tokens, zero for the non-LLM families). One shape for
all three families is what makes the cost/false-alarm comparison in
``RESULTS_B.md`` an apples-to-apples table instead of a narrative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Detection:
    name: str
    family: str  # "rule" | "statistical" | "llm"
    row_flags: dict[str, set[int]] = field(default_factory=dict)
    batch_flags: set[str] = field(default_factory=set)
    reasons: dict[str, dict[int, str]] = field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rows_examined: int = 0

    def flag(self, problem: str, idx: int, reason: str) -> None:
        self.row_flags.setdefault(problem, set()).add(idx)
        self.reasons.setdefault(problem, {})[idx] = reason

    def flagged(self, problem: str) -> set[int]:
        return self.row_flags.get(problem, set())

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "rows_examined": self.rows_examined,
            "flags": {k: len(v) for k, v in self.row_flags.items()},
            "batch_flags": sorted(self.batch_flags),
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


def score(det: Detection, problem: str, truth: set[int], n_rows: int) -> dict[str, Any]:
    """Detection rate and false-alarm rate for one problem type.

    ``truth`` is the set of rows the injector corrupted. For schema-level
    problems there are no per-row labels, so detection is scored on the batch
    flag and the false-alarm rate is the fraction of *clean* batches flagged.
    """
    flagged = det.flagged(problem)
    tp = len(flagged & truth)
    fp = len(flagged - truth)
    fn = len(truth - flagged)
    detection = tp / len(truth) if truth else (1.0 if problem in det.batch_flags else 0.0)
    false_alarm = fp / max(1, n_rows - len(truth))
    return {
        "problem": problem,
        "detector": det.name,
        "family": det.family,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "detection_rate": round(detection, 4),
        "false_alarm_rate": round(false_alarm, 5),
        "explains_itself": bool(det.reasons.get(problem)),
    }
