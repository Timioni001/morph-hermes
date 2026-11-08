#!/usr/bin/env bash
# Produce the MORPH demonstration video from a real browser session.
#
#   scripts/make_video.sh            # → .media/morph-demo.mp4
#
# The recording is an authentic run: the actions on screen really happen, the
# approval really writes to the Storefront, and the Storefront's own page is
# reloaded to show it. Only the caption text is added afterwards, from timings the
# recorder wrote while it ran.
#
# Needs: node with playwright, ffmpeg. Both are checked below.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 1; }
node -e "require('playwright')" 2>/dev/null || {
  echo "playwright is required (npm install playwright && npx playwright install chromium)" >&2
  exit 1; }

PYTHON="${PYTHON:-python3}"
OUT="${OUT:-.media}"
WORK="$(mktemp -d /tmp/morph-video.XXXXXX)"
export MORPH_DEMO_DATA_DIR="$WORK/apps"
export MORPH_DATA_DIR="$WORK/morph"
mkdir -p "$MORPH_DEMO_DATA_DIR" "$MORPH_DATA_DIR" "$OUT"

cleanup() {
  for pid in ${PIDS:-}; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

for port in 8811 8812 8813 8820; do
  if (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null; then
    echo "port $port is already in use — stop whatever is on it first" >&2
    exit 1
  fi
done

echo "› building the dashboard bundle"
node scripts/build_dashboard.mjs
node scripts/fetch_test_vendor.mjs

echo "› starting the applications and the dashboard"
"$PYTHON" -m apps.serve >"$WORK/apps.log" 2>&1 &
PIDS="$!"
"$PYTHON" -c "import sys; from apps.serve import wait_ready, SERVICES; sys.exit(0 if wait_ready(SERVICES) else 1)"
eval "$(set -a; "$PYTHON" -m apps.serve --print-tokens)"
export MORPH_STOREFRONT_TOKEN MORPH_HELPDESK_TOKEN MORPH_WAREHOUSE_TOKEN
"$PYTHON" -m tests.e2e.harness.serve --port 8820 >"$WORK/harness.log" 2>&1 &
PIDS="$PIDS $!"
for _ in $(seq 1 40); do curl -sf http://127.0.0.1:8820/ >/dev/null && break; sleep 0.5; done

echo "› recording"
node scripts/record_demo.cjs --out "$OUT"

echo "› composing"
"$PYTHON" scripts/compose_video.py --input "$OUT/raw.webm" --captions "$OUT/captions.json" \
  --output "$OUT/morph-demo.mp4"
rm -f "$OUT/raw.webm"
echo "› done: $OUT/morph-demo.mp4"
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "$OUT/morph-demo.mp4"
