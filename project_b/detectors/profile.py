"""A reference profile of the *expected* shape of the data.

This is the "model" the statistical family compares a new batch against. It is
built once from a sample of raw data (see ``scripts/build_reference.py``) and
frozen to JSON, so the numbers in ``RESULTS_B.md`` do not depend on a random
seed or on the batch being scored.

What it stores, and why each thing is statistical rather than a rule:

* ``descriptor_by_type`` — P(descriptor | complaint_type). A descriptor that is
  legal but has never been seen for that complaint type is a soft signal, so it
  is scored by probability, not by membership in a hand-written list.
* ``borough_by_zip`` — P(borough | ZIP). Same reasoning: ZIPs occasionally cross
  borough lines, so a mismatch is unlikely, not impossible.
* ``coord`` / ``text_len`` — mean and standard deviation, so a value can be
  scored by how many standard deviations it sits from the norm.
* ``null_rate`` — per-agency, per-column null rate, the baseline a spike is
  measured against.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..common import build_phone_allowlist

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")

_NEG = re.compile(r"still open|remains? (?:open|pending)|under investigation|no action|"
                  r"not (?:been )?addressed|unable to (?:gain|access|locate|find)|"
                  r"will not be|could not (?:find|access)", re.I)


@dataclass
class Reference:
    descriptor_by_type: dict[str, Counter] = field(default_factory=dict)
    borough_by_zip: dict[str, Counter] = field(default_factory=dict)
    borough_by_prefix: dict[str, Counter] = field(default_factory=dict)
    null_rate: dict[str, dict[str, float]] = field(default_factory=dict)
    coord_mean: tuple[float, float] = (0.0, 0.0)
    coord_sd: tuple[float, float] = (1.0, 1.0)
    text_len_mean: float = 0.0
    text_len_sd: float = 1.0
    # P(free text contains a negative/contradiction marker | status == Closed).
    # The baseline a lexicon-drift check measures a batch against.
    neg_rate: float = 0.0
    # Phone numbers that recur across the extract: agency hotlines, not people.
    phone_allowlist: set[str] = field(default_factory=set)
    n_rows: int = 0

    def p_descriptor(self, ctype: str, descriptor: str) -> float:
        c = self.descriptor_by_type.get(ctype)
        if not c:
            return 1.0  # unknown complaint type: no basis to judge
        total = sum(c.values())
        return c.get(descriptor, 0) / total if total else 1.0

    def p_borough(self, zip_code: str, borough: str) -> float:
        # Prefer the exact ZIP; fall back to the 3-digit prefix, which is how
        # NYC allocates ZIPs by borough and therefore generalises to a ZIP the
        # reference has never seen.
        c = self.borough_by_zip.get(zip_code)
        if not c:
            c = self.borough_by_prefix.get(zip_code[:3])
        if not c:
            return 1.0
        total = sum(c.values())
        return c.get(borough, 0) / total if total else 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "descriptor_by_type": {k: dict(v) for k, v in self.descriptor_by_type.items()},
            "borough_by_zip": {k: dict(v) for k, v in self.borough_by_zip.items()},
            "borough_by_prefix": {k: dict(v) for k, v in self.borough_by_prefix.items()},
            "null_rate": self.null_rate,
            "coord_mean": list(self.coord_mean),
            "coord_sd": list(self.coord_sd),
            "text_len_mean": self.text_len_mean,
            "text_len_sd": self.text_len_sd,
            "neg_rate": self.neg_rate,
            "phone_allowlist": sorted(self.phone_allowlist),
            "n_rows": self.n_rows,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Reference":
        return cls(
            descriptor_by_type={k: Counter(v) for k, v in d["descriptor_by_type"].items()},
            borough_by_zip={k: Counter(v) for k, v in d["borough_by_zip"].items()},
            borough_by_prefix={k: Counter(v) for k, v in d.get("borough_by_prefix", {}).items()},
            null_rate=d["null_rate"],
            coord_mean=tuple(d["coord_mean"]),
            coord_sd=tuple(d["coord_sd"]),
            text_len_mean=d["text_len_mean"],
            text_len_sd=d["text_len_sd"],
            neg_rate=d.get("neg_rate", 0.0),
            phone_allowlist=set(d.get("phone_allowlist", [])),
            n_rows=d["n_rows"],
        )


def build_reference(rows: list[dict[str, Any]]) -> Reference:
    desc: dict[str, Counter] = defaultdict(Counter)
    bz: dict[str, Counter] = defaultdict(Counter)
    bp: dict[str, Counter] = defaultdict(Counter)
    nulls: dict[str, Counter] = defaultdict(Counter)
    lats, lons, lens = [], [], []
    neg_hits = 0
    closed_rows = 0
    for r in rows:
        ct, d = r.get("complaint_type"), r.get("descriptor")
        if ct and d:
            desc[ct][d] += 1
        z, b = r.get("incident_zip"), r.get("borough")
        if z and b and str(z).isdigit() and len(str(z)) == 5:
            bz[str(z)][b] += 1
            bp[str(z)[:3]][b] += 1
        agency = r.get("agency") or "?"
        for col in ("incident_zip", "descriptor", "borough", "location_type"):
            nulls[agency][f"{col}_n"] += 1
            if r.get(col) in (None, ""):
                nulls[agency][f"{col}_null"] += 1
        if r.get("latitude") and _isnum(r["latitude"]):
            lats.append(float(r["latitude"]))
            lons.append(float(r["longitude"]))
        if r.get("resolution_description") is not None:
            lens.append(len(r["resolution_description"]))
        # Baseline for the contradiction-drift check is conditioned on status
        # Closed, because that is the population the check scores.
        if r.get("status") == "Closed":
            closed_rows += 1
            if _NEG.search(r.get("resolution_description") or ""):
                neg_hits += 1
    null_rate = {
        a: {col: c[f"{col}_null"] / max(1, c[f"{col}_n"])
            for col in ("incident_zip", "descriptor", "borough", "location_type")}
        for a, c in nulls.items()
    }
    m, s = _mean_sd(lats), _mean_sd(lons)
    tl = _mean_sd([float(x) for x in lens])
    return Reference(
        descriptor_by_type={k: v for k, v in desc.items()},
        borough_by_zip={k: v for k, v in bz.items()},
        borough_by_prefix={k: v for k, v in bp.items()},
        null_rate=null_rate,
        coord_mean=(m[0], s[0]),
        coord_sd=(m[1], s[1]),
        text_len_mean=tl[0],
        text_len_sd=tl[1] or 1.0,
        neg_rate=neg_hits / max(1, closed_rows),
        phone_allowlist=build_phone_allowlist(rows),
        n_rows=len(rows),
    )


def load_reference(path: str) -> Reference:
    with open(path, encoding="utf-8") as fh:
        return Reference.from_dict(json.load(fh))


def save_reference(ref: Reference, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ref.as_dict(), fh)


def _isnum(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _mean_sd(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return 0.0, 1.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / n
    return m, (var ** 0.5) or 1.0
