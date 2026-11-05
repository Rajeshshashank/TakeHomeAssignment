"""Download a representative 2023 sample of NYC 311 service requests.

Pulls a spread of rows across all twelve months of 2023 from the Socrata
Open Data API (dataset erm2-nwe9) so profiling and detector measurements see
seasonal and categorical variety rather than one contiguous block.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

DATASET = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
OUT = os.path.join(os.path.dirname(__file__), "..", "project_b", "data", "nyc311_2023_sample.jsonl")
PER_MONTH = int(os.environ.get("PER_MONTH", "12000"))

# Keep the payload lean: only the columns the detectors reason about.
SELECT = ",".join([
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "location_type", "incident_zip",
    "incident_address", "street_name", "city", "status", "borough",
    "latitude", "longitude", "open_data_channel_type", "resolution_description",
    "community_board", "council_district", "police_precinct",
])


def fetch(params):
    url = DATASET + "?" + urllib.parse.urlencode(params, safe=",()$'")
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fireai-takehome/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} after {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    total = 0
    with open(OUT, "w", encoding="utf-8") as fh:
        for month in range(1, 13):
            start = f"2023-{month:02d}-01T00:00:00"
            end = "2024-01-01T00:00:00" if month == 12 else f"2023-{month + 1:02d}-01T00:00:00"
            params = {
                "$select": SELECT,
                "$where": f"created_date>='{start}' AND created_date<'{end}'",
                "$order": "created_date",
                "$limit": PER_MONTH,
            }
            rows = fetch(params)
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            total += len(rows)
            print(f"month {month:02d}: +{len(rows)} (total {total})", flush=True)
    print(f"WROTE {total} rows -> {os.path.abspath(OUT)}")


if __name__ == "__main__":
    main()
