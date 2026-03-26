# Flow Sharing

Stepwise flows can be single `.flow.yaml` files or directory flows (a directory containing `FLOW.yaml` with co-located scripts and prompts). Both formats can be shared. Directory flows are published as bundles — the YAML plus all co-located files. No accounts, no package managers.

## How It Works

```
author                          stepwise.run                       consumer
──────                          ────────────                       ────────
stepwise share my.flow.yaml
  → validates flow
  → reads metadata
  → uploads YAML            →  stores flow
  → prints URL                  indexes for search
                                renders DAG preview
                                tracks downloads

                                                          stepwise get code-review
                                                            → resolves name
                                                          ← downloads YAML
                                                            → saves to cwd

                                                          stepwise search "agent review"
                                                            → queries registry
                                                          ← prints matches
```

## Authentication

Publishing flows requires registry authentication via GitHub OAuth.

```bash
stepwise login      # opens browser for GitHub OAuth, stores token locally
stepwise logout     # removes stored authentication token
```

`stepwise login` is a prerequisite for `stepwise share`. Reading and downloading flows (`get`, `search`, `info`) do not require authentication.

See [CLI Reference: login/logout](cli.md#stepwise-login) for details.

---

## CLI Commands

### `stepwise share <file>`

Publish a flow to the registry.

```bash
stepwise share my-pipeline.flow.yaml
```

```
Validating my-pipeline.flow.yaml... ✓ (3 steps, 1 loop)
Publishing as "my-pipeline" by zack...

✓ Published: https://stepwise.run/flows/my-pipeline
  Run: stepwise get my-pipeline
```

What happens:
1. Validates the flow (must pass `stepwise validate`)
2. Reads metadata from YAML header (`name`, `description`, `author`, `tags`)
3. Auto-populates `author` from `git config user.name` if not set in YAML
4. Uploads to the registry API
5. Returns the public URL

Flags:
- `--name <name>` — override the flow name (default: from YAML `name` field or filename)
- `--unlisted` — publish but don't index in search results (accessible by direct URL/name)

### `stepwise get <name-or-url>`

Download a flow.

```bash
# By name (from registry)
stepwise get code-review
# → saves code-review.flow.yaml to cwd

# By URL (direct download)
stepwise get https://stepwise.run/flows/code-review/raw
stepwise get https://example.com/my-flow.flow.yaml
```

```
✓ Downloaded code-review.flow.yaml (3 steps, by zack, 1.2k downloads)
  Run: stepwise run code-review.flow.yaml
```

What happens:
1. If the argument starts with `http`, downloads directly
2. Otherwise, resolves the name via `GET /api/flows/{name}`
3. Saves the YAML file to the current directory
4. Prints step count, author, download count

Flags:
- `--output <path>` — save to a specific path instead of cwd
- `--force` — overwrite if file already exists

### `stepwise search <query>`

Search the registry.

```bash
stepwise search "code review agent"
```

```
NAME                 AUTHOR     STEPS  DOWNLOADS  TAGS
code-review          zack       3      1,247      agent, human-in-the-loop
pr-review-lite       sarah      2      892        script, code-review
security-audit       mike       5      634        agent, security
```

Flags:
- `--tag <tag>` — filter by tag
- `--sort <field>` — sort by `downloads` (default), `name`, `newest`
- `--limit <n>` — max results (default: 20)
- `--output json` — machine-readable output

### `stepwise info <name>`

Show details about a published flow without downloading it.

```bash
stepwise info code-review
```

```
Name:        code-review
Author:      zack
Version:     1.2
Description: AI-powered code review with human approval gate
Tags:        agent, human-in-the-loop, code-review
Downloads:   1,247
Published:   2026-03-15
URL:         https://stepwise.run/flows/code-review

Steps:
  analyze     agent    → review the code changes
  approve     external → decide: approve, request changes, or escalate
  merge       script   → merge the PR

Loops:
  approve → analyze (on request_changes, max 3 attempts)
```

---

## Registry API

Base URL: `https://stepwise.run/api`

All endpoints return JSON. No authentication required for reads. Writes use a short-lived token generated at publish time (no accounts — token is derived from the flow name + author + a server secret).

### Publish

```
POST /api/flows
Content-Type: application/json

{
  "yaml": "name: code-review\ndescription: ...\nsteps:\n  ...",
  "author": "zack",
  "source": "cli-0.1.0"
}
```

Response:
```json
{
  "name": "code-review",
  "slug": "code-review",
  "author": "zack",
  "version": "1.0",
  "description": "AI-powered code review with human approval gate",
  "tags": ["agent", "human-in-the-loop"],
  "steps": 3,
  "loops": 1,
  "url": "https://stepwise.run/flows/code-review",
  "raw_url": "https://stepwise.run/flows/code-review/raw",
  "created_at": "2026-03-15T10:30:00Z",
  "update_token": "stw_tok_abc123..."
}
```

The `update_token` is returned only on initial publish. The author must save it (the CLI stores it in `~/.config/stepwise/tokens.json`) to update or unpublish later.

### Update

```
PUT /api/flows/{name}
Authorization: Bearer stw_tok_abc123...
Content-Type: application/json

{
  "yaml": "name: code-review\n...",
  "changelog": "Added security check step"
}
```

### Get (metadata)

```
GET /api/flows/{name}
```

Response:
```json
{
  "name": "code-review",
  "author": "zack",
  "version": "1.2",
  "description": "AI-powered code review with human approval gate",
  "tags": ["agent", "human-in-the-loop"],
  "steps": 3,
  "loops": 1,
  "downloads": 1247,
  "created_at": "2026-03-15T10:30:00Z",
  "updated_at": "2026-03-20T14:00:00Z",
  "url": "https://stepwise.run/flows/code-review",
  "raw_url": "https://stepwise.run/flows/code-review/raw"
}
```

### Download (raw YAML)

```
GET /api/flows/{name}/raw
Accept: text/yaml
```

Returns the raw `.flow.yaml` content. Increments the download counter.

### Search

```
GET /api/flows?q=code+review&tag=agent&sort=downloads&limit=20
```

Response:
```json
{
  "flows": [
    {
      "name": "code-review",
      "author": "zack",
      "description": "AI-powered code review with human approval gate",
      "tags": ["agent", "human-in-the-loop"],
      "steps": 3,
      "downloads": 1247
    }
  ],
  "total": 1,
  "query": "code review",
  "filters": {"tag": "agent"}
}
```

### List (browse)

```
GET /api/flows?sort=downloads&limit=50
GET /api/flows?sort=newest&limit=50
GET /api/flows?tag=agent&sort=downloads
```

### Delete

```
DELETE /api/flows/{name}
Authorization: Bearer stw_tok_abc123...
```

---

## Website (stepwise.run)

The website lives in a separate repo (`~/work/stepwise.run/`). It consumes this doc as the API spec.

### URL Structure

```
stepwise.run/
  /                          → homepage (project overview + featured flows)
  /docs                      → documentation
  /flows                     → flow gallery (browse, search, inspect)
  /flows/{name}              → flow detail page (DAG preview, metadata, download)
  /flows/{name}/raw          → raw YAML download
  /api/...                   → registry API (above)
```

### Flow Detail Pages

Each published flow gets a page at `stepwise.run/flows/{name}` showing:

1. **Metadata** — name, author, description, tags, download count, publish date
2. **DAG preview** — static render of the step graph (reuses dagre layout from the Stepwise web UI)
3. **Step list** — executor types, outputs, exit rules summarized
4. **YAML source** — syntax-highlighted, copyable
5. **Download button** → `stepwise get {name}`
6. **Run command** — copy-pasteable `stepwise get {name} && stepwise run {name}.flow.yaml`

---

## Directory Flow Bundles

When sharing a directory flow, `stepwise share` bundles the `FLOW.yaml` with all co-located files (scripts, prompts, data). On `stepwise get`, the bundle is unpacked into a directory.

### Bundle Limits

| Limit | Value |
|-------|-------|
| Total size | 500KB |
| Max files | 20 |
| File types | Text only — `.py`, `.sh`, `.bash`, `.md`, `.txt`, `.yaml`, `.yml`, `.json`, `.prompt` |

### Blocked Files

These files cause a publish error if found in the flow directory: `.env`, `.pem`, `id_rsa`, `credentials.json`, `.DS_Store`. Directories like `.git`, `__pycache__`, `node_modules`, `.venv` are skipped automatically.

Binary files and files with non-allowed extensions are silently excluded.

### Provenance Tracking

When downloading a directory flow, Stepwise writes `.origin.json` inside the flow directory:

```json
{
  "name": "code-review",
  "author": "alice",
  "registry": "https://stepwise.run",
  "downloaded_at": "2026-03-15T10:30:00Z"
}
```

This tracks where the flow came from. It is excluded from bundles when re-sharing.

---

## Flow YAML Requirements for Sharing

Shared flows must be **self-contained**. This means:

1. **No external dependencies** — for single-file flows, avoid `run: scripts/deploy.sh` (the consumer won't have it). For directory flows, all referenced scripts must be co-located in the flow directory.
2. **Job inputs for configuration** — use `$job.field` for values the consumer provides
3. **Meaningful metadata** — `name`, `description`, and `tags` are required for publishing

```yaml
# Good: single-file, self-contained, configurable via job inputs
name: code-review
description: AI-powered PR review with human approval gate
author: zack
tags: [agent, human-in-the-loop, code-review]

steps:
  analyze:
    executor: agent
    prompt: "Review the PR at $pr_url. Check correctness, security, performance."
    outputs: [result]
    inputs:
      pr_url: $job.pr_url
```

```
# Good: directory flow with co-located scripts (bundled on share)
code-review/
  FLOW.yaml
  analyze.py               # run: analyze.py works — bundled with the flow
  prompts/review-system.md # prompt_file: prompts/review-system.md works
```

```yaml
# Bad: single-file flow depending on external scripts
steps:
  analyze:
    run: scripts/analyze.sh    # consumer won't have this file
    outputs: [result]
```

The `stepwise share` command validates this constraint before publishing. It warns on:
- Single-file flows with `run:` commands referencing relative paths
- Missing `name`, `description`, or `tags` in metadata
- Missing `author` (auto-populated from git config, but warns if empty)

---

## Token Management

Publish tokens are stored locally in `~/.config/stepwise/tokens.json`:

```json
{
  "code-review": "stw_tok_abc123...",
  "data-pipeline": "stw_tok_def456..."
}
```

- Tokens are generated server-side on first publish
- The CLI saves them automatically
- To update or delete a flow, the CLI sends the stored token
- Lost tokens: contact support or re-publish under a different name
- No user accounts — the token IS the credential for that specific flow

---

## Naming Rules

Flow names are URL-safe slugs:
- Lowercase alphanumeric + hyphens
- 3-60 characters
- Must start with a letter
- No consecutive hyphens
- First publisher owns the name (no squatting policy — names can be reclaimed if unused)

The CLI slugifies the YAML `name` field automatically:
- `"My Cool Flow"` → `my-cool-flow`
- `"PR Review v2"` → `pr-review-v2`

---

## Versioning

Flows have a single mutable version. When you update a published flow:
- The version field increments (or uses the version from the YAML if specified)
- The previous YAML is replaced
- Download count persists
- Consumers who previously downloaded get the old version (no auto-update)

Future: `stepwise update` could check if a newer version exists and prompt to re-download. Not needed for v1.
