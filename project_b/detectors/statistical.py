"""Family (b): statistical and unsupervised-ML detectors.

Rules catch what is impossible; statistics catch what is *implausible* given
what this dataset normally looks like. Each detector here compares the batch
against a frozen reference profile (:mod:`profile`) built from raw data, so
thresholds are calibrated on real distributions rather than guessed:

* **Likelihood of a categorical pair** — a descriptor that has never appeared
  for a complaint type scores near zero. This catches a *plausible-looking*
  descriptor that is still wrong, which no membership rule can do.
* **Duplicate detection** — exact via a content hash; near via a blocking key
  plus field/time similarity, so a re-submitted request with a nudged timestamp
  is still a duplicate.
* **Distribution drift** — per-agency null rates and coordinate/text-length
  z-scores against the reference.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Optional

from .base import Detection
from .profile import Reference

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_tokens(a)), set(_tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _row_hash(row: dict[str, Any]) -> str:
    keys = sorted(k for k in row if k != "unique_key")
    payload = "|".join(f"{k}={row.get(k)}" for k in keys)
    return hashlib.sha1(payload.encode()).hexdigest()


class StatisticalDetector:
    name = "statistical"
    family = "statistical"

    def __init__(self, reference: Optional[Reference] = None,
                 descriptor_p: float = 0.002, borough_p: float = 0.05,
                 near_threshold: float = 0.82,
                 coord_z: float = 6.0, text_z: float = 6.0) -> None:
        self.ref = reference
        self.descriptor_p = descriptor_p
        self.borough_p = borough_p
        self.near_threshold = near_threshold
        self.coord_z = coord_z
        self.text_z = text_z

    def detect(self, batch) -> Detection:
        det = Detection(self.name, self.family, rows_examined=len(batch.rows))
        rows = batch.rows
        self._exact_duplicates(rows, det)
        self._near_duplicates(rows, det)
        self._categorical_likelihood(rows, det)
        self._coordinate_outliers(rows, det)
        self._contradiction_drift(rows, det)
        self._null_drift(rows, det)
        return det

    # -- duplicates --------------------------------------------------------
    def _exact_duplicates(self, rows, det: Detection) -> None:
        seen: dict[str, int] = {}
        for i, r in enumerate(rows):
            h = _row_hash(r)
            if h in seen:
                det.flag("exact_duplicates", i, f"identical to row {seen[h]} on every field")
            else:
                seen[h] = i

    def _near_duplicates(self, rows, det: Detection) -> None:
        blocks: dict[tuple, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            blocks[(r.get("complaint_type"), r.get("incident_zip"))].append(i)
        for _key, idxs in blocks.items():
            if len(idxs) < 2 or len(idxs) > 300:
                continue
            for a in range(len(idxs)):
                ia = idxs[a]
                for b in range(a + 1, len(idxs)):
                    ib = idxs[b]
                    if ib in det.flagged("near_duplicates"):
                        continue
                    ra, rb = rows[ia], rows[ib]
                    sim = _jaccard(ra.get("resolution_description") or "",
                                   rb.get("resolution_description") or "")
                    same_addr = ((ra.get("incident_address") or "").upper()
                                 == (rb.get("incident_address") or "").upper())
                    close = _minutes_apart(ra.get("created_date"), rb.get("created_date")) <= 30
                    if same_addr and close:
                        det.flag("near_duplicates", ib,
                                 f"near-duplicate of row {ia}: same type/ZIP/address, "
                                 f"timestamps within 30 min")
                    elif sim >= 0.95 and close:
                        det.flag("near_duplicates", ib,
                                 f"near-duplicate text of row {ia} ({sim:.0%} similar)")

    # -- categorical likelihood -------------------------------------------
    def _categorical_likelihood(self, rows, det: Detection) -> None:
        if self.ref is None:
            return
        for i, r in enumerate(rows):
            ct, d = r.get("complaint_type"), r.get("descriptor")
            if ct and d:
                p = self.ref.p_descriptor(ct, d)
                if p < self.descriptor_p:
                    det.flag("complaint_descriptor_mismatch", i,
                             f"descriptor '{d}' has probability {p:.2e} for '{ct}'")
            z, b = r.get("incident_zip"), r.get("borough")
            if z and b and isinstance(z, str) and z.isdigit() and len(z) == 5:
                pb = self.ref.p_borough(z, b)
                if pb < self.borough_p:
                    det.flag("zip_borough_mismatch", i,
                             f"borough '{b}' has probability {pb:.3f} for ZIP {z}")

    # -- coordinate outliers ----------------------------------------------
    def _coordinate_outliers(self, rows, det: Detection) -> None:
        if self.ref is None:
            return
        mlat, mlon = self.ref.coord_mean
        slat, slon = self.ref.coord_sd
        for i, r in enumerate(rows):
            if not _isnum(r.get("latitude")):
                continue
            lat, lon = float(r["latitude"]), float(r["longitude"])
            zlat = abs(lat - mlat) / slat if slat else 0
            zlon = abs(lon - mlon) / slon if slon else 0
            if zlat > self.coord_z or zlon > self.coord_z:
                det.flag("latlon_swapped", i,
                         f"coordinate {zlat:.0f}sd/{zlon:.0f}sd from the city mean")

    # -- contradiction lexicon drift --------------------------------------
    def _contradiction_drift(self, rows, det: Detection) -> None:
        """Catch a *surge* in contradiction language without naming the phrases.

        The reference records how often resolution text carries a
        negative/unresolved marker. If a batch's Closed rows suddenly carry that
        language far more often than the baseline, the batch is flagged — the
        statistical equivalent of "this column changed distribution", applied to
        prose. It is weaker than the LLM (it only sees the markers it knows) but
        it is free, and it is a genuine unsupervised signal rather than a copy of
        the rule list.
        """
        if self.ref is None or not self.ref.neg_rate:
            return
        from .profile import _NEG
        closed = [i for i, r in enumerate(rows) if r.get("status") == "Closed"]
        if len(closed) < 50:
            return
        hits = [i for i in closed if _NEG.search(rows[i].get("resolution_description") or "")]
        rate = len(hits) / len(closed)
        p0 = self.ref.neg_rate
        sd = max((p0 * (1 - p0) / len(closed)) ** 0.5, 1 / len(closed))
        if rate > p0 + 6 * sd and rate > 2 * p0:
            for i in hits:
                det.flag("resolution_status_contradiction", i,
                         f"Closed row with unresolved language; batch rate {rate:.0%} "
                         f"vs baseline {p0:.1%}")

    # -- null drift --------------------------------------------------------
    def _null_drift(self, rows, det: Detection) -> None:
        if self.ref is None:
            return
        by_agency: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            by_agency[r.get("agency") or "?"].append(i)
        for agency, idxs in by_agency.items():
            if len(idxs) < 50:
                continue
            baseline = self.ref.null_rate.get(agency, {})
            for col in ("incident_zip", "descriptor"):
                p0 = baseline.get(col, 0.0)
                nulls = [i for i in idxs if rows[i].get(col) in (None, "")]
                rate = len(nulls) / len(idxs)
                # A spike is a rate far above the agency's own historical rate,
                # with a floor on the sd so a historically-zero column still has
                # a scale to measure against. The absolute floor stops a single
                # stray null in a small agency from being called a spike.
                sd = max((p0 * (1 - p0) / len(idxs)) ** 0.5, 1 / len(idxs))
                if rate > p0 + 8 * sd and rate >= max(0.02, 5 * p0):
                    for i in nulls:
                        det.flag("null_spike", i,
                                 f"{agency} {col} null rate {rate:.0%} vs baseline {p0:.1%}")


def _isnum(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _minutes_apart(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 1e9
    ta, tb = _parse_any(str(a)), _parse_any(str(b))
    if ta is None or tb is None:
        return 1e9
    return abs((ta - tb).total_seconds()) / 60


def _parse_any(value: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None
