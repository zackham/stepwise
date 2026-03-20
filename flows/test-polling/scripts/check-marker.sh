#!/bin/bash
# Poll check: outputs marker file contents if it exists, empty stdout otherwise.
# Usage: check-marker.sh <marker_path>

marker_path="$1"

if [ -f "$marker_path" ]; then
    cat "$marker_path"
fi
