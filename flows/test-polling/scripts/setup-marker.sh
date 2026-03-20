#!/bin/sh
# setup-marker.sh — Create a marker file after a configurable delay.
#
# Args:
#   $1 — marker_id (unique name for this marker file)
#   $2 — delay_seconds (optional, falls back to env var, then default 5)
#
# Env vars (from ScriptExecutor inputs):
#   delay_seconds — seconds to wait before creating the marker (if not passed as $2)
#
# Outputs (JSON on stdout):
#   marker_path — absolute path to the marker file

set -e

marker_id="${1:?marker_id required}"
delay_seconds="${2:-${delay_seconds:-5}}"

marker_dir="$(pwd)/markers"
mkdir -p "$marker_dir"

marker_path="$marker_dir/${marker_id}.json"

trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

# Launch background process to create marker after delay
(sleep "$delay_seconds" && printf '{"ready": true, "created_at": "%s"}' "$(date -Iseconds)" > "$marker_path") &
disown

# Output marker path for downstream poll steps
printf '{"marker_path": "%s"}' "$marker_path"
