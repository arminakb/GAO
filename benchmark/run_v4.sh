#!/usr/bin/env bash
# Phase 8 benchmark driver: GOA vs ECC vs Solo on 3 domains x 2 seeds.
# Sequential (OpenCode session-DB lock), fresh sandbox per run (v4_runner).
# Usage: benchmark/run_v4.sh [backend|frontend|database]
set -u
cd "$(dirname "$0")/.."
DOMAIN="${1:-}"
PY=".venv/bin/python"

ARGS=(--tasks benchmark/tasks_v4.json
      --arm solo=benchmark.v4_arms:executor_solo
      --arm ecc=benchmark.v4_arms:executor_ecc
      --arm goa=benchmark.v4_arms:executor_goa
      --sandbox-root benchmark/sandboxes/v4)
if [ -n "$DOMAIN" ]; then
  ARGS+=(--domain "$DOMAIN")
fi
exec $PY -m benchmark.v4_runner "${ARGS[@]}"
