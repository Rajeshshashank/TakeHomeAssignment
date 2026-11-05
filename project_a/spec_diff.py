"""Task 5 change log: diff two tax specs and say what each delta does.

The point of Task 5 is not "did the change break the code" but "how did each
design absorb the change, and which failures were silent". This module produces
the machine-readable half of that answer: a field-by-field diff of
``fy2024_25.yaml`` against ``fy2025_26.yaml``, with a human sentence per delta.

    python -m project_a.spec_diff
"""
from __future__ import annotations

import json
import os
from typing import Any

from .spec import load_spec

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tests", "cases", "spec_diff.json")

NOTES = {
    ("slabs", "new"): "new-regime slab boundaries re-based (rebate-free to 4,00,000; 30% above 24,00,000)",
    ("standard_deduction", "new"): "new-regime standard deduction raised from 75,000 to 1,00,000",
    ("rebate_87a", "new"): "s.87A new-regime rebate threshold raised to 12,00,000, cap to 60,000",
}


def _flat(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if k == "cite":
                continue  # citations change with every Act; not a substantive delta
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list):
        out[prefix] = json.dumps(d, sort_keys=True)
    else:
        out[prefix] = d
    return out


def diff_fys(old_fy: str = "2024-25", new_fy: str = "2025-26") -> list[dict[str, Any]]:
    a, b = _flat(load_spec(old_fy)), _flat(load_spec(new_fy))
    keys = sorted(set(a) | set(b))
    rows = []
    for k in keys:
        if a.get(k) == b.get(k):
            continue
        parts = k.split(".")
        note = NOTES.get((parts[0], parts[1] if len(parts) > 1 else ""), "")
        rows.append({
            "field": k,
            "fy2024_25": a.get(k),
            "fy2025_26": b.get(k),
            "note": note,
        })
    return rows


def main() -> None:
    rows = diff_fys()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"old_fy": "2024-25", "new_fy": "2025-26", "deltas": rows}, fh, indent=1)
    print(f"{len(rows)} substantive deltas between FY 2024-25 and FY 2025-26:")
    for r in rows:
        print(f"  {r['field']:44s} {str(r['fy2024_25'])[:28]:28s} -> {str(r['fy2025_26'])[:28]}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
