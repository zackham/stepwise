#!/bin/bash
# Creates a marker file after a delay (background process).
# Env vars: $delay_seconds, $marker_id (unique name for this marker)
# Outputs JSON: {"marker_path": "<abs path>", "flow_dir": "<abs path>"}

set -euo pipefail

delay=${delay_seconds:-5}
id=${marker_id:-default}

# Create markers dir in workspace (cwd = workspace)
markers_dir="$(pwd)/markers"
mkdir -p "$markers_dir"

marker_path="$markers_dir/${id}.json"

# Launch background process to create marker after delay
(
  sleep "$delay"
  printf '{"ready": true, "marker_id": "%s", "delay_seconds": %s}' "$id" "$delay" > "$marker_path"
) &

# Output marker path and flow dir for downstream steps
printf '{"marker_path": "%s", "flow_dir": "%s"}' "$marker_path" "$STEPWISE_FLOW_DIR"
