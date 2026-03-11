# Flow Sharing

Stepwise flows are self-contained `.flow.yaml` files. Sharing is built around this: publish a file, download a file, browse what's available. No accounts, no tokens, no package managers.

## How It Works

```
author                          stepwise.run                       consumer
──────                          ────────────                       ────────
stepwise flow share my.flow.yaml
  → validates flow
  → reads metadata
  → uploads YAML            →  stores flow
  → prints URL                  indexes for search
                                renders DAG preview
                                tracks downloads

                                                          stepwise flow get code-review
                                                            → resolves name
                                                          ← downloads YAML
                                                            → saves to cwd

                                                          stepwise flow search "agent review"
                                                            → queries registry
                                                          ← prints matches
```

## CLI Commands

### `stepwise flow share <file>`

Publish a flow to the registry.

```bash
stepwise flow share my-pipeline.flow.yaml
```

```
Validating my-pipeline.flow.yaml... ✓ (3 steps, 1 loop)
Publishing as "my-pipeline" by zack...

✓ Published: https://stepwise.run/flows/my-pipeline
  Run: stepwise flow get my-pipeline
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

### `stepwise flow get <name-or-url>`

Download a flow.

```bash
# By name (from registry)
stepwise flow get code-review
# → saves code-review.flow.yaml to cwd

# By URL (direct download)
stepwise flow get https://stepwise.run/flows/code-review/raw
stepwise flow get https://example.com/my-flow.flow.yaml
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

### `stepwise flow search <query>`

Search the registry.

```bash
stepwise flow search "code review agent"
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

### `stepwise flow info <name>`

Show details about a published flow without downloading it.

```bash
stepwise flow info code-review
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
  approve     human    → decide: approve, request changes, or escalate
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

The website is generated from this repository. It serves two purposes: project homepage and flow gallery.

### Structure

```
stepwise.run/
  /                          → homepage (project overview + featured flows)
  /docs                      → documentation (from docs/)
  /flows                     → flow gallery (browse, search, inspect)
  /flows/{name}              → flow detail page (DAG preview, metadata, download)
  /flows/{name}/raw          → raw YAML download
  /api/...                   → registry API (above)
```

### Homepage Generation

The homepage is built from content in this repo:

```
web/site/
  index.html                 → landing page template
  docs/                      → rendered from docs/*.md
  public/                    → static assets (logo, og-image, etc.)
```

The build step renders markdown docs to HTML and assembles the landing page. The flow gallery and detail pages are dynamic (served by the registry API + a thin frontend).

### Flow Detail Pages

Each published flow gets a page at `stepwise.run/flows/{name}` showing:

1. **Metadata** — name, author, description, tags, download count, publish date
2. **DAG preview** — static SVG render of the step graph (same layout as the web UI)
3. **Step list** — executor types, outputs, exit rules summarized
4. **YAML source** — syntax-highlighted, copyable
5. **Download button** → `stepwise flow get {name}`
6. **Run command** — copy-pasteable `stepwise flow get {name} && stepwise run {name}.flow.yaml`

### DAG Preview Rendering

Flow detail pages render a static DAG preview. This reuses the existing dagre layout code from the web UI:

```
web/src/lib/dag-renderer.ts   → shared layout logic (dagre)
web/site/flow-preview.ts      → static SVG generation for the website
```

The preview is generated server-side when a flow is published and cached as SVG. It updates when the flow is updated.

---

## Flow YAML Requirements for Sharing

Shared flows must be **self-contained**. This means:

1. **No external script references** — use inline commands, not `run: scripts/deploy.sh`
2. **No local file dependencies** — everything is in the YAML
3. **Job inputs for configuration** — use `$job.field` for values the consumer provides
4. **Meaningful metadata** — `name`, `description`, and `tags` are required for publishing

```yaml
# Good: self-contained, configurable via job inputs
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

```yaml
# Bad: depends on external scripts
steps:
  analyze:
    run: scripts/analyze.sh    # consumer won't have this file
    outputs: [result]
```

The `stepwise flow share` command validates this constraint before publishing. It warns on:
- `run:` commands referencing relative paths (files that won't exist on the consumer's machine)
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

Future: `stepwise flow update` could check if a newer version exists and prompt to re-download. Not needed for v1.
