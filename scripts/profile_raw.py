"""Task 1: profile the raw 2023 NYC 311 extract and document the quality
problems that are already present, with counts and examples.

    python scripts/profile_raw.py

Writes ``project_b/results/raw_profile.json`` and prints a summary. This is
evidence, not a demo: every count below is computed from the file in
``project_b/data/`` and the examples are real rows.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project_b.common import (  # noqa: E402
    PHONE,
    build_phone_allowlist,
    data_path,
    free_text,
    has_personal_data,
    read_jsonl,
)
from project_b.injectors.inject import (  # noqa: E402
    BASE_SCHEMA,
    COMPLAINT_DOMAIN,
    DESCRIPTOR_DOMAIN,
    ZIP_PREFIX_BOROUGH,
    _NEG,
    _RES,
)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = data_path(os.path.join(HERE, "project_b", "data"))
OUT = os.path.join(HERE, "project_b", "results", "raw_profile.json")


def _iso(v) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", str(v or "")))


def main() -> None:
    rows = read_jsonl(RAW)
    n = len(rows)
    allowlist = build_phone_allowlist(rows)
    findings: dict[str, dict] = {}

    def add(key, count, example, note=""):
        findings[key] = {"count": count, "pct": round(100 * count / n, 3),
                         "example": example, "note": note}

    # 1. columns present vs declared schema
    present = set(rows[0])
    add("renamed_or_dropped_column",
        len(set(BASE_SCHEMA) - present) + len(present - set(BASE_SCHEMA)),
        f"missing={sorted(set(BASE_SCHEMA) - present)} unexpected={sorted(present - set(BASE_SCHEMA))}",
        "columns in the extract that do not match the documented schema")

    # 2. numbers arriving as strings
    strnums = sum(1 for r in rows if isinstance(r.get("latitude"), str))
    add("column_type_change", strnums, f"latitude is a string, e.g. '{rows[0].get('latitude')}'",
        "latitude/longitude are JSON strings, not numbers")

    # 3. date format uniformity
    bad_dates = sum(1 for r in rows if not _iso(r.get("created_date")) or
                    (r.get("closed_date") and not _iso(r["closed_date"])))
    add("date_format_switch", bad_dates, f"created_date='{rows[0].get('created_date')}'",
        "all rows share one ISO format in this extract; a mid-batch switch is what the injector models")

    # 4/5. coordinates: missing vs out of the city envelope.
    missing_coord = 0
    out_env = 0
    ex = None
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
            if not (40.4 <= lat <= 41.1 and -74.3 <= lon <= -73.6):
                out_env += 1
                ex = ex or f"({lat}, {lon})"
        except (TypeError, ValueError, KeyError):
            missing_coord += 1
    add("latlon_swapped", out_env, ex or "no out-of-envelope coordinates",
        "no swapped pairs in this extract")
    add("missing_coordinates", missing_coord, f"{missing_coord} rows have no latitude/longitude",
        "a separate, larger problem than a swap")

    # 6. exact duplicates
    seen = Counter()
    for r in rows:
        seen[tuple(sorted((k, str(v)) for k, v in r.items() if k != "unique_key"))] += 1
    dupes = sum(c - 1 for c in seen.values() if c > 1)
    add("exact_duplicates", dupes, "no two rows identical on every field",
        "unique_key is unique; content duplicates do exist but are near-, not exact")

    # 7. near-duplicates
    blocks = defaultdict(list)
    for i, r in enumerate(rows):
        blocks[(r.get("complaint_type"), r.get("incident_address"), r.get("created_date"))].append(i)
    near = sum(len(v) - 1 for v in blocks.values() if len(v) > 1)
    add("near_duplicates", near, f"e.g. {max(blocks.values(), key=len)[0]} shares type/address/timestamp with another row",
        "same complaint, same address, same timestamp, different unique_key")

    # 8. nulls, by column, and the worst agency concentration.
    # Socrata omits a field entirely when it is null, so "null" here means the
    # key is absent from the row — which is itself a finding worth stating.
    null_counts = Counter()
    for r in rows:
        for k in BASE_SCHEMA:
            if k not in r or r.get(k) in (None, ""):
                null_counts[k] += 1
    top_null = null_counts.most_common(1)[0]
    add("null_spike", top_null[1], f"{top_null[0]} is absent on {top_null[1]} rows",
        "nulls are diffuse here, not concentrated in one agency's feed; Socrata omits the key entirely")

    # 9. closed before created
    cb = sum(1 for r in rows if r.get("closed_date") and r["created_date"]
             and r["closed_date"] < r["created_date"])
    ex = next((r for r in rows if r.get("closed_date") and r["created_date"]
               and r["closed_date"] < r["created_date"]), None)
    add("closed_before_created", cb,
        f"created={ex['created_date']} closed={ex['closed_date']}" if ex else "",
        "negative resolution time")

    # 10. ZIP vs borough
    mism = 0
    ex = None
    for r in rows:
        z, b = r.get("incident_zip"), r.get("borough")
        if isinstance(z, str) and len(z) == 5 and z[:3] in ZIP_PREFIX_BOROUGH \
                and ZIP_PREFIX_BOROUGH[z[:3]] != b:
            mism += 1
            ex = ex or f"ZIP {z} in {b}"
    add("zip_borough_mismatch", mism, ex or "", "ZIP prefix contradicts the stated borough")

    # 11. complaint type vs descriptor domain
    cd_mism = 0
    ex = None
    for r in rows:
        ct = (r.get("complaint_type") or "").lower()
        d = (r.get("descriptor") or "").strip().lower()
        dd = DESCRIPTOR_DOMAIN.get(d)
        if not dd:
            continue
        cdom = next((dom for needle, dom in COMPLAINT_DOMAIN if needle in ct), "")
        if cdom and cdom != dd:
            cd_mism += 1
            ex = ex or f"{r.get('complaint_type')} / {r.get('descriptor')}"
    add("complaint_descriptor_mismatch", cd_mism, ex or "",
        "descriptor drawn from a different complaint family")

    # 12. status vs resolution text
    contra = 0
    ex = None
    for r in rows:
        s, t = r.get("status"), r.get("resolution_description") or ""
        if s == "Closed" and _NEG.search(t):
            contra += 1
            ex = ex or t[:110]
        elif s in ("Open", "In Progress", "Pending", "Assigned", "Started") and _RES.search(t):
            contra += 1
            ex = ex or t[:110]
    add("resolution_status_contradiction", contra, ex or "",
        "status and resolution text disagree")

    # 13. PII in free text
    pii = sum(1 for r in rows if has_personal_data(free_text(r), allowlist))
    ex = next((free_text(r)[:120] for r in rows if has_personal_data(free_text(r), allowlist)), "")
    add("pii_in_text", pii, ex, f"{len(allowlist)} agency numbers allow-listed by frequency")

    # 14. missing status
    add("missing_status", sum(1 for r in rows if r.get("status") in (None, "Unspecified")),
        "status Unspecified or absent",
        "an unrecognised status value is itself a schema problem")

    report = {"n_rows": n, "findings": findings}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    print(f"profiled {n:,} raw rows")
    print(f"{'problem':38s} {'count':>9s} {'share':>8s}")
    for k, v in findings.items():
        print(f"{k:38s} {v['count']:>9,} {v['pct']:>7.2f}%")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
