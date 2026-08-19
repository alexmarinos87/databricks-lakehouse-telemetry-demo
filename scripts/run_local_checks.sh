#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
fi

CONTRACT_ARGS=()
if [[ -n "${CONTRACT_BASE_REF:-}" ]]; then
  CONTRACT_ARGS+=(--base "${CONTRACT_BASE_REF}")
fi

"${PYTHON_BIN}" -m py_compile notebooks/*.py src/lakehouse_demo/*.py scripts/*.py
"${PYTHON_BIN}" scripts/check_repo_contracts.py "${CONTRACT_ARGS[@]}"
"${PYTHON_BIN}" -m unittest discover -s tests -v
