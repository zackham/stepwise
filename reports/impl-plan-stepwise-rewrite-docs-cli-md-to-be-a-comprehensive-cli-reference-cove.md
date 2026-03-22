---
title: "Implementation Plan: Rewrite docs/cli.md to comprehensive CLI reference"
date: "2026-03-22T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Comprehensive CLI Reference

Rewrite `docs/cli.md` to cover all 32 stepwise commands (organized into 6 groups), adding 10 missing commands and updating 6 existing entries with missing flags. Single file edit — no code changes, no new files.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|-------------|-------------------|
| R1 | All 32 commands documented | `grep -cP '^\#{2,3} .stepwise ' docs/cli.md` returns ≥ 32 (one heading per command, plus subcommand headings for server/cache/config) |
| R2 | Grouped by function | The doc contains exactly 6 group headings: Core, Jobs, Server, Registry, Configuration, Utility — each as `## <Group> Commands` |
| R3 | Each command section has 4 parts | Every command section contains: (a) 1-2 sentence description, (b) code-fenced usage example, (c) flags table (if flags exist), (d) at least 1 example |
| R4 | Flags match actual `--help` output | For every command, the flags table is a 1:1 match with `stepwise <cmd> --help` output. Zero invented flags, zero omitted flags. Verified by the diff-based test in Testing Strategy. |
| R5 | Existing accurate content preserved | The following sections survive intact (possibly relocated): Global Flags (L5-14), Exit Codes (L16-36), Project Hooks (L695-721), Server-Aware CLI (L725-735), Signal Handling (L739-741), Project Discovery (L743-749) |
| R6 | Cross-links to other docs | At least 5 cross-links to: `quickstart.md`, `yaml-format.md`, `patterns.md`, `flow-sharing.md`, `agent-integration.md` |

## Assumptions

| # | Assumption | Verification |
|---|------------|-------------|
| A1 | `docs/cli.md` is the only file to modify | Confirmed: ran `grep -rl 'stepwise tail\|stepwise chain\|stepwise cache' docs/` — no other doc references the undocumented commands. CLAUDE.md mentions `cache` but as operational reference, not user docs. |
| A2 | 32 commands exist (spec says 34) | Verified: `stepwise --help` lists exactly 32 subcommands. The spec's "34" count is inaccurate. Counting: init, server, run, chain, check, validate, preflight, diagram, templates, config, new, share, get, search, info, jobs, status, cancel, schema, tail, logs, output, list, wait, fulfill, agent-help, cache, login, logout, update, welcome, uninstall = 32. |
| A3 | Existing doc covers 22 commands, not 2 | Verified: `grep -cP '^## .stepwise' docs/cli.md` = 22. The spec's claim of "only 2 commands" is wrong — the doc already covers init, new, run, server, validate, diagram, jobs, status, cancel, list, wait, templates, config, get, share, search, info, schema, output, fulfill, agent-help, update. |
| A4 | Existing command sections are mostly accurate | Verified by comparing each section's flags table against actual `--help` output for all 22 documented commands. 16 are fully accurate; 6 have missing flags (enumerated in Gap Analysis). No section has invented/wrong flags. |
| A5 | The doc uses flat `##` headings for commands | Verified: `grep -n '^##' docs/cli.md` shows all commands at `##` level, subcommands (server start/stop, config set/get, run modes) at `###`. The rewrite introduces `##` for groups and `###` for commands, which changes heading depth by 1 — existing `###` subcommands become `####`. |
| A6 | No other docs link to cli.md anchors | Verified: `grep -rl 'cli\.md#' docs/` returns no results. Changing heading structure won't break cross-doc links. |

## Out of Scope

- **Tutorial/narrative content** — belongs in `docs/quickstart.md` and `docs/patterns.md`; cli.md is a reference
- **API endpoint docs** — covered by `docs/api.md`
- **YAML flow format** — covered by `docs/yaml-format.md`
- **New documentation files** — this is a single-file rewrite
- **Code changes** — no CLI behavior changes, no source edits
- **Updating other docs** — no cross-doc anchor links break (verified in A6)

## Architecture

### Existing document structure (`docs/cli.md`, 750 lines)

The current doc uses a flat structure: `# CLI Reference` → `## Global Flags` → `## Exit Codes` → 22× `## stepwise <cmd>` → `## Project Hooks` → `## Server-Aware CLI` → `## Signal Handling` → `## Project Discovery`. Each command section follows a consistent pattern (verified by reading lines 40-200, 300-410, 620-670):

1. **Heading**: `` ## `stepwise <cmd>` ``
2. **Description**: 1-2 sentences
3. **Usage examples**: code-fenced bash blocks
4. **Flags table**: `| Flag | Description |` markdown table
5. **Separator**: `---`

Complex commands (`run`, `server`, `config`) use `###` subheadings for modes/subcommands.

### Target document structure

The rewrite adds one heading level for command groups. This is the only structural change.

```
# CLI Reference                          (existing, L1)
  overview paragraph + command-group ToC  (NEW — ~15 lines)
## Global Flags                           (existing, L5-14 — preserved verbatim)
## Exit Codes                             (existing, L16-36 — preserved verbatim)
## Core Commands                          (NEW group heading)
  ### `stepwise run`                      (existing L86-195 — add 5 missing flags to table)
  ### `stepwise chain`                    (NEW)
  ### `stepwise new`                      (existing L70-83 — unchanged)
  ### `stepwise validate`                 (existing L252-271 — unchanged)
  ### `stepwise check`                    (NEW)
  ### `stepwise preflight`                (NEW)
## Job Commands                           (NEW group heading)
  ### `stepwise jobs`                     (existing L300-329 — add --meta flag)
  ### `stepwise status`                   (existing L333-361 — unchanged)
  ### `stepwise output`                   (existing L593-617 — add step_name positional)
  ### `stepwise tail`                     (NEW)
  ### `stepwise logs`                     (NEW)
  ### `stepwise wait`                     (existing L410-427 — unchanged)
  ### `stepwise cancel`                   (existing L364-385 — unchanged)
  ### `stepwise fulfill`                  (existing L620-651 — unchanged)
  ### `stepwise list`                     (existing L387-408 — unchanged)
## Server Commands                        (NEW group heading)
  ### `stepwise server`                   (existing L198-248 — unchanged)
## Registry Commands                      (NEW group heading)
  ### `stepwise share`                    (existing L504-526 — unchanged)
  ### `stepwise get`                      (existing L482-502 — unchanged)
  ### `stepwise search`                   (existing L527-546 — unchanged)
  ### `stepwise info`                     (existing L547-557 — unchanged)
  ### `stepwise login`                    (NEW)
  ### `stepwise logout`                   (NEW)
## Configuration Commands                 (NEW group heading)
  ### `stepwise config`                   (existing L451-479 — add init subcommand)
  ### `stepwise init`                     (existing L40-67 — add --no-skill, --skill flags)
  ### `stepwise templates`                (existing L429-448 — unchanged)
  ### `stepwise schema`                   (existing L559-591 — unchanged)
  ### `stepwise diagram`                  (existing L274-297 — unchanged)
## Utility Commands                       (NEW group heading)
  ### `stepwise agent-help`               (existing L654-670 — add --format flag)
  ### `stepwise cache`                    (NEW — with 3 subcommands)
  ### `stepwise update`                   (existing L673-691 — unchanged)
  ### `stepwise welcome`                  (NEW)
  ### `stepwise uninstall`                (NEW)
## Project Hooks                          (existing L695-721 — preserved verbatim)
## Server-Aware CLI                       (existing L725-735 — preserved verbatim)
## Signal Handling                        (existing L739-741 — preserved verbatim)
## Project Discovery                      (existing L743-749 — preserved verbatim)
```

Estimated final length: ~950-1000 lines (up from 750). The 10 new command sections average ~20 lines each (~200 lines added), minus ~10 lines saved by deduplicating the existing `run` section's flag descriptions that are now shared with `chain`.

### Per-command section template (matching existing pattern from L300-329, L364-385)

```markdown
### `stepwise <cmd>`

<1-2 sentence description.>

\```bash
stepwise <cmd> <args>
\```

| Flag | Description |
|------|-------------|
| `--flag` | Description |

\```bash
# Example usage
stepwise <cmd> <example-args>
\```
```

## Gap Analysis

### Missing commands (10)

| Command | Source (cli.py) | Flags (from `--help`) | Content to write |
|---------|----------------|----------------------|-----------------|
| `chain` | `cmd_chain` L1921 | Same as `run` plus positional `flows` (2+ required) | ~30 lines: description, note re: shares flags with `run`, 2 examples showing 2-flow and 3-flow chains |
| `check` | `cmd_check` L1436 | `flow` (positional only) | ~12 lines: description, usage, example with sample output table |
| `preflight` | `cmd_preflight` L1510 | `flow` (positional), `--var KEY=VALUE` | ~15 lines: description (combined check: validation + config + requirements + models), usage, example with sample output |
| `tail` | `cmd_tail` L358 | `job_id` (positional only) | ~12 lines: description, note re: requires running server, usage, Ctrl+C to stop |
| `logs` | `cmd_logs` L380 | `job_id` (positional only) | ~12 lines: description, usage, example output showing formatted event history |
| `cache` | `cmd_cache` L3991 | 3 subcommands | ~25 lines: description + 3 sub-sections for `stats`, `clear` (`--flow`, `--step`), `debug` (flow, step positional, `--var`) |
| `login` | `cmd_login` L2670 | (none) | ~8 lines: description (GitHub Device Flow), usage, example showing user code prompt |
| `logout` | `cmd_logout` L2743 | (none) | ~5 lines: description, usage |
| `welcome` | `cmd_welcome` L4127 | (none) | ~8 lines: description (interactive demo chooser), usage |
| `uninstall` | `cmd_uninstall` L3560 | `--yes/-y`, `--force`, `--remove-flows`, `--cli` | ~15 lines: description, flags table, example, note about active job safety |

### Existing commands needing flag updates (6)

| Command | Current flags table line range | Missing flags (from `--help`) | Specific edit |
|---------|-------------------------------|-------------------------------|---------------|
| `run` | L173-189 | `--local`, `--rerun STEP`, `--notify URL`, `--notify-context JSON`, `--meta KEY=VALUE` | Add 5 rows to the flags table at L189 |
| `init` | L64-66 | `--no-skill`, `--skill DIR` | Add 2 rows to the flags table at L66 |
| `jobs` | L319-326 | `--meta KEY=VALUE` | Add 1 row to the flags table at L326 |
| `agent-help` | L667-669 | `--format {compact,json,full}` | Add 1 row to the flags table at L669 |
| `config` | L451-479 | `init` subcommand (creates per-flow config) | Add `### Initialise flow config` subsection after L479 |
| `output` | L593-617 | `step_name` positional argument | Update usage line and add note about positional shorthand |

## Implementation Steps

### Step 1: Add overview and command-group table of contents (~10 min)

**File:** `docs/cli.md` — edit lines 1-3

Insert after the `# CLI Reference` heading (L1): a 2-sentence overview paragraph, then a bulleted list of 6 command groups with short descriptions. Link to `quickstart.md` for getting started.

**Depends on:** nothing
**Why first:** establishes the grouping structure that all subsequent steps reference

### Step 2: Wrap existing command sections in group headings (~15 min)

**File:** `docs/cli.md` — add 6 new `##` lines, change 22 existing `##` to `###`

Insert `## Core Commands` before `stepwise run` (L86). Insert `## Job Commands` before `stepwise jobs` (L300). Insert `## Server Commands` before `stepwise server` (L198). Insert `## Registry Commands` before `stepwise share` (L504). Insert `## Configuration Commands` before `stepwise config` (L451). Insert `## Utility Commands` before `stepwise agent-help` (L654).

Demote all 22 existing `## \`stepwise ...\`` headings to `###`. Demote existing `###` subheadings (run modes, server subcommands, config subcommands) to `####`.

Reorder sections to match the target structure (move `init` from before `new` into Configuration group, move `diagram`/`schema`/`templates` into Configuration, etc.).

**Depends on:** Step 1 (overview must exist before group headers reference it)
**Why second:** all subsequent steps (adding new commands, updating flags) need to know which group to insert into

### Step 3: Add `stepwise chain` section (~10 min)

**File:** `docs/cli.md` — insert new `###` section in Core Commands group, after `run`

Write ~30 lines: description ("Chain multiple flows into a linear pipeline — outputs from each flow feed into the next"), usage syntax, note that it shares all flags with `run` (link to run section instead of duplicating the table), 2 examples. Flags unique to chain: positional `flows` (2+ required).

Source: `cmd_chain` at `src/stepwise/cli.py:1921`, `stepwise chain --help`

**Depends on:** Step 2 (Core Commands group must exist)

### Step 4: Add `stepwise check` section (~5 min)

**File:** `docs/cli.md` — insert new `###` section in Core Commands group, after `preflight`

Write ~12 lines: description ("Verify model resolution for every LLM step in a flow — shows which model each step will use and whether API keys are configured"), usage, example showing the table output format (STEP, MODEL, RESOLVED, SOURCE columns).

Source: `cmd_check` at `src/stepwise/cli.py:1436`, `stepwise check --help`

**Depends on:** Step 2

### Step 5: Add `stepwise preflight` section (~5 min)

**File:** `docs/cli.md` — insert new `###` section in Core Commands group, after `validate`

Write ~15 lines: description ("Combined pre-run check: validates flow, resolves config variables, verifies system requirements, and checks model resolution"), usage with `--var`, example showing multi-section output (Flow, Config, Requirements, Models).

Source: `cmd_preflight` at `src/stepwise/cli.py:1510`, `stepwise preflight --help`

**Depends on:** Step 2

### Step 6: Add `stepwise tail` and `stepwise logs` sections (~10 min)

**File:** `docs/cli.md` — insert 2 new `###` sections in Job Commands group, after `output`

`tail` (~12 lines): description ("Stream live events for a running job via WebSocket — requires a running server"), usage, note about Ctrl+C to stop, note about server requirement.
Source: `cmd_tail` at `src/stepwise/cli.py:358`

`logs` (~12 lines): description ("Show the full event history for a job — works with or without a running server"), usage, example showing formatted event output.
Source: `cmd_logs` at `src/stepwise/cli.py:380`

**Depends on:** Step 2

### Step 7: Add `stepwise cache` section with subcommands (~10 min)

**File:** `docs/cli.md` — insert new `###` section in Utility Commands group

Write ~25 lines: brief description, then 3 `####` subheadings for `stats`, `clear`, `debug`. Each with usage line and flags. `clear` has `--flow`, `--step` filters. `debug` takes positional `flow` and `step` plus `--var`.

Source: `cmd_cache` at `src/stepwise/cli.py:3991`, `stepwise cache {stats,clear,debug} --help`

**Depends on:** Step 2

### Step 8: Add `stepwise login`, `logout`, `welcome`, `uninstall` sections (~10 min)

**File:** `docs/cli.md`

`login` (~8 lines, Registry group): "Log in to the Stepwise registry via GitHub Device Flow." Usage, example showing device code prompt.
Source: `cmd_login` at `src/stepwise/cli.py:2670`

`logout` (~5 lines, Registry group): "Log out of the Stepwise registry." Usage only.
Source: `cmd_logout` at `src/stepwise/cli.py:2743`

`welcome` (~8 lines, Utility group): "Interactive demo that lets you try a simulated dev workflow in the browser or terminal." Usage.
Source: `cmd_welcome` at `src/stepwise/cli.py:4127`

`uninstall` (~15 lines, Utility group): "Remove stepwise from the current project. Deletes `.stepwise/` directory and all job data." Flags table (`--yes/-y`, `--force`, `--remove-flows`, `--cli`), safety note about active job check.
Source: `cmd_uninstall` at `src/stepwise/cli.py:3560`

**Depends on:** Step 2

### Step 9: Update 6 existing command sections with missing flags (~15 min)

**File:** `docs/cli.md` — edit 6 existing sections

| Command | Edit |
|---------|------|
| `run` | Add 5 rows to flags table: `--local` (force local execution, skip server delegation), `--rerun STEP` (bypass cache for this step, repeatable), `--notify URL` (webhook URL for job event notifications), `--notify-context JSON` (JSON context for webhook payloads), `--meta KEY=VALUE` (set job metadata, dot notation) |
| `init` | Add 2 rows to flags table: `--no-skill` (skip agent skill installation), `--skill DIR` (install agent skill to specific directory) |
| `jobs` | Add 1 row to flags table: `--meta KEY=VALUE` (filter by metadata) |
| `agent-help` | Add 1 row to flags table: `--format {compact,json,full}` (output format, default: compact) |
| `config` | Add `#### Initialize flow config` subsection documenting `stepwise config init <flow-name>` |
| `output` | Add `step_name` positional argument to usage line and description |

**Depends on:** Step 2 (heading levels must be finalized before editing subsections)

### Step 10: Add cross-links (~5 min)

**File:** `docs/cli.md`

Add links at these specific locations:
- Overview section: link to [Quickstart](quickstart.md) for installation and first flow
- `run` description: link to [YAML Format](yaml-format.md) for flow authoring
- `chain` description: link to [Patterns](patterns.md) for pipeline composition
- `fulfill` description: link to [Agent Integration](agent-integration.md) for the mediation pattern
- `share`/`get` descriptions: link to [Flow Sharing](flow-sharing.md) for registry details
- `agent-help` description: link to [Agent Integration](agent-integration.md)

**Depends on:** Steps 3-9 (all sections must exist before adding links to them)

### Step 11: Final validation pass (~10 min)

Run the full test suite from Testing Strategy (below). Fix any issues found.

**Depends on:** Step 10

## Testing Strategy

All tests are CLI commands that can be run immediately after the edit. No test infrastructure needed.

### T1: All 32 commands have headings

```bash
grep -cP '^\#{2,3} `stepwise ' docs/cli.md
```
**Expected:** ≥ 32. **Pass criteria:** each of the 32 commands from `stepwise --help` appears as a `##` or `###` heading.

### T2: All 6 group headings present

```bash
grep -cP '^## (Core|Job|Server|Registry|Configuration|Utility) Commands' docs/cli.md
```
**Expected:** 6.

### T3: No command from `--help` is missing from the doc

```bash
stepwise --help 2>/dev/null | grep -oP '^\s{4}(\S+)' | awk '{print $1}' | sort > /tmp/cli_commands.txt
grep -oP '(?<=`stepwise )\S+(?=`)' docs/cli.md | sort -u > /tmp/doc_commands.txt
comm -23 /tmp/cli_commands.txt /tmp/doc_commands.txt
```
**Expected:** empty output (no commands in CLI but missing from doc).

### T4: Flag accuracy — automated diff for all commands

```bash
# For each command, extract flags from --help and from doc, diff them
for cmd in run chain init validate check preflight jobs status output tail logs wait cancel fulfill list server cache agent-help config get share search info schema diagram templates login logout update welcome uninstall; do
  help_flags=$(stepwise $cmd --help 2>/dev/null | grep -oP '^\s+--\S+' | sed 's/,.*//;s/\s//g' | sort -u)
  doc_flags=$(sed -n "/\`stepwise $cmd\`/,/^---$/p" docs/cli.md | grep -oP '`--\S+' | sed 's/`//g;s/,.*//g' | sort -u)
  diff_result=$(diff <(echo "$help_flags") <(echo "$doc_flags"))
  if [ -n "$diff_result" ]; then
    echo "MISMATCH: $cmd"
    echo "$diff_result"
  fi
done
```
**Expected:** no output for any command. Any mismatch indicates an invented or missing flag.

### T5: Markdown renders without errors

```bash
python3 -c "
import re
with open('docs/cli.md') as f:
    content = f.read()
# Check for unclosed code blocks
blocks = content.count('\`\`\`')
assert blocks % 2 == 0, f'Unclosed code block ({blocks} backtick fences)'
# Check heading hierarchy (no jumps > 1 level)
levels = [len(m.group(1)) for m in re.finditer(r'^(#{1,6}) ', content, re.MULTILINE)]
for i in range(1, len(levels)):
    assert levels[i] <= levels[i-1] + 1, f'Heading jump at position {i}: {levels[i-1]} → {levels[i]}'
print('OK: markdown structure valid')
"
```
**Expected:** `OK: markdown structure valid`

### T6: Cross-links resolve to existing files

```bash
grep -oP '\[.*?\]\(([-a-z]+\.md)' docs/cli.md | grep -oP '[a-z-]+\.md' | sort -u | while read f; do
  [ -f "docs/$f" ] || echo "BROKEN LINK: $f"
done
```
**Expected:** no output.

### T7: Preserved sections are intact

```bash
# Verify the 4 appendix sections survived
for section in "Project Hooks" "Server-Aware CLI" "Signal Handling" "Project Discovery"; do
  grep -q "## $section" docs/cli.md || echo "MISSING: $section"
done
```
**Expected:** no output.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Heading-level change breaks external links | Low | Medium | Verified in A6: no other doc links to cli.md anchors. GitHub anchors auto-generate from headings — but since no external doc references them, no breakage. |
| Flags change between plan and implementation | Low | Low | Single-session task; run T4 (flag diff) at the end to catch any drift. |
| Rewrite loses accurate prose from existing sections | Low | High | Implementation steps explicitly preserve 16 unchanged sections verbatim. Only 6 sections get flag additions (rows appended to existing tables). Diff review before commit will catch any accidental deletions. |
| Document becomes unwieldy (>1000 lines) | Medium | Low | Target ~950 lines. New sections average 12-15 lines. `login`/`logout`/`welcome` are under 10 lines each. If it exceeds 1000, trim examples (1 per command instead of 2) for minor commands. |
| `config init` subcommand behavior unclear from source | Medium | Low | Read `cmd_config` in `cli.py` to verify exact behavior before writing. If unclear, document only the usage syntax from `--help` and add "See `stepwise config init --help` for details." |
