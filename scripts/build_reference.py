"""Build the frozen reference profile used by the statistical detectors.

    python scripts/build_reference.py

Reads the raw sample and writes ``project_b/detectors/reference.json``. Frozen
on purpose: if the reference were rebuilt on the batch being scored, every
distribution-drift check would compare a batch against itself and never fire.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project_b.detectors.profile import build_reference, save_reference  # noqa: E402
from project_b.injectors.inject import load_clean_rows  # noqa: E402
from project_b.common import data_path  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = data_path(os.path.join(ROOT, "project_b", "data"))
OUT = os.path.join(ROOT, "project_b", "detectors", "reference.json")


def main() -> None:
    rows = load_clean_rows(RAW)
    ref = build_reference(rows)
    save_reference(ref, OUT)
    print(f"reference built from {ref.n_rows} clean rows -> {OUT}")
    print(f"  complaint types: {len(ref.descriptor_by_type)}")
    print(f"  ZIPs with borough history: {len(ref.borough_by_zip)}")
    print(f"  agencies: {len(ref.null_rate)}")
    print(f"  coord mean={ref.coord_mean} sd={ref.coord_sd}")
    print(f"  P(unresolved language | Closed) = {ref.neg_rate:.4f}")
    print(f"  agency phone allowlist: {len(ref.phone_allowlist)} numbers")


if __name__ == "__main__":
    main()
