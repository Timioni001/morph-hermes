#!/usr/bin/env bash
# Run MORPH's browser end-to-end suite against a clean set of demo applications.
#
#   scripts/e2e.sh [--keep]     # --keep leaves the services running afterwards
#
# Everything runs on loopback, in throwaway directories, with no external service.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d /tmp/morph-e2e.XXXXXX)"
export MORPH_DEMO_DATA_DIR="$WORK/apps"
export MORPH_DATA_DIR="$WORK/morph"
export MORPH_CONFIG_FILE="$PWD/config/connectors.sandbox.json"
mkdir -p "$MORPH_DEMO_DATA_DIR" "$MORPH_DATA_DIR"

cleanup() {
  [[ "${KEEP:-0}" == "1" ]] && { echo "services left running; data in $WORK"; return; }
  for pid in ${PIDS:-}; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$WORK"
}
[[ "${1:-}" == "--keep" ]] && KEEP=1
trap cleanup EXIT

"$PYTHON" -c "import sys; sys.path.insert(0, '.'); from morph.preflight import require; require()" || exit 1

echo "› building the dashboard bundle"
node scripts/build_dashboard.mjs
node scripts/fetch_test_vendor.mjs

for port in 8811 8812 8813 8820; do
  if (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null; then
    echo "port $port is already in use — stop whatever is on it first" >&2
    exit 1
  fi
done

echo "› starting the demo applications"
"$PYTHON" -m apps.serve >"$WORK/apps.log" 2>&1 &
PIDS="$!"
"$PYTHON" - <<'PY'
from apps.serve import wait_ready, SERVICES
import sys
sys.exit(0 if wait_ready(SERVICES) else 1)
PY

# Each service owns its bearer token: from its environment variable when the
# operator set one, otherwise generated and kept beside its database.
eval "$(set -a; "$PYTHON" -m apps.serve --print-tokens)"
export MORPH_STOREFRONT_TOKEN MORPH_HELPDESK_TOKEN MORPH_WAREHOUSE_TOKEN

echo "› starting the dashboard harness"
"$PYTHON" -m tests.e2e.harness.serve --port 8820 >"$WORK/harness.log" 2>&1 &
PIDS="$PIDS $!"
for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:8820/ >/dev/null && break
  sleep 0.5
done

echo "› running the browser suite"
node tests/e2e/run_e2e.cjs --base http://127.0.0.1:8820 "${@:2}"
