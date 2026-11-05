#!/usr/bin/env bash
# One command that reproduces every number reported in RESULTS_A.md and
# RESULTS_B.md.
#
#   bash scripts/run_all.sh
#
# It is offline by default: the LLM layers run against deterministic proxies
# (project_a/llm/proxy.py, project_b/detectors/llm_review.py) so no API key is
# needed. Export GEMINI_API_KEY or OPENAI_API_KEY to re-run the LLM layers
# against a real model instead; the harnesses pick it up automatically and the
# result files record which mode was used.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The repo ships the raw extract gzipped. Expand it once if only the .gz exists
# so the profiler and detector harness read the same file the measurements used.
if [ ! -f project_b/data/nyc311_2023_sample.jsonl ] \
   && [ -f project_b/data/nyc311_2023_sample.jsonl.gz ]; then
  echo "== expanding project_b/data/nyc311_2023_sample.jsonl.gz =="
  gunzip -k -f project_b/data/nyc311_2023_sample.jsonl.gz
fi

echo "== Project A: test-case corpus =="
python3 -m project_a.tests.generate_cases

echo
echo "== Project A: three implementations =="
python3 -m project_a.measure_a

echo
echo "== Project A: FY 2025-26 change log (spec diff) =="
python3 -m project_a.spec_diff

echo
echo "== Project A: official-calculator cross-check (30 cases) =="
python3 -m project_a.verify_official

echo
echo "== Project B: raw-data profile (Task 1) =="
python3 scripts/profile_raw.py

echo
echo "== Project B: reference profile from clean rows =="
python3 scripts/build_reference.py

echo
echo "== Project B: injector + three detector families + hybrid =="
python3 -m project_b.measure_b

echo
echo "== Test suites (includes the docs-vs-artifacts consistency checks) =="
python3 -m pytest project_a/tests project_b/tests tests -q

echo
echo "All numbers regenerated. See RESULTS_A.md, RESULTS_B.md, DECISION_MATRIX.md."
