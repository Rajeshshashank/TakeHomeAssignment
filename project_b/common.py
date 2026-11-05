"""Shared text patterns and the agency-phone allow-list rule.

Kept in one module so the injector's clean-row filter, the rule detector and the
LLM proxy all agree on what counts as personal data. Disagreement between them
would silently inflate or deflate every PII number in the results.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from collections import Counter
from typing import Any, Iterable

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PERSON = re.compile(r"\b(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+[A-Z][a-z]+")

TEXT_FIELDS = ("resolution_description", "incident_address", "street_name")


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def free_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(f) or "") for f in TEXT_FIELDS)


def data_path(directory: str, stem: str = "nyc311_2023_sample.jsonl") -> str:
    """Return the plain or gzipped extract, preferring the plain file.

    The gzipped copy is what the repository ships (the plain file is 108 MB);
    both are read identically so a fresh clone reproduces every number without
    a download.
    """
    plain = os.path.join(directory, stem)
    if os.path.exists(plain):
        return plain
    gz = plain + ".gz"
    if os.path.exists(gz):
        return gz
    return plain


def iter_jsonl(path: str):
    """Yield rows from a JSONL file, transparently decompressing ``.gz``."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl(path: str) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def build_phone_allowlist(rows: Iterable[dict[str, Any]], min_count: int = 5) -> set[str]:
    """Numbers that appear repeatedly across the extract are agency hotlines,
    not residents. Learned from frequency rather than hand-typed, so a new
    agency number is allow-listed as soon as it shows up often enough."""
    counts: Counter[str] = Counter()
    for r in rows:
        for m in PHONE.finditer(free_text(r)):
            counts[digits(m.group())] += 1
    return {num for num, n in counts.items() if n >= min_count}


def personal_phone_numbers(text: str, allowlist: set[str]) -> list[str]:
    return [m.group() for m in PHONE.finditer(text)
            if digits(m.group()) not in allowlist]


def has_personal_data(text: str, allowlist: set[str]) -> str:
    """Return a short reason if the text carries personal data, else ''."""
    if EMAIL.search(text):
        return "email address in free text"
    if SSN.search(text):
        return "SSN in free text"
    if PERSON.search(text):
        return "personal name in free text"
    nums = personal_phone_numbers(text, allowlist)
    if nums:
        return f"phone number {nums[0]} in free text"
    return ""
