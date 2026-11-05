"""Corruption injector for Project B.

Injects each of the twelve problem types from the assignment into a *clean*
batch at a controlled rate, and records exactly which rows (and, for the two
schema-level problems, which batch) were corrupted. Those labels are the ground
truth every detector is measured against, which is the only way to report a
detection rate and a false-alarm rate honestly.

The batch is represented as :class:`Batch`: the rows, the *declared* schema
(column -> type), and the labels. Injecting a renamed column or a type change
mutates the declared schema or the values without touching the labels of other
problems, so problems can be injected independently or stacked.
"""
from __future__ import annotations

import copy
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ..common import build_phone_allowlist, free_text, has_personal_data, iter_jsonl

PROBLEM_TYPES = [
    "renamed_or_dropped_column",
    "column_type_change",
    "date_format_switch",
    "latlon_swapped",
    "exact_duplicates",
    "near_duplicates",
    "null_spike",
    "closed_before_created",
    "zip_borough_mismatch",
    "complaint_descriptor_mismatch",
    "resolution_status_contradiction",
    "pii_in_text",
]

# Column -> logical type, used as the "declared schema" baseline.
BASE_SCHEMA: dict[str, str] = {
    "unique_key": "string",
    "created_date": "datetime",
    "closed_date": "datetime",
    "agency": "string",
    "agency_name": "string",
    "complaint_type": "string",
    "descriptor": "string",
    "location_type": "string",
    "incident_zip": "string",
    "incident_address": "string",
    "street_name": "string",
    "city": "string",
    "status": "string",
    "borough": "string",
    "latitude": "number",
    "longitude": "number",
    "open_data_channel_type": "string",
    "resolution_description": "string",
    "community_board": "string",
    "council_district": "string",
    "police_precinct": "string",
}

# ZIP prefix -> borough, learned from the raw file rather than assumed. NYC ZIPs
# are allocated by borough in contiguous ranges, so the first three digits are a
# strong (not perfect) borough signal.
ZIP_PREFIX_BOROUGH = {
    "100": "MANHATTAN", "101": "MANHATTAN", "102": "MANHATTAN", "103": "STATEN ISLAND",
    "104": "BRONX", "110": "QUEENS", "111": "QUEENS", "112": "BROOKLYN",
    "113": "QUEENS", "114": "QUEENS", "116": "QUEENS",
}

# Descriptor -> the complaint family it belongs to. Used by the rule family to
# flag a descriptor that is *impossible* for a complaint type (a different
# domain), which is a much narrower and more precise check than "not in the
# allowed list".
DESCRIPTOR_DOMAIN = {
    "rodent sighting": "rodent", "mouse sighting": "rodent",
    "condition attracting rodents": "rodent",
    "loud music/party": "noise", "loud talking": "noise", "banging/pounding": "noise",
    "plumbing": "building", "elevator": "building",
    "heat/hot water": "heat", "street light condition": "street",
    "food establishment": "food", "air quality": "air",
    "water leak": "water", "water quality": "water", "water pressure": "water",
}
COMPLAINT_DOMAIN = [
    ("noise", "noise"), ("rodent", "rodent"), ("parking", "parking"),
    ("heat", "heat"), ("water", "water"), ("air", "air"), ("food", "food"),
    ("street", "street"), ("plumbing", "building"), ("elevator", "building"),
]

# Plausible coordinate envelope for the city the dataset covers, with a small
# margin. A declarative rule can use a configured bounding box to catch a
# latitude/longitude swap that a pure range check misses: -73.9 is a legal
# latitude in the abstract, but it is not a legal latitude *for New York*.
CITY_BBOX = {"lat": (40.4, 41.1), "lon": (-74.3, -73.6)}

FAKE_NAMES = ["Rajesh Kumar", "Maria Lopez", "John Smith", "Aisha Khan", "Chen Wei"]
FAKE_PHONES = ["(917) 555-0143", "646-555-0198", "212.555.0177"]
FAKE_EMAILS = ["rajesh.kumar@gmail.com", "m.lopez@yahoo.com", "resident1432@outlook.com"]

STATUS_RESOLUTION_CONTRADICTIONS = [
    ("Closed", "This complaint is still open and under investigation."),
    ("Closed", "No action was taken; the request remains pending."),
    ("Open", "The case was resolved and closed on the same day."),
    ("In Progress", "The matter has been fully resolved and closed."),
]

DESCRIPTOR_MISMATCH = {
    "Noise - Residential": ["Rodent Sighting", "Plumbing", "Food Establishment"],
    "Rodent": ["Loud Music/Party", "Elevator", "Water Quality"],
    "Illegal Parking": ["Rodent Sighting", "Heat/Hot Water", "Air Quality"],
    "Water System": ["Loud Music/Party", "Street Light Condition", "Dirty Condition"],
}
# Descriptors that are never valid for the complaint types they are attached to
# in the injector, drawn from the vocabulary of a different complaint family.
UNRELATED_DESCRIPTOR = {
    "Noise - Residential": "Rodent Sighting",
    "Noise - Commercial": "Plumbing",
    "Rodent": "Loud Music/Party",
    "Illegal Parking": "Heat/Hot Water",
    "Water System": "Street Light Condition",
    "Street Condition": "Food Establishment",
    "HEAT/HOT WATER": "Air Quality",
    "Dirty Condition": "Elevator",
    "Air Quality": "Noise",
    "Food Poisoning": "Water Leak",
}


@dataclass
class Batch:
    rows: list[dict[str, Any]]
    schema: dict[str, str] = field(default_factory=lambda: dict(BASE_SCHEMA))
    # problem -> row indices known to be corrupted with that problem
    labels: dict[str, set[int]] = field(default_factory=dict)
    # problem -> True when the *batch* is corrupted at schema level
    batch_labels: dict[str, bool] = field(default_factory=dict)

    def copy(self) -> "Batch":
        return Batch(copy.deepcopy(self.rows), dict(self.schema),
                     {k: set(v) for k, v in self.labels.items()}, dict(self.batch_labels))

    def mark(self, problem: str, idx: int) -> None:
        self.labels.setdefault(problem, set()).add(idx)


def _iso(dt: Any) -> Optional[str]:
    return dt if isinstance(dt, str) else None


def _swap_date_format(value: str) -> str:
    """Turn '2023-01-05T00:00:00.000' into '05/01/2023 00:00:00'."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(.*)", value or "")
    if not m:
        return value
    y, mo, d, rest = m.groups()
    rest = rest.split(".")[0]
    return f"{d}/{mo}/{y} {rest}"


def _maybe_pii(rng: random.Random) -> str:
    return rng.choice([
        f"Resident called from {rng.choice(FAKE_PHONES)} to follow up.",
        f"Please contact {rng.choice(FAKE_NAMES)} at {rng.choice(FAKE_EMAILS)}.",
        f"Tenant {rng.choice(FAKE_NAMES)} reports the issue; callback {rng.choice(FAKE_PHONES)}.",
    ])


class Injector:
    """Injects one problem type at a time into a clean batch."""

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)

    def inject(self, clean: Batch, problem: str, rate: float = 0.05,
               n_problems: int = 1) -> Batch:
        if problem not in PROBLEM_TYPES:
            raise ValueError(f"unknown problem type: {problem}")
        batch = clean.copy()
        n = len(batch.rows)
        k = max(1, int(round(rate * n))) if problem not in (
            "renamed_or_dropped_column", "column_type_change", "date_format_switch",
            "null_spike", "exact_duplicates", "near_duplicates") else max(1, int(round(rate * n)))
        targets = self.rng.sample(range(n), min(k, n))
        handler: Callable[[Batch, list[int]], None] = getattr(self, f"_inj_{problem}")
        handler(batch, targets)
        return batch

    # -- schema level ------------------------------------------------------
    def _inj_renamed_or_dropped_column(self, b: Batch, targets: list[int]) -> None:
        # Half the batches rename a column, half drop one; both must be caught.
        if self.rng.random() < 0.5:
            old, new = "borough", "boro"
            for r in b.rows:
                if old in r:
                    r[new] = r.pop(old)
            b.schema[new] = b.schema.pop(old)
        else:
            for r in b.rows:
                r.pop("police_precinct", None)
            b.schema.pop("police_precinct", None)
        b.batch_labels["renamed_or_dropped_column"] = True

    def _inj_column_type_change(self, b: Batch, targets: list[int]) -> None:
        # A number column starts arriving as strings, the classic upstream bug.
        for i in targets:
            for col in ("latitude", "longitude"):
                v = b.rows[i].get(col)
                if v is not None:
                    b.rows[i][col] = str(v)
                    b.mark("column_type_change", i)

    def _inj_date_format_switch(self, b: Batch, targets: list[int]) -> None:
        # Switch format for the *tail* of the batch, as a mid-batch upstream
        # config change would.
        cut = min(targets) if targets else len(b.rows) // 2
        for i in range(cut, len(b.rows)):
            for col in ("created_date", "closed_date"):
                if b.rows[i].get(col):
                    b.rows[i][col] = _swap_date_format(b.rows[i][col])
                    b.mark("date_format_switch", i)

    # -- row level ---------------------------------------------------------
    def _inj_latlon_swapped(self, b: Batch, targets: list[int]) -> None:
        for i in targets:
            lat, lon = b.rows[i].get("latitude"), b.rows[i].get("longitude")
            if lat and lon:
                b.rows[i]["latitude"], b.rows[i]["longitude"] = lon, lat
                b.mark("latlon_swapped", i)

    def _inj_exact_duplicates(self, b: Batch, targets: list[int]) -> None:
        dupes = []
        for i in targets:
            row = copy.deepcopy(b.rows[i])
            dupes.append(row)
        start = len(b.rows)
        b.rows.extend(dupes)
        for j in range(len(dupes)):
            b.mark("exact_duplicates", start + j)

    def _inj_near_duplicates(self, b: Batch, targets: list[int]) -> None:
        start = len(b.rows)
        added = []
        for i in targets:
            row = copy.deepcopy(b.rows[i])
            # Same request, tiny alteration: nudge the timestamp and tweak text.
            if row.get("created_date", "").startswith("2023-"):
                row["created_date"] = row["created_date"].replace("T00:00", "T00:07")
            row["resolution_description"] = (
                (row.get("resolution_description") or "") + " (re-submitted)")
            # A re-submission gets a new key (that is why it is a *near* dup).
            row["unique_key"] = str(int(row.get("unique_key") or 0) + 900000000)
            added.append(row)
        b.rows.extend(added)
        for j in range(len(added)):
            b.mark("near_duplicates", start + j)

    def _inj_null_spike(self, b: Batch, targets: list[int]) -> None:
        # One agency's feed suddenly stops populating a normally-full column.
        # Nulling most of one agency's rows (not a scatter of rows) is what makes
        # it a *spike* rather than diffuse sparsity, and is how the failure
        # actually presents in an upstream feed.
        agency = Counter(r.get("agency") for r in b.rows).most_common(1)[0][0]
        agency_rows = [i for i, r in enumerate(b.rows) if r.get("agency") == agency]
        k = max(1, int(round(0.6 * len(agency_rows))))
        for i in self.rng.sample(agency_rows, min(k, len(agency_rows))):
            b.rows[i]["incident_zip"] = None
            b.rows[i]["descriptor"] = None
            b.mark("null_spike", i)

    def _inj_closed_before_created(self, b: Batch, targets: list[int]) -> None:
        for i in targets:
            created = b.rows[i].get("created_date")
            if created and created.startswith("2023-"):
                # Close it the day *before* it was created.
                b.rows[i]["closed_date"] = created.replace("-01T", "-01T")[:8] + "01T00:00:00.000"
                y, mo, d = created[:4], created[5:7], created[8:10]
                prev = f"{y}-{mo}-{max(1, int(d) - 1):02d}"
                b.rows[i]["closed_date"] = f"{prev}T00:00:00.000"
                b.mark("closed_before_created", i)

    def _inj_zip_borough_mismatch(self, b: Batch, targets: list[int]) -> None:
        for i in targets:
            z = b.rows[i].get("incident_zip")
            if not z:
                continue
            # Pick a ZIP that belongs to a *different* borough, so the row is a
            # genuine mismatch rather than a lucky same-borough replacement.
            row_borough = b.rows[i].get("borough")
            candidates = [c for c, bor in ZIP_PREFIX_BOROUGH.items() if bor != row_borough]
            prefix = self.rng.choice(candidates)
            b.rows[i]["incident_zip"] = prefix + str(self.rng.randint(10, 99))
            b.mark("zip_borough_mismatch", i)

    def _inj_complaint_descriptor_mismatch(self, b: Batch, targets: list[int]) -> None:
        for i in targets:
            ct = b.rows[i].get("complaint_type")
            b.rows[i]["descriptor"] = UNRELATED_DESCRIPTOR.get(
                ct, "Rodent Sighting" if ct != "Rodent" else "Loud Music/Party")
            b.mark("complaint_descriptor_mismatch", i)

    def _inj_resolution_status_contradiction(self, b: Batch, targets: list[int]) -> None:
        for i in targets:
            status, text = self.rng.choice(STATUS_RESOLUTION_CONTRADICTIONS)
            b.rows[i]["status"] = status
            b.rows[i]["resolution_description"] = text
            b.mark("resolution_status_contradiction", i)

    def _inj_pii_in_text(self, b: Batch, targets: list[int]) -> None:
        for i in targets:
            b.rows[i]["resolution_description"] = (
                (b.rows[i].get("resolution_description") or "") + " " + _maybe_pii(self.rng))
            b.mark("pii_in_text", i)


# ---------------------------------------------------------------------------
# Curating a genuinely clean batch.
#
# The raw 2023 extract already contains several of the twelve problems (Task 1
# documents them). Measuring a detector's false-alarm rate on data that already
# contains the problem it looks for is meaningless, so the clean corpus excludes
# any row that exhibits a known problem signature. The filters below are
# deliberately simple and conservative; the counts of what was removed are
# printed by scripts/profile_raw.py.
# ---------------------------------------------------------------------------
_NEG = re.compile(r"still open|remains? (?:open|pending)|under investigation|no action|"
                  r"not (?:been )?addressed|unable to (?:gain|access|locate|find)|"
                  r"will not be|could not (?:find|access)", re.I)
_RES = re.compile(r"resolved|closed|completed|corrected|inspected|fixed|addressed|serviced", re.I)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _row_is_clean(r: dict[str, Any], allowlist: set[str]) -> bool:
    if not r.get("latitude") or not r.get("longitude"):
        return False
    if not r.get("incident_zip") or not r.get("borough") or not r.get("descriptor"):
        return False
    if r.get("status") in (None, "Unspecified"):
        return False
    if not (_ISO.match(str(r.get("created_date", ""))) and _ISO.match(str(r.get("closed_date", "")))):
        return False
    if r.get("closed_date") and r["closed_date"] < r["created_date"]:
        return False
    text = r.get("resolution_description") or ""
    status = r.get("status") or ""
    if status == "Closed" and _NEG.search(text):
        return False
    if status in ("Open", "In Progress", "Pending", "Assigned", "Started") and _RES.search(text):
        return False
    if has_personal_data(free_text(r), allowlist):
        return False
    ct = (r.get("complaint_type") or "").lower()
    d = (r.get("descriptor") or "").strip().lower()
    dd = DESCRIPTOR_DOMAIN.get(d)
    if dd:
        cd = ""
        for needle, domain in COMPLAINT_DOMAIN:
            if needle in ct:
                cd = domain
                break
        if cd and cd != dd:
            return False
    z = r.get("incident_zip")
    if isinstance(z, str) and z[:3] in ZIP_PREFIX_BOROUGH \
            and ZIP_PREFIX_BOROUGH[z[:3]] != r.get("borough"):
        return False
    return True


def load_clean_rows(path: str) -> list[dict[str, Any]]:
    """Return the subset of the extract that exhibits none of the twelve
    problems: the population a reference profile and a false-alarm measurement
    should both be based on."""
    rows: list[dict[str, Any]] = list(iter_jsonl(path))

    # The allowlist must be learned from the whole extract, not the surviving
    # clean rows, or a hotline that only appears on rows we dropped would look
    # like personal data.
    allowlist = build_phone_allowlist(rows)

    clean: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_content: set[str] = set()
    for r in rows:
        if not _row_is_clean(r, allowlist):
            continue
        if r.get("unique_key") in seen_keys:
            continue
        seen_keys.add(r.get("unique_key"))
        sig = (r.get("complaint_type"), r.get("incident_address"), r.get("created_date"))
        if sig in seen_content:
            continue
        seen_content.add(sig)
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
        clean.append(r)
    return clean


def load_clean_batches(path: str, batch_size: int = 2000,
                       n_batches: int = 12, seed: int = 7) -> list[Batch]:
    """Split the clean rows into shuffled batches of a fixed size."""
    clean = load_clean_rows(path)
    rng = random.Random(seed)
    rng.shuffle(clean)
    clean = clean[:batch_size * n_batches]
    return [Batch(rows=clean[i * batch_size:(i + 1) * batch_size])
            for i in range(n_batches)]
