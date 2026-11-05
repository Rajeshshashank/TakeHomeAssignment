"""Prompts for the LLM review family (Project B).

Kept in one file so the exact wording the model sees is versioned alongside the
code that parses its answer, and so a change to the prompt is visible in the
diff as a *change to the detector*, not buried in a string literal.
"""
from __future__ import annotations

import json
from typing import Any

DATACHECK_SYSTEM = (
    "You are a data-quality reviewer for a municipal service-request dashboard. "
    "You are given rows from an NYC 311 extract. For each row, decide whether it "
    "contains one or more of these problems:\n"
    "- resolution_status_contradiction: the resolution_description contradicts status "
    "(e.g. status is Closed but the text says the case is still open, or status is "
    "Open but the text says it was resolved).\n"
    "- complaint_descriptor_mismatch: the descriptor is unrelated to the complaint_type.\n"
    "- pii_in_text: free text contains a person's name, phone number or email address "
    "(an agency hotline such as 311 is NOT personal data).\n"
    "Answer with a JSON array, one object per row in order, shaped "
    '{"problems": ["..."], "reason": "one short sentence"}. Use an empty problems '
    "list for a clean row. Do not invent problems."
)


def datacheck_user_prompt(rows: list[dict[str, Any]]) -> str:
    return ("Review these rows and return only the JSON array.\n"
            + json.dumps(rows, ensure_ascii=False))
