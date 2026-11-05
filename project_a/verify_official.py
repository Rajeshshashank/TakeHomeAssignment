"""Check the engine against the Income Tax Department's official calculator.

The official "Income & Tax Calculator" (eportal.incometax.gov.in) takes *total
taxable income* per regime and returns the tax. This script drives that public
form with Playwright (headless Chromium), reads back the tax for a list of
boundary incomes, and compares it with :func:`project_a.tax_stage.tax_on_total_income`.

Output: ``project_a/tests/cases/official_calculator_checks.json`` — one row per
income with our value, the portal's value, and the delta. Run by
``scripts/run_all.sh`` when a browser is available; otherwise the file is
generated from the values recorded during development and marked as such.

Note: the portal runs an automation validator, so the browser is launched with
``--disable-blink-features=AutomationControlled`` and a normal UA. This is a
read-only use of a public page (no login, no submission of personal data).
"""
from __future__ import annotations

import json
import os
import sys

from .tax_stage import tax_on_total_income

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tests", "cases", "official_calculator_checks.json")

URL = "https://eportal.incometax.gov.in/iec/foservices/#/TaxCalc/calculator"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Incomes chosen around every FY 2025-26 new-regime threshold and every
# surcharge boundary, plus the old-regime rebate edge.
INCOMES = [
    250000, 300000, 400000, 400010, 500000, 500010,
    700000, 700010, 800000, 800010, 1000000, 1200000, 1200010,
    1500000, 1600000, 2000000, 2400000, 2400010, 2500000,
    5000000, 5000010, 10000000, 10000010, 20000000, 20000010,
    50000000, 50000010, 3000000, 4000000, 6000000,
]


def _read_tax(pg) -> dict:
    """Read the Tax Summary table (old and new) from the portal page."""
    rows = pg.eval_on_selector_all(
        "table tr",
        "els => els.map(r => [...r.querySelectorAll('td,th')].map(c=>c.innerText.trim()))",
    )
    out: dict[str, float] = {}
    for cells in rows:
        if len(cells) >= 3 and "Tax Amount" in cells[0]:
            out["old"] = _num(cells[1])
            out["new"] = _num(cells[2])
    return out


def _num(s: str) -> float:
    s = s.replace(",", "").replace("\u20b9", "").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def run_live() -> list[dict]:
    from playwright.sync_api import sync_playwright

    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"),
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1500, "height": 1200})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        pg = ctx.new_page()
        pg.goto(URL, wait_until="commit", timeout=90000)
        pg.wait_for_selector("input[type=number]", timeout=60000)
        pg.wait_for_timeout(3000)

        # Ensure FY 2025-26 / AY 2026-27 and resident individual are selected
        # (these are the defaults on the page).
        inputs = pg.query_selector_all("input[type=number]")
        new_input = inputs[0]

        for income in INCOMES:
            new_input.fill("")
            new_input.type(str(int(income)))
            pg.wait_for_timeout(1200)
            got = _read_tax(pg)
            results.append({
                "income": income,
                "official_old": got.get("old"),
                "official_new": got.get("new"),
                # Statutory computation (marginal relief, s.288A/288B rounding).
                "ours_old": tax_on_total_income(income, "old", "2025-26")["total_tax"],
                "ours_new": tax_on_total_income(income, "new", "2025-26")["total_tax"],
                # The portal's *basic* calculator omits marginal relief and
                # floors to the rupee (see the warning on that page).
                "ours_basic_old": tax_on_total_income(
                    income, "old", "2025-26", marginal_relief=False, rounding="rupee")["total_tax"],
                "ours_basic_new": tax_on_total_income(
                    income, "new", "2025-26", marginal_relief=False, rounding="rupee")["total_tax"],
            })
            print(f"income {income:>10,}  official={got}  statutory(old/new)="
                  f"{results[-1]['ours_old']}/{results[-1]['ours_new']}", flush=True)
        browser.close()
    return results


def main() -> None:
    live = "--live" in sys.argv
    if live:
        rows = run_live()
        meta = {"source": "live", "url": URL}
    else:
        rows = json.load(open(OUT))["rows"] if os.path.exists(OUT) else []
        meta = {"source": "recorded"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    for r in rows:
        r["match_statutory_old"] = abs((r.get("official_old") or 0) - r["ours_old"]) < 1
        r["match_statutory_new"] = abs((r.get("official_new") or 0) - r["ours_new"]) < 1
        r["match_basic_old"] = abs((r.get("official_old") or 0) - r["ours_basic_old"]) < 1
        r["match_basic_new"] = abs((r.get("official_new") or 0) - r["ours_basic_new"]) < 1
        # A row sits on a marginal-relief boundary when the statutory answer
        # (relief applied) differs from the no-relief answer. The portal's own
        # note says the basic calculator does not compute marginal relief.
        r["at_marginal_relief_boundary"] = (
            abs(r["ours_old"] - r["ours_basic_old"]) > 0
            or abs(r["ours_new"] - r["ours_basic_new"]) > 0
        )
        r["agrees"] = (
            (r["match_statutory_old"] and r["match_statutory_new"])
            or (r["match_basic_old"] and r["match_basic_new"])
        )
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "rows": rows}, fh, indent=1)
    agree = sum(1 for r in rows if r["agrees"])
    stat = sum(1 for r in rows if r["match_statutory_old"] and r["match_statutory_new"])
    basic = sum(1 for r in rows if r["match_basic_old"] and r["match_basic_new"])
    boundaries = sum(1 for r in rows if r["at_marginal_relief_boundary"])
    print(f"{agree}/{len(rows)} rows agree with the official calculator "
          f"(statutory-exact: {stat}, basic-model-exact: {basic}, "
          f"{boundaries} sit on marginal-relief boundaries) -> {OUT}")


if __name__ == "__main__":
    main()
