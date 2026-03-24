# How to Build a Stepwise Extension

Stepwise extensions are external commands that add CLI subcommands and integrate with the stepwise engine via its public APIs. They follow the git-style discovery model: if `stepwise-telegram` is on your PATH, then `stepwise telegram` works automatically.

Extensions are always out-of-process — they never load code into the engine. They interact via HTTP, WebSocket, stdin/stdout, and the REST API. This keeps the core small and lets you write extensions in any language.

## How Discovery Works

When you run `stepwise extensions list`, stepwise scans every directory on your `$PATH` for executables whose name starts with `stepwise-`. The suffix becomes the subcommand name: `stepwise-deploy` becomes `stepwise deploy`.

Deduplication follows shell rules — if the same name appears in multiple PATH directories, the first one wins. Results are cached in `.stepwise/extensions.json` for one hour. Use `--refresh` to force a fresh scan.

## The Extension Manifest

After finding an executable, stepwise runs `<executable> --manifest` and reads the JSON response. This is how your extension reports its name, version, capabilities, and configuration requirements.

```json
{
  "name": "telegram",
  "version": "0.1.0",
  "description": "Route stepwise events to Telegram",
  "capabilities": ["event_consumer", "fulfillment"],
  "config_keys": ["telegram_bot_token", "telegram_chat_id"]
}
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `name` | yes | Extension identifier (shown in `stepwise extensions list`) |
| `version` | no | Semver string |
| `description` | no | One-line summary |
| `capabilities` | no | List of capability tags (e.g., `event_consumer`, `fulfillment`, `executor`) |
| `config_keys` | no | Config keys the extension reads from stepwise config |

If `--manifest` returns a non-zero exit code or invalid JSON, the extension still appears in the listing — just with name and path only.

## Example: Building a Deploy Extension

Here's a minimal extension in Bash that deploys on job completion. Save it as `stepwise-deploy` somewhere on your PATH and make it executable.

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

## Extension Interaction Patterns

Extensions don't get special privileges. They use the same interfaces available to any external tool:

**Consume events** — Connect to `ws://localhost:PORT/api/v1/events/stream` for real-time events. Filter by `?session_id=X` or `?job_id=X`. Use `?since_job_start=true` for replay.

**Fulfill suspended steps** — When a step uses `executor: external` and suspends for input, your extension can fulfill it via the REST API:

```bash
curl -X POST http://localhost:8340/api/runs/<run-id>/fulfill \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "feedback": "Looks good"}'
```

**Create and manage jobs** — Use the REST API at `/api/jobs/` to create, cancel, or query jobs programmatically.

## Writing Extensions in Python

For more complex extensions, Python with `click` or `argparse` works well. The key requirements are the same: the binary must be named `stepwise-<name>`, be executable, and respond to `--manifest`.

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

## Reference

- `stepwise extensions list` — show all discovered extensions
- `stepwise extensions list --refresh` — bypass cache and rescan PATH
- Extensions cache: `.stepwise/extensions.json` (1-hour TTL)
- Event envelope format: see [extensions.md](extensions.md) for the full event schema
- REST API: see [api.md](api.md) for endpoint documentation
