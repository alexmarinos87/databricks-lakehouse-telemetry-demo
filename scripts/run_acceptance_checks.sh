#!/usr/bin/env bash
set -euo pipefail

BASE_REF="${BASE_REF:-origin/main}"

git rev-parse --verify "${BASE_REF}" >/dev/null
MERGE_BASE="$(git merge-base "${BASE_REF}" HEAD)"

scripts/check_repo_contracts.py --base "${BASE_REF}"

ACCEPTANCE_DIR="$(mktemp -d -t lakehouse-acceptance.XXXXXX)"
cleanup() {
  rm -rf -- "${ACCEPTANCE_DIR}"
}
trap cleanup EXIT

git checkout-index --all --prefix="${ACCEPTANCE_DIR}/"
(
  cd "${ACCEPTANCE_DIR}"
  scripts/run_local_checks.sh
)

git diff --check "${MERGE_BASE}" HEAD
git diff --check
git diff --cached --check
