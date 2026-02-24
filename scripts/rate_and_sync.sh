#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${1:-/Users/okonfu/.openclaw/workspace}"
YAML_PATH="${2:-$WORKSPACE/internships.yaml}"
SYNC_MODE="${3:-new}"
FILTER_COMPANY="${4:-}"

python3 "$WORKSPACE/skills/jd-rater/scripts/rate_jds.py" --missing-only

if [[ -n "$FILTER_COMPANY" ]]; then
  NOTION_API_KEY="${NOTION_API_KEY:-}" python3 "$WORKSPACE/skills/internship-scout/scripts/notion_sync.py" \
    --yaml "$YAML_PATH" --mode "$SYNC_MODE" --filter "$FILTER_COMPANY"
else
  NOTION_API_KEY="${NOTION_API_KEY:-}" python3 "$WORKSPACE/skills/internship-scout/scripts/notion_sync.py" \
    --yaml "$YAML_PATH" --mode "$SYNC_MODE"
fi
