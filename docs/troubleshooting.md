# Troubleshooting

Centralized error reference for Stepwise. Errors are grouped by where they surface: validation, runtime, or CLI.

---

## Validator Errors (`stepwise validate`)

Run `stepwise validate <flow>` before every run. These errors come from YAML parsing and structural checks.

### YAML Syntax

| Error | Cause | Fix |
|-------|-------|-----|
| `YAML parse error: <detail>` | Invalid YAML syntax (bad indentation, missing colon, etc.) | Fix the YAML syntax. Use a linter or `yamllint`. |
| `YAML root must be a mapping` | Root element is a list or scalar instead of a key-value mapping | Ensure the file starts with `name:` and `steps:` at the top level. |
| `File not found: <path>` | The flow file doesn't exist at the given path | Check the path. Use `stepwise flows` to list discovered flows. |
| `Workflow must have a 'steps' mapping` | The `steps:` key is missing or not a dict | Add a `steps:` block with at least one step definition. |

### Step Definition

| Error | Cause | Fix |
|-------|-------|-----|
| `Step '<name>': must have either 'run' or 'executor'` | Step has no execution method | Add `run: <command>` for scripts or `executor: <type>` for other executors. |
| `Step '<name>': cannot combine flow with run/executor` | A flow delegation step also has `run` or `executor` | Remove `run`/`executor` — flow steps delegate to a sub-flow, not run directly. |
| `Step '<name>': Agent executor requires 'prompt'` | Agent step missing prompt | Add `prompt:` or `prompt_file:` to the step. |
| `Step '<name>': LLM executor requires 'prompt'` | LLM step missing prompt | Add `prompt:` or `prompt_file:` to the step. |
| `Step '<name>': Poll executor requires 'check_command'` | Poll step missing check command | Add `check_command:` with the shell command to poll. |
| `Step '<name>': cannot specify both 'prompt' and 'prompt_file'` | Both inline and file-based prompt declared | Use one or the other, not both. |
| `Step '<name>': flow steps must declare outputs` | Sub-flow step has no `outputs` list | Add `outputs: [field1, field2]` matching the sub-flow's terminal step outputs. |

### Input Bindings

| Error | Cause | Fix |
|-------|-------|-----|
| `Step '<name>': input binding references unknown step '<step>'` | Input `from:` points to a step that doesn't exist | Check spelling. Use `stepwise validate` output to see valid step names. |
| `Step '<name>': input binding references unknown field '<field>' on step '<step>'` | Source step doesn't declare that output field | Add the field to the source step's `outputs:` list, or fix the field name. |
| `Invalid input source '<source>'. Expected 'step_name.field_name' or '$job.field_name'` | Malformed input reference | Use `step-name.field` for step outputs or `$job.param` for job inputs. |
| `Step '<name>': input name '<local>' is not a valid identifier` | Input local name contains invalid characters | Use `[A-Za-z_][A-Za-z0-9_]*` — letters, digits, underscores only. |
| `Step '<name>': duplicate local_name '<local>' in inputs` | Same input name used twice in one step | Rename one of the duplicate inputs. |
| `Step '<name>': input '<local>' any_of must be a list with >= 2 entries` | `any_of` has fewer than 2 sources | Provide at least 2 alternative sources, or use a simple input binding instead. |
| `Step '<name>': after references unknown step '<step>'` | `after:` lists a step that doesn't exist | Fix the step name in the `after:` list. |

### Exit Rules

| Error | Cause | Fix |
|-------|-------|-----|
| `Step '<name>': exit rule '<rule>' missing 'when' condition` | Exit rule has no `when:` clause | Add `when: "<expression>"` to the exit rule. |
| `Step '<name>': exit rule '<rule>' invalid action '<action>'` | Action is not `advance`, `loop`, `escalate`, or `abandon` | Use one of the four valid actions. |
| `Step '<name>': exit rule '<rule>' has action 'loop' but no 'target'` | Loop action needs a target step to jump back to | Add `target: <step-name>` to the exit rule. |
| `Step '<name>': exit rule '<rule>' has 'advance' with 'target'` | Advance shouldn't specify a target | Remove `target:` from advance rules. Use step-level `when:` for conditional branching. |
| `Step '<name>': exit rule '<rule>' loop target '<target>' is not a valid step` | Loop target step doesn't exist | Fix the target step name. |
| `Invalid expression syntax: <detail>` | `when:` condition has a Python syntax error | Fix the expression. Valid: `outputs.score >= 0.8`, `attempt < 3`, etc. |
| `Access to '<attr>' is not allowed in expressions` | Expression accesses `_private` attributes | Only access public attributes. No `__dunder__` or `_private` access. |
| `Lambda expressions are not allowed` | Expression uses `lambda` | Use simple expressions, not lambdas. |
| `f-strings are not allowed in expressions` | Expression uses an f-string | Use string concatenation or keep f-strings in scripts instead. |

### Outputs

| Error | Cause | Fix |
|-------|-------|-----|
| `Step '<name>': duplicate output '<field>'` | Same output field name listed twice | Remove the duplicate from the `outputs:` list. |
| `Step '<name>': 'outputs' must be a list or mapping` | Outputs is a string or other non-list/dict type | Use `outputs: [field1, field2]` (list) or `outputs: {field1: {type: string}}` (mapping). |
| `Step '<name>': output field '<field>' has invalid type '<type>'` | Unknown output field type in schema | Valid types: `str`, `text`, `number`, `bool`, `choice`. |
| `Step '<name>': output field '<field>' (type=choice) requires non-empty 'options' list` | Choice field without options | Add `options: [opt1, opt2, ...]` to the field spec. |

### Workflow Structure

| Error | Cause | Fix |
|-------|-------|-----|
| `Workflow has no steps` | Empty `steps:` block | Add at least one step definition. |
| `Workflow has no entry steps (all steps have dependencies)` | Every step depends on something — no starting point | At least one step must have no `inputs:` from other steps and no `after:`. |
| `Workflow has no terminal steps` | No steps without downstream dependents | This usually indicates a circular dependency. Check your DAG structure. |
| `Step '<name>' is unreachable — no path from any entry step` | Step is disconnected from the workflow graph | Wire it into the DAG via `inputs:`, `after:`, or remove it. |

### Validator Warnings

These are advisory (the flow still runs) but should be treated as defects:

| Warning | Cause | Fix |
|---------|-------|-----|
| Unbounded loop | Loop exit rule has no `attempt >= N` guard or `max_iterations` | Add `when: "attempt < 5"` or `max_iterations: 5` to prevent infinite loops. |
| Uncovered output combinations | External step outputs don't all have matching exit rules | Add exit rules covering all possible output value combinations. |
| Type coercion safety | Exit rule uses `float()` or `int()` on potentially None/non-numeric output | Add a guard: `when: "outputs.score is not None and float(outputs.score) >= 0.8"`. |

### Coordination Errors

Detected by `stepwise validate` when steps share named sessions or use loop-back patterns:

| Error | Cause | Fix |
|-------|-------|-----|
| `pair_unsafe: steps 'A' and 'B' both write to session 'S'` | Two session writers can run concurrently — the validator can't prove ordering or mutual exclusion | Add `after: [A]` to B to force ordering, or use predicate-form `when:` clauses (e.g., `is_null: true` vs `is_null: false` on the same input) to prove they never both run |
| `cyclic_dependency: cycle detected` | Steps form a cycle with no loop exit rule to break it | Add an `exits: [{action: loop, target: <step>}]` rule on the cycle's tail step |
| `loop_back_binding_ambiguous_closure` | A loop-back input binding has no guard for iter-1 (when the producer hasn't run yet) | Add `optional: true` to the binding, use `any_of` with a forward fallback source, or gate the step with `when: {input: <name>, is_present: true}` |
| `fork_from requires session` | `fork_from` target step doesn't declare `session:` (no session to fork from) | Add `session: <name>` to the target step |
| `fork_from: $job.<input> requires type: session` | A `fork_from` references a job input that isn't typed as `session` | Change the input declaration to `type: session` |
| `_session on step without session:` | An input binding references `step._session` but the step has no `session:` declared | Add `session: <name>` to the source step, or use a regular output instead |

---

## Engine Runtime Errors

Errors that occur during job execution.

### Step Execution

| Error | Cause | Fix |
|-------|-------|-----|
| `Step '<name>' executor crashed: <ExceptionType>: <message>` | Executor raised an unhandled exception | Check the step's command/config. Use `stepwise logs <job-id>` for full context. |
| `Exit code <N>` | Script step returned non-zero exit code | Fix the script. Test it standalone: `bash -c '<command>'`. |
| `No exit rule matched for step '<name>' (artifact: [keys])` | Step has explicit `advance` rules but none matched the output | Add exit rules covering all output combinations, or add a catch-all `when: "True"` rule. |
| `Unknown executor type: '<type>'` | Executor type not registered | Valid built-in types: `script`, `llm`, `agent`, `external`, `poll`, `mock_llm`. Check spelling. |
| `Input names rejected by blocklist: <names>` | Input name matches a protected env var name | Rename the input. Blocked names: `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `PATH`, `HOME`. |

### Limits

| Error | Cause | Fix |
|-------|-------|-----|
| `Duration limit exceeded: <elapsed>m > <limit>m` | Step ran longer than `max_duration_minutes` | Increase the limit in step config, or optimize the step. |
| `Cost limit exceeded: $<cost> > $<limit>` | LLM/agent step exceeded `max_cost_usd` | Increase the limit, use a cheaper model, or reduce prompt size. |
| `Token count limit exceeded: <tokens> > <limit>` | Step exceeded `max_tokens` | Increase the limit or reduce input size. |
| `Artifact too large: <bytes> bytes (limit: 5MB)` | Step output exceeds 5 MB | Reduce output size or split into smaller steps. Write large data to files instead. |

### Job State

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot start job in status <status>` | Tried to start a job that's not PENDING | Check `stepwise status <job-id>`. Only PENDING jobs can be started. |
| `Cannot pause job in status <status>` | Tried to pause a non-RUNNING job | The job must be RUNNING to pause it. |
| `Cannot resume job in status <status>` | Tried to resume a job that's not PAUSED/CANCELLED | The job must be PAUSED or CANCELLED to resume. |
| `Cannot rerun step '<name>': latest run is <status>` | Step is currently active (RUNNING/SUSPENDED/DELEGATED) | Cancel the active run first, then rerun. |
| `Job depth <N> exceeds maximum of 10` | Too many nested sub-flows | Reduce nesting. Flatten sub-flows or restructure the workflow. |
| `Invalid workflow: <errors>` | Job created with a workflow that fails validation | Fix the validation errors (see Validator Errors above). |

### Fulfillment

| Error | Cause | Fix |
|-------|-------|-----|
| `Run <id> is not suspended (status: <status>)` | Tried to fulfill a run that isn't waiting for input | Check `stepwise list --suspended` for current suspended runs. |
| `Run <id> has no watch spec` | Suspended run has no external input configuration | This is a bug in the flow — the step should use `executor: external`. |
| `Payload missing required field '<field>'` | Fulfillment JSON is missing a required output field | Include all fields listed in `expected_outputs`. Use `stepwise status <job-id> --output json` to see required fields. |
| `Payload validation failed: <details>` | Field values don't match the output schema (wrong type, out of range, invalid choice) | Check field constraints: number min/max, valid choice options, expected types. |

### For-Each

| Error | Cause | Fix |
|-------|-------|-----|
| `For-each step '<name>': source step has no completed run` | The step producing the list hasn't completed | Check upstream step status. It must complete before for-each can fan out. |
| `For-each step '<name>': '<step>.<field>' is not a list` | The source field value is not a list/array | Ensure the upstream step outputs a JSON array for the for-each field. |
| `All <N> sub-jobs failed` | Every item in the fan-out failed | Check individual sub-job errors via `stepwise status <job-id> --output json`. |

### Agent / LLM

| Error | Cause | Fix |
|-------|-------|-----|
| `LLM API error: <detail>` | OpenRouter API call failed | Check your API key: `stepwise config get openrouter_api_key`. Verify the model exists. |
| `Could not parse LLM output into declared output fields` | LLM response didn't contain the expected JSON structure | Simplify outputs, improve the prompt, or try a different model. |
| `Missing output fields: <fields>` | LLM response is missing some declared output fields | Ensure your prompt instructs the model to produce all declared outputs. |

### Derived Outputs

| Error | Cause | Fix |
|-------|-------|-----|
| `Derived output '<field>' expression failed: <error>` | Python expression in `derived_outputs` raised an exception | Check the expression syntax. Common issues: referencing fields not in the artifact, division by zero, type mismatches. |

---

## CLI Errors

### Configuration

| Error | Cause | Fix |
|-------|-------|-----|
| `No .stepwise/ found (searched up from <dir>). Run 'stepwise init' to create a project.` | No project directory in the path | Run `stepwise init` in your project root, or use `--project-dir`. |
| `Project already initialized in <path>. Use --force to reinitialize.` | `.stepwise/` already exists | Use `stepwise init --force` to reinitialize. |
| `Error: Unknown config key '<key>'` | Invalid key passed to `stepwise config` | Valid keys: `openrouter_api_key`, `default_model`. |

### Flow Resolution

| Error | Cause | Fix |
|-------|-------|-----|
| `File not found: <path>` | Flow file doesn't exist | Check the path. Use `stepwise flows` to see discovered flows. |
| `Error loading flow: <detail>` | Flow file exists but has parse errors | Run `stepwise validate <flow>` for detailed error messages. |
| `Error: Invalid flow name: '<name>'` | Flow name has disallowed characters | Flow names must match `[a-zA-Z0-9_.+-]+`. |
| `Error: Directory already exists: <path>` | `stepwise new` target directory exists | Choose a different name or delete the existing directory. |

### Server Connection

| Error | Cause | Fix |
|-------|-------|-----|
| `Error: Could not connect to server: <detail>` | CLI can't reach the Stepwise server | Check if the server is running: `stepwise server status`. Start it: `stepwise server start`. |
| `Server did not start within 5 seconds` | Server startup timed out | Check `.stepwise/logs/server.log` for errors. Verify the port isn't in use. |

### Job Operations

| Error | Cause | Fix |
|-------|-------|-----|
| `Error: Job not found: <job-id>` | Job ID doesn't exist in the database | Use `stepwise jobs` to list valid job IDs. |
| `Error: Invalid status '<status>'` | Bad `--status` filter value | Valid statuses: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled`. |
| `Error: Invalid --meta format: '<arg>' (expected KEY=VALUE)` | Malformed `--meta` flag | Use `--meta sys.key=value` or `--meta app.key=value`. |
| `Error: notify_context must be valid JSON` | `--notify-context` is not valid JSON | Pass valid JSON: `--notify-context '{"key": "value"}'`. |

---

## Quick Diagnostic Commands

Use these commands to diagnose issues before or during a run:

```bash
# Validate a flow file (catch errors before running)
stepwise validate my-flow.flow.yaml

# Check model resolution (verify API keys and model aliases)
stepwise check my-flow.flow.yaml

# Full pre-run check (config + requirements + models)
stepwise preflight my-flow.flow.yaml

# Tail live events for a running job
stepwise tail <job-id>

# View full event history for a job
stepwise logs <job-id>

# Check job status with full detail
stepwise status <job-id> --output json

# List all suspended steps across jobs
stepwise list --suspended

# View server status and log location
stepwise server status

# Check cache state
stepwise cache stats

# Debug cache key for a step
stepwise cache debug my-flow.flow.yaml fetch --input url="https://example.com"
```

### Diagnostic Workflow

1. **Before running:** `stepwise validate` then `stepwise preflight`
2. **Flow won't start:** Check `stepwise server status`, try `--local` flag to bypass server
3. **Step failing:** `stepwise logs <job-id>` for events, `stepwise status <job-id> --output json` for per-step detail
4. **Step stuck:** `stepwise list --suspended` to find waiting external steps, `stepwise tail <job-id>` for live events
5. **Cache issues:** `stepwise cache stats` to check hit rates, `stepwise run --rerun <step>` to bypass cache for one step
6. **Agent/LLM errors:** `stepwise config get openrouter_api_key` to verify key, `stepwise check <flow>` for model resolution
