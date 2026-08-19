#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PWD}/src${PYTHONPATH:+:${PYTHONPATH}}"
export SPARK_LOCAL_IP="${SPARK_LOCAL_IP:-127.0.0.1}"
export PYSPARK_PYTHON="${PYSPARK_PYTHON:-python3}"

python3 -m unittest discover \
  -s tests_runtime \
  -p 'test_spark_*_runtime.py' \
  -v
