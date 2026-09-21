#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PWD}/src${PYTHONPATH:+:${PYTHONPATH}}"
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"
export PYSPARK_PYTHON="${PYSPARK_PYTHON:-python3}"

python3 scripts/run_spark_runtime_checks.py
