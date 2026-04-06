# Flow and Kit Sharing

Publish, discover, and install flows and kits from the Stepwise registry at stepwise.run.

---

Stepwise flows can be single `.flow.yaml` files or directory flows (a directory containing `FLOW.yaml` with co-located scripts and prompts). **Kits** are directories containing a `KIT.yaml` manifest with multiple bundled flows — a way to share a collection of related flows as a single package. All formats can be shared.

## How It Works

```
author                          stepwise.run                       consumer
──────                          ────────────                       ────────
stepwise share my-flow
  → validates flow (or kit)
  → reads metadata
  → uploads YAML + files    →  stores flow or kit
  → prints URL                  indexes for search
                                renders DAG preview
                                tracks downloads

                                                          stepwise get code-review
                                                            → resolves name (flow or kit)
                                                          ← downloads YAML + bundled flows
                                                            → saves to .stepwise/registry/

                                                          stepwise search "agent review"
                                                            → queries registry
                                                          ← prints flows and kits
```

---

## Authentication

Publishing flows requires registry authentication via GitHub OAuth.

```bash
stepwise login      # opens browser for GitHub OAuth, stores token locally
stepwise logout     # removes stored authentication token
```

`stepwise login` is a prerequisite for `stepwise share`. Reading and downloading flows (`get`, `search`, `info`) do not require authentication.

See [CLI Reference](cli.md) for details.

---

## CLI Commands

### `stepwise share <flow-or-kit>`

Publish a flow or kit to the registry. The command auto-detects kits by looking for `KIT.yaml` in the target directory.

```bash
# Share a single flow
stepwise share my-pipeline.flow.yaml

# Share a kit (directory with KIT.yaml)
stepwise share swdev
```

```
Validated kit 'swdev' (5 flows)
  plan
  plan-light
  implement
  implement-light
  research
Publish kit 'swdev' (5 flows)? [Y/n]

Published kit 'swdev' (5 flows)
  Get: stepwise get swdev
```

What happens:
1. Validates the flow or kit (KIT.yaml + all bundled flows)
2. Reads metadata from YAML header (`name`, `description`, `author`)
3. For kits: collects all flow subdirectories with their co-located files
4. Uploads to the registry API
5. Returns the public URL

Flags:
- `--author <name>` — override the author name (default: from git config)
- `--update` — update an existing published flow or kit

### `stepwise get <name-or-url>`

Download a flow or kit from the registry. Saved into `.stepwise/registry/@author/slug/`.

The command tries to resolve the name as a flow first. If not found, it falls back to kit lookup.

```bash
# By name (flow or kit — auto-detected)
stepwise get code-review
stepwise get swdev

# By author:name reference
stepwise get @zack:code-review
stepwise get @zack:swdev

# By URL (direct download, flows only)
stepwise get https://stepwise.run/flows/code-review/raw
```

Downloaded flows and kits can be run directly by name:

```bash
# Run a single flow
stepwise run @zack:code-review --input pr_url="..."

# Run a flow from an installed kit
stepwise run @zack:swdev/plan --input spec="new feature"
```

When installing a kit, Stepwise also auto-fetches any registry includes listed in the kit's `KIT.yaml`.

Flags:
- `--output <path>` — save to a specific path instead of the registry cache
- `--force` — overwrite if the flow or kit already exists locally

### `stepwise search <query>`

Search the registry. Results include both flows and kits, distinguished by a TYPE column.

```bash
stepwise search "code review agent"
```

```
TYPE   NAME                 AUTHOR     STEPS  DOWNLOADS
flow   code-review          zack       3      1,247
flow   pr-review-lite       sarah      2      892
kit    swdev                zack       5      634
```

Flags:
- `--tag <tag>` — filter by tag
- `--sort <field>` — sort by `downloads` (default), `name`, `newest`
- `--output json` — machine-readable output

### `stepwise info <name>`

Show details about a flow without downloading it.

```bash
stepwise info code-review
```

```
Name:        code-review
Author:      zack
Version:     1.2
Description: AI-powered code review with human approval gate
Tags:        agent, external-fulfillment, code-review
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

## Forking Registry Flows

Registry flows can be forked to your local `flows/` directory via the web UI. This copies the flow into your project so you can customize it.

The fork API is available at `POST /api/local-flows/fork` with a `source_path` (the registry flow path) and `name` (the local flow name). The web UI provides a "Fork" button on registry flow detail pages.

Forked flows are fully independent — changes don't propagate back to the registry version.

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

These files cause a publish error: `.env`, `.pem`, `id_rsa`, `credentials.json`, `.DS_Store`. Directories like `.git`, `__pycache__`, `node_modules`, `.venv` are skipped automatically.

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

## Kit Publishing

Kits bundle multiple related flows into a single shareable package. A kit is a directory containing `KIT.yaml` and one or more flow subdirectories.

### Kit directory structure

```
swdev/
  KIT.yaml                 # kit manifest (required)
  plan/
    FLOW.yaml              # bundled flow
  implement/
    FLOW.yaml
    scripts/build.sh       # co-located files included
  research/
    FLOW.yaml
```

### KIT.yaml format

```yaml
name: swdev
description: Software development kit — plan, implement, and research flows
author: zack
category: development
tags: [agent, code, planning]
include:                       # optional — registry flows to auto-fetch on install
  - @alice:code-review
defaults:                      # optional — default input values for bundled flows
  project_path: .
```

Required fields: `name`, `description`. All other fields are optional.

### Namespacing

Locally, kit flows are referenced as `kit/flow` (e.g., `swdev/plan`). On the registry, installed kits use `@author:kit/flow` (e.g., `@zack:swdev/plan`). Kits and flows share the same slug namespace — a kit named `swdev` and a flow named `swdev` cannot coexist.

---

## Flow YAML Requirements for Sharing

Shared flows must be **self-contained**:

1. **No external dependencies** — for single-file flows, avoid `run: scripts/deploy.sh` (the consumer won't have it). For directory flows, all referenced scripts must be co-located in the flow directory.
2. **Job inputs for configuration** — use `$job.field` for values the consumer provides.
3. **Meaningful metadata** — `name` and `description` are required for publishing.

```yaml
# Good: single-file, self-contained, configurable via job inputs
name: code-review
description: AI-powered PR review with human approval gate
author: zack

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
- Missing `name` or `description` in metadata
- Missing `author` (auto-populated from git config, but warns if empty)

---

## Naming Rules

Flow names are URL-safe slugs:
- Lowercase alphanumeric + hyphens
- 3-60 characters
- Must start with a letter
- No consecutive hyphens
- First publisher owns the name (no squatting policy — names can be reclaimed if unused)

The CLI slugifies the YAML `name` field automatically:
- `"My Cool Flow"` becomes `my-cool-flow`
- `"PR Review v2"` becomes `pr-review-v2`

---

## Versioning

Flows have a single mutable version. When you update a published flow:
- The version field increments (or uses the version from the YAML if specified)
- The previous YAML is replaced
- Download count persists
- Consumers who previously downloaded get the old version (no auto-update)

---

See [Writing Flows](writing-flows.md) for YAML syntax. See [CLI Reference](cli.md) for the full command list.
