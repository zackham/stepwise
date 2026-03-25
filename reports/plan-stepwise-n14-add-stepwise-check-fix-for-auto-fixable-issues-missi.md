# N14: `stepwise check --fix` and `stepwise test-fixture`

## Overview

Two new CLI capabilities:

1. **`stepwise validate --fix`** — Extends the existing `validate` command with an auto-fix mode that rewrites the YAML file to resolve fixable warnings. The first (and primary) fixable issue: loop exit rules missing `max_iterations`. Future fixable issues can be added incrementally.

2. **`stepwise test-fixture <flow>`** — New command that reads a `.flow.yaml` file and generates a self-contained pytest test file. The generated test replaces real executors (script, llm, agent) with `CallableExecutor` stubs, wires up inputs/outputs correctly, and provides a runnable skeleton that exercises the flow's DAG structure.

Both features build on existing infrastructure: `validate` already detects these warnings via `WorkflowDefinition.warnings()`, and the test patterns are well-established in `tests/conftest.py`.

---

## Requirements

### R1: `stepwise validate --fix`

**R1.1** — Add `--fix` flag to the existing `validate` subparser (`cli.py:3966-3968`).

**R1.2** — When `--fix` is passed and fixable warnings exist, rewrite the YAML file in-place using `ruamel.yaml` round-trip (preserving comments, formatting, key order). Print what was changed.

**R1.3** — First auto-fix: **missing `max_iterations` on loop exit rules**.
- Default value: `10` (safe general-purpose bound — matches the value used in FLOW_REFERENCE.md examples).
- Detection: reuse `WorkflowDefinition.warnings()` — lines matching `"has no max_iterations"`.
- Fix: for each loop exit rule lacking `max_iterations`, insert `max_iterations: 10` into the YAML exit rule block.

**R1.4** — `--fix` must be idempotent: running it twice produces no diff on the second run.

**R1.5** — `--fix` must not apply if `validate()` returns hard errors (structural problems). Fix only warnings on an otherwise valid flow.

**R1.6** — After applying fixes, re-validate and re-warn to show the user the updated state.

**R1.7** — If `--fix` is passed but there are no fixable warnings, print "Nothing to fix" and exit successfully.

**Acceptance criteria:**
- `stepwise validate flow.yaml --fix` on a flow with unbounded loops adds `max_iterations: 10` to each loop exit rule missing it, prints the changes, and re-validates clean.
- Running `--fix` again produces "Nothing to fix."
- Comments, blank lines, and key ordering in the YAML are preserved.
- Hard validation errors block `--fix` (user must fix those manually first).

### R2: `stepwise test-fixture <flow>`

**R2.1** — New CLI subcommand `test-fixture` that accepts a flow name or path.

**R2.2** — Output: a complete, runnable pytest file printed to stdout (or written to a file with `-o`).

**R2.3** — Generated test file structure:
```python
"""Auto-generated test fixture for <flow-name>."""
import pytest
from tests.conftest import register_step_fn, run_job_sync
from stepwise.models import (
    WorkflowDefinition, StepDefinition, ExecutorRef,
    InputBinding, ExitRule, JobStatus,
)

# --- Step stubs ---
# One register_step_fn per step, returning placeholder outputs

# --- Test class ---
class TestFlowName:
    def test_happy_path(self, async_engine):
        """Run the flow to completion with stub executors."""
        # Build WorkflowDefinition inline (mirrors YAML structure)
        # Create job with sample inputs
        # run_job_sync()
        # Assert COMPLETED + check artifact keys
```

**R2.4** — Executor mapping for stubs:
| YAML executor | Test stub |
|---|---|
| `script` / `run:` | `register_step_fn` returning dict with declared outputs |
| `llm` | `register_step_fn` returning dict with declared outputs |
| `agent` | `register_step_fn` returning dict with declared outputs |
| `external` | `register_step_fn` returning dict with declared outputs (no suspension in test) |
| `poll` | `register_step_fn` returning dict with declared outputs |
| `mock_llm` | Keep as-is (already a test executor) |
| `callable` | Keep as-is (already uses `register_step_fn`) |

All non-callable/non-mock executors are replaced with `callable` + `register_step_fn` stubs. The stubs return placeholder values matching the step's `outputs` list.

**R2.5** — For steps with exit rules, generate stub return values that satisfy the first `advance` rule (happy path). If no advance rule exists, return values that satisfy the first non-loop rule.

**R2.6** — For steps with `$job.*` inputs, generate a `job_inputs` dict with placeholder values and pass it to `create_job(inputs=job_inputs)`.

**R2.7** — For steps with `when` conditions, include a comment noting the condition so the user can adjust stub values.

**R2.8** — For `for_each` steps, generate a stub that returns a list for the source field, and a sub-flow test that processes a single item.

**Acceptance criteria:**
- `stepwise test-fixture my-flow` prints a valid Python file to stdout.
- `stepwise test-fixture my-flow -o tests/test_my_flow.py` writes to file.
- The generated file passes `python -m py_compile` (syntactically valid).
- Running the generated test with `uv run pytest <file>` passes (stubs produce COMPLETED job).
- Flow with loops, branches, external steps, and `$job` inputs all generate correct stubs.

---

## Assumptions (verified against code)

1. **`ruamel.yaml` is available** — already used in `server.py:2169,2263,2320,2377` for round-trip YAML editing. The `_ruamel_load_and_patch()` helper at `server.py:2257` is a proven pattern.

2. **Warning text is stable** — `models.py:791-793` emits `"has no max_iterations"` which can be matched to identify fixable issues. The fix targets `exits:` blocks in the raw YAML.

3. **`validate` runs before `warnings`** — `cmd_validate` at `cli.py:886` calls `wf.validate()` first, then `wf.warnings()` only if validation passes. `--fix` follows the same order.

4. **CLI pattern** — Commands are registered as `sub.add_parser()` in `build_parser()` (~line 3962) and dispatched via the `handlers` dict at `cli.py:4986`. Handler functions return exit codes.

5. **`load_workflow_yaml()`** — `yaml_loader.py` parses YAML into `WorkflowDefinition` with full `StepDefinition`, `InputBinding`, `ExitRule`, `ExecutorRef` objects. The generator can introspect these to emit test code.

6. **Test fixtures** — `tests/conftest.py` provides `async_engine`, `store`, `registry`, `register_step_fn`, `run_job_sync`. All generated tests use these.

7. **`ExecutorRef.type`** identifies the executor — `"script"`, `"llm"`, `"agent"`, `"external"`, `"poll"`, `"callable"`, `"mock_llm"`. The YAML shorthand `run:` maps to `type="script"`.

8. **Exit rule config keys** — `rule.config` contains `"action"`, `"target"`, `"condition"`, `"max_iterations"` (when present). The YAML key is `max_iterations` at the same level as `action` and `target` in the `exits:` list item.

---

## Implementation Steps

### Phase 1: `stepwise validate --fix`

#### Step 1: Add fixable-warning detection to `models.py`

**File:** `src/stepwise/models.py`

Add a new method `fixable_warnings()` to `WorkflowDefinition` that returns structured fix descriptors instead of string warnings:

```python
@dataclass
class FixableWarning:
    step_name: str
    rule_name: str
    fix_type: str  # "add_max_iterations"
    description: str
    default_value: Any  # e.g., 10
```

Method `fixable_warnings() -> list[FixableWarning]`: iterates the same loop as `warnings()` lines 786-794, but returns structured objects. This avoids fragile string parsing of warning text.

Alternatively (simpler): skip the dataclass and just return a list of dicts. Either way, the key data is `(step_name, rule_index, fix_type, default_value)`.

**Decision:** Use a simple list of dicts to avoid adding a new model class for an internal-only structure:

```python
def fixable_warnings(self) -> list[dict]:
    fixes = []
    for name, step in self.steps.items():
        for i, rule in enumerate(step.exit_rules):
            if (rule.config.get("action") == "loop"
                    and not rule.config.get("max_iterations")):
                fixes.append({
                    "step": name,
                    "rule_name": rule.name,
                    "rule_index": i,
                    "fix": "add_max_iterations",
                    "value": 10,
                })
    return fixes
```

#### Step 2: Add `--fix` flag to CLI parser

**File:** `src/stepwise/cli.py` (~line 3966-3968)

```python
p_validate = sub.add_parser("validate", help="Validate a flow file")
p_validate.add_argument("flow", help="Flow name or path to .flow.yaml file")
p_validate.add_argument("--fix", action="store_true", help="Auto-fix fixable warnings")
```

#### Step 3: Add YAML fix-application function

**File:** `src/stepwise/yaml_loader.py` (new function, near end of file)

```python
def apply_fixes(file_path: str, fixes: list[dict]) -> str:
    """Apply auto-fixes to a flow YAML file using ruamel.yaml round-trip.

    Returns the updated YAML string. Does NOT write to disk (caller decides).
    """
    from ruamel.yaml import YAML
    from io import StringIO

    ryaml = YAML()
    ryaml.preserve_quotes = True

    with open(file_path) as f:
        data = ryaml.load(f)

    for fix in fixes:
        if fix["fix"] == "add_max_iterations":
            step = data["steps"][fix["step"]]
            exits = step.get("exits", [])
            # Find the matching exit rule by index
            if fix["rule_index"] < len(exits):
                exits[fix["rule_index"]]["max_iterations"] = fix["value"]

    buf = StringIO()
    ryaml.dump(data, buf)
    return buf.getvalue()
```

Place this in `yaml_loader.py` since it's the module responsible for YAML ↔ model translation. The function mirrors the `_ruamel_load_and_patch` pattern from `server.py:2257`.

#### Step 4: Extend `cmd_validate` for `--fix` mode

**File:** `src/stepwise/cli.py` (modify `cmd_validate` at line 873)

After the existing validation-pass logic (line 903, after printing warnings):

```python
if getattr(args, 'fix', False):
    fixes = wf.fixable_warnings()
    if not fixes:
        io.log("info", "Nothing to fix.")
        return EXIT_SUCCESS

    # Apply fixes to YAML
    from stepwise.yaml_loader import apply_fixes
    updated_yaml = apply_fixes(str(flow_path), fixes)
    flow_path.write_text(updated_yaml)

    # Report what changed
    for fix in fixes:
        if fix["fix"] == "add_max_iterations":
            io.log("success", f"  Fixed: step '{fix['step']}' rule '{fix['rule_name']}' → max_iterations: {fix['value']}")

    # Re-validate to show clean state
    wf2 = load_workflow_yaml(str(flow_path))
    remaining = wf2.warnings()
    if remaining:
        for w in remaining:
            io.log("warn" if w.startswith("⚠") else "info", f"  {w}")
    else:
        io.log("success", "All warnings resolved.")
```

Key behavior:
- `--fix` is gated behind validation passing (hard errors block fix).
- Each fix is reported individually.
- Re-validation confirms the fix worked.

#### Step 5: Tests for `--fix`

**File:** `tests/test_validate_fix.py` (new file)

Tests:
1. **Roundtrip preservation** — Create a temp YAML file with comments, run `apply_fixes`, verify comments survive.
2. **Missing max_iterations fixed** — YAML with unbounded loop → `apply_fixes` → reload → `fixable_warnings()` returns empty.
3. **Idempotent** — Run fix twice, second time produces identical output.
4. **Hard errors block fix** — `cmd_validate` with `--fix` on invalid YAML returns error, does not modify file.
5. **No fixable warnings** — `cmd_validate --fix` on clean YAML prints "Nothing to fix."
6. **Multiple fixes in one pass** — YAML with 3 unbounded loops → all 3 get `max_iterations: 10`.

---

### Phase 2: `stepwise test-fixture`

#### Step 6: Create test fixture generator module

**File:** `src/stepwise/test_gen.py` (new module)

This module takes a `WorkflowDefinition` and generates Python test code. It lives in its own module to keep `cli.py` lean.

```python
def generate_test_fixture(wf: WorkflowDefinition, flow_name: str) -> str:
    """Generate a pytest test file for the given workflow."""
```

Key logic:

**a) Collect job-level inputs:**
```python
job_inputs = {}
for step in wf.steps.values():
    for binding in step.inputs:
        if binding.source_step == "$job":
            job_inputs[binding.source_field] = _placeholder(binding.source_field)
```

**b) Generate stub functions:**
For each step, create a `register_step_fn` call that returns a dict matching the step's `outputs`:
```python
def _stub_outputs(step: StepDefinition) -> dict:
    """Generate placeholder output dict for a step."""
    result = {}
    for out in step.outputs:
        if step.output_schema and out in step.output_schema:
            spec = step.output_schema[out]
            result[out] = _typed_placeholder(spec)
        else:
            result[out] = f"stub_{out}"
    return result
```

For steps with exit rules that have an `advance` action with a condition, attempt to reverse-engineer output values that satisfy the condition (best-effort — fall back to generic placeholders with a `# TODO` comment).

**c) Build the WorkflowDefinition inline:**
Emit Python code that constructs the same `WorkflowDefinition` programmatically, but with all executors replaced by `callable` type pointing to the registered stub functions.

**d) Generate test method:**
```python
def test_{flow_name}_happy_path(self, async_engine):
    # register stubs
    # build workflow
    # create job
    # run_job_sync
    # assert completed
    # assert output keys
```

**e) String generation:**
Use `textwrap.dedent` and f-strings to build the output. No template engine dependency.

#### Step 7: Register CLI command

**File:** `src/stepwise/cli.py`

Parser (in `build_parser()`):
```python
p_tf = sub.add_parser("test-fixture", help="Generate a pytest test harness for a flow")
p_tf.add_argument("flow", help="Flow name or path to .flow.yaml file")
p_tf.add_argument("-o", "--output", help="Output file path (default: stdout)")
```

Handler:
```python
def cmd_test_fixture(args: argparse.Namespace) -> int:
    from stepwise.flow_resolution import resolve_flow
    from stepwise.yaml_loader import load_workflow_yaml
    from stepwise.test_gen import generate_test_fixture

    flow_path = resolve_flow(args.flow, _project_dir(args))
    wf = load_workflow_yaml(str(flow_path))

    errors = wf.validate()
    if errors:
        # warn but don't block — test might be for debugging
        io.log("warn", "Flow has validation errors")

    code = generate_test_fixture(wf, wf.metadata.name if wf.metadata else flow_path.stem)

    if args.output:
        Path(args.output).write_text(code)
        io.log("success", f"Wrote test fixture to {args.output}")
    else:
        print(code)

    return EXIT_SUCCESS
```

Add to handlers dict:
```python
"test-fixture": cmd_test_fixture,
```

#### Step 8: Handle edge cases in generator

**File:** `src/stepwise/test_gen.py`

Special cases:
- **`for_each` steps** — Generate a stub that returns a list for the source step, and inline the sub-flow steps as additional callable stubs.
- **`when` conditions** — Add a comment `# when: <condition>` above the step definition.
- **Optional inputs** — Set `optional=True` in the generated `InputBinding`.
- **Exit rules** — Preserve them in the generated workflow (they're structural and affect engine behavior). Only the executor changes.
- **`after:` deps** — Preserve as-is.
- **Loops** — Stub returns values that trigger `advance` (not `loop`), so the test completes in one pass.

#### Step 9: Tests for test-fixture generator

**File:** `tests/test_test_gen.py` (new file)

Tests:
1. **Simple linear flow** — 2 steps, A→B. Generated test compiles and runs.
2. **Flow with loops** — Step with loop exit rules. Generated stub triggers advance path.
3. **Flow with branches** — `when` conditions. Both branches get stubs.
4. **Flow with $job inputs** — `job_inputs` dict includes all referenced fields.
5. **Flow with external step** — External executor replaced with callable stub.
6. **Generated code compiles** — `compile(code, "<test>", "exec")` succeeds for all cases.
7. **Generated test runs** — Use `async_engine` fixture + `exec()` to actually run the generated test (meta-test).
8. **Output file mode** — `-o` flag writes to temp file.

---

## File Change Summary

| File | Change |
|---|---|
| `src/stepwise/models.py` | Add `fixable_warnings()` method to `WorkflowDefinition` |
| `src/stepwise/yaml_loader.py` | Add `apply_fixes()` function |
| `src/stepwise/cli.py` | Add `--fix` flag to validate parser; add `cmd_test_fixture` handler; register both in parser + handlers dict |
| `src/stepwise/test_gen.py` | **New file** — test fixture code generator |
| `tests/test_validate_fix.py` | **New file** — tests for `--fix` functionality |
| `tests/test_test_gen.py` | **New file** — tests for test fixture generator |

---

## Testing Strategy

### Unit tests

```bash
# Run all new tests
uv run pytest tests/test_validate_fix.py tests/test_test_gen.py -v

# Run specific test
uv run pytest tests/test_validate_fix.py::test_fix_adds_max_iterations -v
uv run pytest tests/test_test_gen.py::TestTestGen::test_linear_flow -v
```

### Integration tests (manual)

```bash
# Create a flow with unbounded loops
cat > /tmp/test-fix.flow.yaml << 'EOF'
name: test-fix
steps:
  generate:
    run: echo '{"text": "hello"}'
    outputs: [text]
    exits:
      - name: good
        when: "outputs.text == 'done'"
        action: advance
      - name: retry
        when: "True"
        action: loop
        target: generate
EOF

# Validate — should warn about missing max_iterations
uv run stepwise validate /tmp/test-fix.flow.yaml

# Fix — should add max_iterations: 10
uv run stepwise validate /tmp/test-fix.flow.yaml --fix

# Verify fix is idempotent
uv run stepwise validate /tmp/test-fix.flow.yaml --fix
# Should print "Nothing to fix."

# Generate test fixture
uv run stepwise test-fixture /tmp/test-fix.flow.yaml -o /tmp/test_test_fix.py

# Verify generated test compiles
python -m py_compile /tmp/test_test_fix.py

# Run generated test
uv run pytest /tmp/test_test_fix.py -v
```

### Regression

```bash
# Full test suite must still pass
uv run pytest tests/ -x
```

---

## Ordering and Dependencies

```
Step 1 (models.py fixable_warnings)
  → Step 3 (yaml_loader.py apply_fixes) — needs fix descriptors
    → Step 4 (cli.py --fix) — needs both
      → Step 5 (tests) — validates the feature

Step 2 (cli.py parser flag) — independent, can parallel with Step 1

Step 6 (test_gen.py) — independent of Phase 1
  → Step 7 (cli.py command) — needs generator
    → Step 8 (edge cases) — iterative refinement
      → Step 9 (tests) — validates the feature
```

Phase 1 and Phase 2 are independent and can be developed in parallel. Phase 1 is simpler and should land first.
