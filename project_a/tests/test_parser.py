"""Task 3: the plain-language parser must extract what is stated and ask about
what is not.

The 58 descriptions in ``cases/descriptions.json`` cover Hinglish, incomplete
and contradictory phrasings. Each carries the facts a correct parse must
recover and whether the parser must ask a clarifying question rather than
guess. Both are asserted here.
"""
from __future__ import annotations

import json
import os

import pytest

from project_a.nlp.parser import parse_description

CASES = os.path.join(os.path.dirname(__file__), "cases", "descriptions.json")

with open(CASES, encoding="utf-8") as fh:
    DESCRIPTIONS = json.load(fh)["descriptions"]

IDS = [d["id"] for d in DESCRIPTIONS]


@pytest.mark.parametrize("case", DESCRIPTIONS, ids=IDS)
def test_parser_recovers_stated_facts(case):
    result = parse_description(case["text"])
    profile = vars(result.profile)
    for field, want in case.get("expect", {}).items():
        got = profile.get(field)
        if isinstance(want, bool):
            assert bool(got) == want, f"{case['id']}: {field}"
        elif want is None:
            assert got is None, f"{case['id']}: {field} should be unknown, got {got}"
        else:
            assert got is not None and abs(float(got) - float(want)) < 1, (
                f"{case['id']}: {field} want {want} got {got}")


@pytest.mark.parametrize("case", DESCRIPTIONS, ids=IDS)
def test_parser_asks_instead_of_guessing(case):
    result = parse_description(case["text"])
    assert bool(result.needs_clarification) == bool(case.get("expect_question")), (
        f"{case['id']}: clarification decision {result.needs_clarification}, "
        f"questions={result.questions}"
    )


def test_contradictory_descriptions_produce_questions():
    contradictory = [d for d in DESCRIPTIONS if d["style"] == "contradictory"]
    assert contradictory, "corpus must contain contradictory descriptions"
    for case in contradictory:
        result = parse_description(case["text"])
        if case.get("expect_question"):
            assert result.questions, f"{case['id']}: no question asked"


def test_hinglish_descriptions_are_parsed():
    hinglish = [d for d in DESCRIPTIONS if d["style"] == "hinglish"]
    assert len(hinglish) >= 5
    for case in hinglish:
        result = parse_description(case["text"])
        assert result.profile.gross_salary, f"{case['id']}: salary not found in {case['text']!r}"
