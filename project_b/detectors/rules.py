"""Family (a): declarative rules and schema checks.

Cheap, deterministic, explainable, and run on every row. Each check carries the
one-sentence reason it would show an analyst. The rules encode the *contract*
the dataset is supposed to satisfy: a declared schema, ISO-8601 timestamps,
plausible coordinates, ZIP-in-borough, closed-after-created, a descriptor from
the right domain, and no personal data in free text.

The rules are deliberately conservative about what they flag: a rule that fires
on a legal value is worse than useless, because it trains the team to ignore
alerts. Where a rule cannot be certain ("is this descriptor merely unusual for
this complaint type?") it does not guess — that judgement is delegated to the
statistical and LLM families.
"""
from __future__ import annotations

import re
from typing import Any

from ..injectors.inject import (
    BASE_SCHEMA,
    CITY_BBOX,
    COMPLAINT_DOMAIN,
    DESCRIPTOR_DOMAIN,
    ZIP_PREFIX_BOROUGH,
)
from .base import Detection
from ..common import free_text, has_personal_data

ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


class RuleDetector:
    name = "rules"
    family = "rule"

    def __init__(self, schema: dict[str, str] | None = None,
                 phone_allowlist: set[str] | None = None) -> None:
        self.schema = schema or BASE_SCHEMA
        self.phone_allowlist = phone_allowlist or set()

    def detect(self, batch) -> Detection:
        det = Detection(self.name, self.family, rows_examined=len(batch.rows))
        self._schema_checks(batch, det)
        for i, row in enumerate(batch.rows):
            self._row_checks(row, i, det)
        return det

    # -- schema-level ------------------------------------------------------
    def _schema_checks(self, batch, det: Detection) -> None:
        missing = [c for c in BASE_SCHEMA if c not in batch.schema]
        renamed = [c for c in batch.schema if c not in BASE_SCHEMA]
        if missing or renamed:
            det.batch_flags.add("renamed_or_dropped_column")
            det.reasons.setdefault("renamed_or_dropped_column", {})[-1] = (
                f"schema drift: missing={missing} unexpected={renamed}")
        if not batch.rows:
            return
        cols = set(batch.rows[0])
        for col, logical in BASE_SCHEMA.items():
            if col not in cols or logical != "number":
                continue
            bad = [i for i, r in enumerate(batch.rows)
                   if r.get(col) is not None and isinstance(r[col], str)]
            if bad:
                det.batch_flags.add("column_type_change")
                for i in bad:
                    det.flag("column_type_change", i,
                             f"{col} arrived as a string ('{batch.rows[i][col]}')")
        for col in ("created_date", "closed_date"):
            vals = [r.get(col) for r in batch.rows if r.get(col)]
            if vals and any(not ISO_DATETIME.match(str(v)) for v in vals):
                det.batch_flags.add("date_format_switch")
                for i, r in enumerate(batch.rows):
                    if r.get(col) and not ISO_DATETIME.match(str(r[col])):
                        det.flag("date_format_switch", i,
                                 f"{col} is not ISO-8601 ('{r[col]}')")

    # -- row-level ---------------------------------------------------------
    def _row_checks(self, row: dict[str, Any], i: int, det: Detection) -> None:
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is not None and lon is not None:
            if _isnum(lat) and _isnum(lon):
                flat, flon = float(lat), float(lon)
                if not (-90 <= flat <= 90 and -180 <= flon <= 180):
                    det.flag("latlon_swapped", i, f"coordinate out of range ({flat}, {flon})")
                elif not _in_bbox(flat, flon) and _in_bbox(flon, flat):
                    det.flag("latlon_swapped", i,
                             f"({flat}, {flon}) is outside the city envelope but "
                             f"({flon}, {flat}) is inside it — latitude and longitude "
                             f"look swapped")
            else:
                det.flag("latlon_swapped", i, f"non-numeric coordinate ({lat!r}, {lon!r})")

        created, closed = row.get("created_date"), row.get("closed_date")
        if created and closed and str(closed) < str(created):
            det.flag("closed_before_created", i, f"closed {closed} < created {created}")

        z, borough = row.get("incident_zip"), row.get("borough")
        if z and borough and isinstance(z, str) and z.isdigit() and len(z) == 5:
            prefix = z[:3]
            if prefix in ZIP_PREFIX_BOROUGH and ZIP_PREFIX_BOROUGH[prefix] != borough:
                det.flag("zip_borough_mismatch", i,
                         f"ZIP {z} is {ZIP_PREFIX_BOROUGH[prefix]}, row says {borough}")

        ct, desc = row.get("complaint_type"), row.get("descriptor")
        if ct and desc:
            d_domain = DESCRIPTOR_DOMAIN.get(desc.strip().lower())
            if d_domain:
                c_domain = _complaint_domain(ct)
                if c_domain and c_domain != d_domain:
                    det.flag("complaint_descriptor_mismatch", i,
                             f"descriptor '{desc}' is a {d_domain} descriptor but "
                             f"'{ct}' is a {c_domain} complaint")

        text = free_text(row)
        reason = has_personal_data(text, self.phone_allowlist)
        if reason:
            det.flag("pii_in_text", i, reason)


def _complaint_domain(complaint_type: str) -> str:
    ct = complaint_type.lower()
    for needle, domain in COMPLAINT_DOMAIN:
        if needle in ct:
            return domain
    return ""


def _in_bbox(lat: float, lon: float) -> bool:
    lo, hi = CITY_BBOX["lat"]
    lo2, hi2 = CITY_BBOX["lon"]
    return lo <= lat <= hi and lo2 <= lon <= hi2


def _isnum(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
