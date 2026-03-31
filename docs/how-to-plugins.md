# How to Build a Stepwise Extension

Step-by-step guide to creating a CLI extension that integrates with the Stepwise engine via its public APIs.

---

Stepwise extensions are external commands that add CLI subcommands. They follow git-style discovery: if `stepwise-telegram` is on your PATH, then `stepwise telegram` works automatically. Extensions are always out-of-process — they interact via HTTP, WebSocket, and stdin/stdout, never by loading code into the engine.

## How Discovery Works

When you run `stepwise extensions list`, Stepwise scans every directory on your `$PATH` for executables named `stepwise-*`. The suffix becomes the subcommand: `stepwise-deploy` becomes `stepwise deploy`.

Deduplication follows shell rules — if the same name appears in multiple PATH directories, the first one wins. Results are cached in `.stepwise/extensions.json` for one hour. Use `--refresh` to force a fresh scan.

## The Extension Manifest

After finding an executable, Stepwise runs `<executable> --manifest` and reads the JSON response. This is how your extension reports its capabilities.

```json
{
  "name": "telegram",
  "version": "0.1.0",
  "description": "Route stepwise events to Telegram",
  "capabilities": ["event_consumer", "fulfillment"],
  "config_keys": ["telegram_bot_token", "telegram_chat_id"]
}
```

| Field | Required | Description |
|---|---|---|
| `name` | yes | Extension identifier (shown in `stepwise extensions list`) |
| `version` | no | Semver string |
| `description` | no | One-line summary |
| `capabilities` | no | Capability tags (e.g., `event_consumer`, `fulfillment`, `executor`) |
| `config_keys` | no | Config keys the extension reads from stepwise config |

If `--manifest` returns a non-zero exit code or invalid JSON, the extension still appears in the listing — just with name and path only.

## Example: Deploy Extension in Bash

A minimal extension that deploys on job completion. Save as `stepwise-deploy` on your PATH and make it executable.

```bash
#!/usr/bin/env bash
set -euo pipefail

# Handle manifest request
if [[ "${1:-}" == "--manifest" ]]; then
    cat <<'EOF'
{
    "name": "deploy",
    "version": "0.1.0",
    "description": "Deploy on job completion",
    "capabilities": ["event_consumer"],
    "config_keys": ["deploy_target"]
}
EOF
    exit 0
fi

# Handle subcommands
case "${1:-}" in
    watch)
        # Connect to WebSocket and deploy on job.completed events
        wscat -c "ws://localhost:8340/api/v1/events/stream" | while read -r event; do
            event_type=$(echo "$event" | jq -r '.event // empty')
            if [[ "$event_type" == "job.completed" ]]; then
                job_id=$(echo "$event" | jq -r '.job_id')
                echo "Deploying for job $job_id..."
                # your deploy logic here
            fi
        done
        ;;
    *)
        echo "Usage: stepwise deploy watch"
        exit 1
        ;;
esac
```

After placing this on your PATH:

```bash
$ stepwise extensions list
NAME      VERSION   DESCRIPTION               PATH
deploy    0.1.0     Deploy on job completion   /usr/local/bin/stepwise-deploy

$ stepwise deploy watch
# Listening for job completions...
```

## Example: Python Extension

For more complex extensions, Python with `argparse` or `click` works well. Same requirements: named `stepwise-<name>`, executable, responds to `--manifest`.

```python
#!/usr/bin/env python3
import json
import sys

if "--manifest" in sys.argv:
    json.dump({
        "name": "my-extension",
        "version": "1.0.0",
        "description": "My custom extension",
        "capabilities": ["event_consumer"],
    }, sys.stdout)
    sys.exit(0)

# Extension logic here...
```

## Extension Interaction Patterns

Extensions use the same interfaces available to any external tool:

**Consume events** — Connect to `ws://localhost:PORT/api/v1/events/stream` for real-time events. Filter by `?session_id=X` or `?job_id=X`. Use `?since_job_start=true` for replay. See [Extensions](extensions.md) for the full event envelope format.

**Fulfill suspended steps** — When a step with `executor: external` suspends for input, your extension can fulfill it:

```bash
curl -X POST http://localhost:8340/api/runs/<run-id>/fulfill \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "feedback": "Looks good"}'
```

**Create and manage jobs** — Use the REST API at `/api/jobs/` to create, cancel, or query jobs programmatically.

## Reference

- `stepwise extensions list` — show all discovered extensions
- `stepwise extensions list --refresh` — bypass cache and rescan PATH
- Extensions cache: `.stepwise/extensions.json` (1-hour TTL)
- Event envelope format: [Extensions](extensions.md)
- REST API endpoints: [API Reference](api.md)
