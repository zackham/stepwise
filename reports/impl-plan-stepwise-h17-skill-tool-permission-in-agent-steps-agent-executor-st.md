---
title: "Implementation Plan: H17 — Skill Tool Permission in Agent Steps"
date: "2026-03-22T18:30:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# H17: Skill Tool Permission in Agent Steps

## Overview

Extend agent step configuration to support the full acpx permission model (`approve_reads` mode) and add tool-level restrictions (`allowed_tools`/`disallowed_tools`) that let flow authors constrain which tools an agent can use. Builds on H15's permission mode foundation.

## Requirements

### R1: Complete permission mode coverage
- **What:** Support all four acpx permission modes in YAML and config: `approve_all`, `approve_reads`, `prompt`, `deny`.
- **Acceptance criteria:** `permissions: approve_reads` in a flow YAML maps to `--approve-reads` on the acpx command line. Existing three modes continue working unchanged.

### R2: Tool-level restrictions in YAML
- **What:** Agent steps accept `allowed_tools` and `disallowed_tools` list fields that restrict which tools the agent can invoke.
- **Acceptance criteria:** A flow with `allowed_tools: [Read, Grep, Glob]` results in the spawned agent being constrained to only those tools. `disallowed_tools: [Bash]` blocks Bash while allowing everything else.

### R3: Tool restrictions passed to agent process
- **What:** Tool restriction config is translated into an enforcement mechanism the underlying agent respects.
- **Acceptance criteria:** The agent process cannot invoke tools outside the allowed set. Verified by integration test or by inspecting the generated configuration.

### R4: Backward compatibility
- **What:** Existing flows without `permissions`, `allowed_tools`, or `disallowed_tools` behave identically to today.
- **Acceptance criteria:** All existing tests pass. No config migration needed.

### R5: Validation
- **What:** Invalid permission values and malformed tool lists are caught at parse time or validate time, not at runtime.
- **Acceptance criteria:** `permissions: bogus` raises a validation error. `allowed_tools: "Read"` (string, not list) raises a parse error.

### R6: Test coverage
- **What:** Unit tests for YAML parsing, config propagation, flag mapping, and validation.
- **Acceptance criteria:** Tests in `test_agent_permissions.py` cover `approve_reads`, `allowed_tools`, `disallowed_tools`, and validation.

## Assumptions

| # | Assumption | Verification |
|---|---|---|
| A1 | acpx supports `--approve-reads` flag | Confirmed: `acpx --help` lists `--approve-reads` ("Auto-approve read/search requests and prompt for writes") |
| A2 | acpx does NOT support `--allowedTools` / `--disallowedTools` flags | Confirmed: `acpx --help` and source code grep show no such flags |
| A3 | The `allowed_tools` config in `examples/self_analysis.py` is dead code — it flows into `AgentExecutor.config` but is never mapped to any CLI flag | Confirmed: `grep allowed_tools src/stepwise/agent.py` returns no matches |
| A4 | `permissions` is already extracted from YAML by `yaml_loader.py` (line 253) | Confirmed: explicit `"permissions"` in the extraction list |
| A5 | Claude Code respects `.claude/settings.local.json` for `allowedTools`/`disallowedTools` | Assumed based on Claude Code documentation — settings.local.json is the standard project-level override mechanism |
| A6 | Other acpx-supported agents (Codex, Gemini) have no tool restriction mechanism accessible from acpx | Assumed — only Claude Code has the settings.local.json convention |

## >>>ESCALATE: Tool restriction mechanism for R2/R3

acpx has **no** `--allowedTools`/`--disallowedTools` CLI flags. The only enforcement mechanism available today is writing `.claude/settings.local.json` in the agent's working directory before spawn. This approach has trade-offs:

**Option A: `.claude/settings.local.json` write-before-spawn**
- Pros: Enforceable, uses Claude Code's native permission system, works today
- Cons: Only works for Claude agents. Requires filesystem manipulation (backup/restore of any existing file). Risk of race conditions if multiple agents share a working directory. Leaves behind `.claude/` directory if cleanup fails.

**Option B: Prompt injection (soft enforcement)**
- Pros: Works for all agent types. No filesystem manipulation. Simple.
- Cons: Not enforceable — agent could ignore the instruction. Relies on model compliance.

**Option C: Defer tool-level restrictions until acpx supports `--allowedTools`**
- Pros: Clean implementation when available. No workarounds.
- Cons: Blocks R2/R3 entirely. Unknown timeline for acpx feature.

**Option D: Option A + Option B combined**
- Pros: Hard enforcement for Claude agents via settings.local.json, soft guidance for all agents via prompt. Best of both worlds.
- Cons: Most complex to implement. Claude-specific codepath.

**Recommendation:** Option D — use `.claude/settings.local.json` for Claude agents (hard enforcement) and inject tool guidance into the prompt for all agents (soft guidance). The settings file approach is reliable because agent steps already manipulate the working directory (writing `.step-io/` files, `.stepwise/emit.flow.yaml`, etc.).

**The plan below assumes Option D is approved.** If a different option is chosen, Steps 3-4 change accordingly.

## Out of Scope

- Adding `--allowedTools`/`--disallowedTools` to acpx itself (external project)
- Tool restriction support for non-Claude agents beyond prompt injection
- Per-tool approval policies (e.g., "approve Bash but auto-approve Read")
- Runtime tool usage auditing or logging
- Changes to the web UI for permission configuration

## Architecture

### Config flow (extended from H15)

```
YAML step definition
  ↓ permissions: "approve_reads"
  ↓ allowed_tools: [Read, Grep, Glob]
  ↓ disallowed_tools: [Bash]
  ↓
yaml_loader.py → ExecutorRef.config = {
    "permissions": "approve_reads",
    "allowed_tools": ["Read", "Grep", "Glob"],
    "disallowed_tools": ["Bash"],
    ...
}
  ↓
AgentExecutor.__init__(**config) → self.config
  ↓
AgentExecutor.start() → spawn_config = dict(self.config)
  ↓
AcpxBackend.spawn(prompt, spawn_config, context)
  ├─ permissions → --approve-reads flag
  ├─ allowed_tools → .claude/settings.local.json (Claude agents)
  └─ allowed_tools → prompt suffix (all agents)
```

### Files modified

| File | Change |
|---|---|
| `src/stepwise/agent.py` | `AcpxBackend.spawn()`: add `approve_reads` flag mapping, tool restriction settings file write + cleanup, prompt tool guidance injection |
| `src/stepwise/yaml_loader.py` | Extract `allowed_tools`, `disallowed_tools` from agent step YAML |
| `src/stepwise/config.py` | Add `approve_reads` to valid `agent_permissions` values |
| `src/stepwise/models.py` | Add `permissions` validation in `WorkflowDefinition.validate()` or `warnings()` |
| `tests/test_agent_permissions.py` | Extend with `approve_reads`, `allowed_tools`, `disallowed_tools`, validation tests |

### YAML syntax

```yaml
steps:
  safe-analysis:
    executor: agent
    prompt: "Analyze this codebase"
    permissions: approve_reads
    allowed_tools: [Read, Grep, Glob]
    outputs: [analysis]

  implement:
    executor: agent
    prompt: "Implement: $spec"
    permissions: approve_all
    disallowed_tools: [Bash]
    outputs: [result]
```

Constraints:
- `allowed_tools` and `disallowed_tools` are mutually exclusive (specifying both is an error)
- Values are lists of strings (tool names like `Read`, `Write`, `Bash`, `Grep`, `Glob`, `Edit`)
- Only meaningful for agent executor type — ignored on other executor types

## Implementation Steps

### Step 1: Add `approve_reads` permission mode (~30 min)

**`src/stepwise/agent.py`** — `AcpxBackend.spawn()` (line 427-432):

Add `approve_reads` branch to the permission flag mapping:

```python
if permissions == "approve_all":
    args.append("--approve-all")
elif permissions == "approve_reads":
    args.append("--approve-reads")
elif permissions == "deny":
    args.append("--deny-all")
```

**`src/stepwise/config.py`** — Update the comment on `agent_permissions` (line 117) to include `approve_reads`:

```python
agent_permissions: str = "approve_all"  # "approve_all" | "approve_reads" | "prompt" | "deny"
```

**`tests/test_agent_permissions.py`** — Add test cases for `approve_reads`:
- `TestConfigAgentPermissions.test_roundtrip` already covers it via the loop — add explicit test
- `TestAcpxBackendPermissions`: add `test_approve_reads_flag` verifying `--approve-reads` in args
- Update `_build_args` helper to include the new branch

### Step 2: Parse `allowed_tools` / `disallowed_tools` from YAML (~30 min)

**`src/stepwise/yaml_loader.py`** (line 253) — Add the two new fields to the agent config extraction list:

```python
for k in ("prompt", "output_mode", "output_path", "emit_flow",
          "working_dir", "permissions", "agent",
          "allowed_tools", "disallowed_tools"):
```

Add type validation after extraction:

```python
for tools_key in ("allowed_tools", "disallowed_tools"):
    if tools_key in config:
        val = config[tools_key]
        if not isinstance(val, list) or not all(isinstance(t, str) for t in val):
            raise ValueError(
                f"Step '{step_name}': '{tools_key}' must be a list of strings"
            )
if "allowed_tools" in config and "disallowed_tools" in config:
    raise ValueError(
        f"Step '{step_name}': cannot specify both 'allowed_tools' and 'disallowed_tools'"
    )
```

**`tests/test_agent_permissions.py`** — Add `TestYAMLToolRestrictions` class:
- `test_allowed_tools_parsed`: verify list flows into `ExecutorRef.config`
- `test_disallowed_tools_parsed`: same for disallowed
- `test_both_raises_error`: verify mutual exclusion
- `test_non_list_raises_error`: verify type validation

### Step 3: Write tool restrictions to `.claude/settings.local.json` (~45 min)

**`src/stepwise/agent.py`** — Add a helper method to `AcpxBackend`:

```python
def _apply_tool_restrictions(self, working_dir: str, config: dict) -> Path | None:
    """Write tool restrictions to .claude/settings.local.json. Returns backup path or None."""
```

Logic:
1. Extract `allowed_tools` / `disallowed_tools` from config
2. If neither is set, return `None` (no-op)
3. Read existing `.claude/settings.local.json` if present → save as `.claude/settings.local.json.stepwise-backup`
4. Merge `allowedTools` / `disallowedTools` keys into settings dict
5. Write the updated file
6. Return the backup path (or sentinel indicating "file was created, not backed up")

Add corresponding cleanup:

```python
def _restore_tool_restrictions(self, working_dir: str, backup_path: Path | None) -> None:
    """Restore original settings.local.json after agent completes."""
```

**`src/stepwise/agent.py`** — In `AcpxBackend.spawn()`, call `_apply_tool_restrictions()` before `Popen`. Store the backup path in `AgentProcess` (add field).

**`src/stepwise/agent.py`** — In `AgentExecutor.start()`, after `self.backend.wait(process)`, call `self.backend._restore_tool_restrictions()`. Use try/finally to ensure cleanup on failure.

**`src/stepwise/agent.py`** — Update `AgentProcess` dataclass to include `settings_backup: Path | None = None`.

### Step 4: Inject tool guidance into prompt (~20 min)

**`src/stepwise/agent.py`** — In `AgentExecutor._render_prompt()` (after line 1236), add tool restriction guidance:

```python
allowed = self.config.get("allowed_tools")
disallowed = self.config.get("disallowed_tools")
if allowed:
    prompt += f"\n\nTool restrictions: You may ONLY use these tools: {', '.join(allowed)}. Do not use any other tools."
elif disallowed:
    prompt += f"\n\nTool restrictions: Do NOT use these tools: {', '.join(disallowed)}. All other tools are available."
```

This provides soft enforcement for all agent types and supplements the hard enforcement from Step 3 for Claude agents.

### Step 5: Add validation warnings (~20 min)

**`src/stepwise/models.py`** — In `WorkflowDefinition.warnings()` (line 720+), add warnings for agent steps:

- Permission value not in `{"approve_all", "approve_reads", "prompt", "deny"}` → warning
- `allowed_tools` or `disallowed_tools` on non-agent step → warning (ignored)
- Empty `allowed_tools` or `disallowed_tools` list → warning

**`src/stepwise/yaml_loader.py`** — Add validation for `permissions` value:

```python
valid_permissions = {"approve_all", "approve_reads", "prompt", "deny"}
if "permissions" in config and config["permissions"] not in valid_permissions:
    raise ValueError(
        f"Step '{step_name}': permissions must be one of {valid_permissions}, "
        f"got '{config['permissions']}'"
    )
```

### Step 6: Tests (~45 min)

Extend `tests/test_agent_permissions.py` with:

**`TestAcpxBackendPermissions`:**
- `test_approve_reads_flag`: verify `--approve-reads` appears in args
- `test_approve_reads_in_roundtrip`: config → backend → flag → correct

**`TestYAMLToolRestrictions` (new class):**
- `test_allowed_tools_parsed`
- `test_disallowed_tools_parsed`
- `test_both_raises_error`
- `test_non_list_raises_error`
- `test_string_instead_of_list_raises_error`

**`TestToolRestrictionSettings` (new class):**
- `test_apply_creates_settings_file`: verify `.claude/settings.local.json` written with correct keys
- `test_apply_backs_up_existing`: verify backup created when file exists
- `test_restore_removes_created_file`: verify cleanup when no prior file
- `test_restore_restores_backup`: verify original file restored
- `test_no_restrictions_is_noop`: verify no file written when neither field set
- `test_disallowed_tools_in_settings`: verify `disallowedTools` key in JSON

**`TestToolRestrictionPrompt` (new class):**
- `test_allowed_tools_in_prompt`: verify prompt contains tool restriction text
- `test_disallowed_tools_in_prompt`: same for disallowed
- `test_no_restrictions_no_prompt_suffix`: verify no injection when neither field set

**`TestPermissionValidation` (new class):**
- `test_invalid_permission_raises`: `permissions: bogus` raises ValueError
- `test_valid_permissions_accepted`: all four modes parse without error

### Step 7: Update example (~10 min)

**`examples/self_analysis.py`** — Now that `allowed_tools` is functional, the example already uses it. Verify it works end-to-end or add a comment noting the feature.

## Testing Strategy

### Unit tests (automated)

```bash
# Run all permission-related tests
uv run pytest tests/test_agent_permissions.py -v

# Run full test suite to verify no regressions
uv run pytest tests/

# Run web tests
cd web && npm run test
```

### Manual integration test

1. Create a test flow with `allowed_tools`:
```yaml
name: tool-restriction-test
steps:
  analyze:
    executor: agent
    prompt: "List the files in this directory using the Bash tool"
    permissions: approve_reads
    allowed_tools: [Read, Grep, Glob]
    outputs: [result]
```

2. Run: `stepwise run tool-restriction-test.flow.yaml --var ...`
3. Verify: Agent uses only Read/Grep/Glob, not Bash
4. Verify: `.claude/settings.local.json` is created before spawn and cleaned up after

### Validation test

```bash
# Should warn on invalid permissions
stepwise validate bad-permissions.flow.yaml
# Expected: error about invalid permission value
```

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `.claude/settings.local.json` write fails (permissions, disk full) | Agent spawns without tool restrictions | Catch IOError, log warning, continue without restrictions. Don't fail the step. |
| Cleanup fails (process killed, power loss) | Stale settings.local.json left in working directory | File is `.local.json` (gitignored by convention). Agent steps already leave artifacts in working dirs. |
| Multiple agents share working directory | Race condition on settings.local.json | Session lock manager already serializes steps sharing a session. For truly concurrent agents in the same dir, this is an existing limitation. Document in YAML reference. |
| Non-Claude agents ignore tool restrictions | Soft enforcement only | Prompt injection provides best-effort. Document that hard enforcement requires Claude agent. |
| Existing flows with typo'd `permissions` value silently ignored | Wrong permission mode applied (falls through to default) | Step 5 adds validation — parse-time error for invalid values. |
| `approve_reads` not tested in CI (needs acpx) | Flag mapping untested end-to-end | Unit tests verify flag mapping logic without spawning a process (same pattern as existing tests). |
