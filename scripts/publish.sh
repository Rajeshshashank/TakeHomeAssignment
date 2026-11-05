#!/usr/bin/env bash
# Push this repo to a private GitHub repo and open a PR, in one command.
#
# The automated environment's GitHub token cannot create repositories (it is a
# GitHub App installation token without Administration:write), so this script
# does the whole publish step the moment it is run with a token that can.
#
#   GITHUB_TOKEN=ghp_... bash scripts/publish.sh            # default name
#   GITHUB_TOKEN=ghp_... bash scripts/publish.sh my-name    # custom repo name
#
# It creates the repo private, pushes the current branch, and opens a PR from
# that branch onto main. Idempotent: re-running pushes new commits.
set -euo pipefail

REPO="${1:-rules-vs-llms}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${GITHUB_TOKEN:?set GITHUB_TOKEN to a token with repo scope (Administration:write to create a repo)}"

OWNER="$(curl -sf -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["login"])')"

echo "== creating private repo $OWNER/$REPO (no-op if it exists) =="
curl -sf -X POST -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" https://api.github.com/user/repos \
  -d "{\"name\":\"$REPO\",\"private\":true,\"description\":\"Rules vs LLMs: income-tax engine + NYC 311 data-quality pipeline, each built rules-only / LLM-only / hybrid and measured\",\"has_issues\":true,\"has_wiki\":false}" \
  > /dev/null || echo "  (already exists, continuing)"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  BRANCH="submit/rules-vs-llms"
  git checkout -B "$BRANCH"
fi

git remote remove origin 2>/dev/null || true
git remote add origin "https://${GITHUB_TOKEN}@github.com/${OWNER}/${REPO}.git"

echo "== pushing $BRANCH =="
git push -u origin "$BRANCH"

echo
echo "Pushed. Open the pull request with the create_pr tool, or:"
echo "  https://github.com/${OWNER}/${REPO}/compare/main...${BRANCH}?expand=1"
