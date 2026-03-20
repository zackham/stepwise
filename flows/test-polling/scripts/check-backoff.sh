#!/bin/bash
# Poll check with simulated backoff: tracks attempt count in a state file.
# Succeeds (outputs JSON) only after max_fails attempts.
# Usage: check-backoff.sh <marker_path> <state_dir> <max_fails>

marker_path="$1"
state_dir="$2"
max_fails="${3:-4}"

mkdir -p "$state_dir"
attempts_file="$state_dir/attempts"

# Read and increment attempt counter
if [ -f "$attempts_file" ]; then
    attempts=$(cat "$attempts_file")
else
    attempts=0
fi
attempts=$((attempts + 1))
printf '%d' "$attempts" > "$attempts_file"

echo "backoff check: attempt $attempts / need > $max_fails" >&2

if [ "$attempts" -gt "$max_fails" ]; then
    # Success — write marker and output it
    printf '{"ready": true, "attempts": %d, "max_fails": %s}' "$attempts" "$max_fails" > "$marker_path"
    cat "$marker_path"
fi
# Otherwise: empty stdout = not ready
