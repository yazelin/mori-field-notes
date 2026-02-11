#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root relative to this script
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATE="${1:-$(date +%Y-%m-%d)}"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/${DATE}.log"

mkdir -p "$LOG_DIR"

STEPS=(
  "scripts/collect-materials.py"
  "scripts/write-note.py"
  "scripts/generate-image.py"
  "scripts/publish.py"
)

for step in "${STEPS[@]}"; do
  echo "=== Running ${step} (--date ${DATE}) ===" | tee -a "$LOG_FILE"
  if ! python3 "${REPO_ROOT}/${step}" --date "$DATE" 2>&1 | tee -a "$LOG_FILE"; then
    echo "ERROR: ${step} failed" | tee -a "$LOG_FILE"
    exit 1
  fi
done

echo "=== Daily update completed for ${DATE} ===" | tee -a "$LOG_FILE"
