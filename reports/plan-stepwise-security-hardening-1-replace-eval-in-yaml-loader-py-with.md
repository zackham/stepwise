# Security Hardening Plan

Harden three attack surfaces: (1) add AST validation before `eval()` in expression evaluation to block dunder attribute traversal, (2) shell-quote user inputs at the engine interpolation boundary to prevent command injection in script and poll executors, (3) namespace user input env vars under `STEPWISE_INPUT_` with a dual-export deprecation period and input-name identifier validation.

---

## Requirements

### R1: AST Validation Before `eval()`

Add an AST validation pass before all three `eval()` call sites in `src/stepwise/yaml_loader.py`. The validator parses the expression, walks the tree to reject dunder attribute access, then delegates to the existing restricted `eval()`. This preserves full backward compatibility with generator expressions (`test_yaml_loader.py:45–60`), dict method calls like `outputs.get('_delegated', False)` (`CLAUDE.md:343`), and collapse-to-False `when` semantics (`yaml_loader.py:124–131`).

**Acceptance criteria:**

| Expression | Function | Expected |
|---|---|---|
| `"outputs.status == 'done'"` | `evaluate_exit_condition` | `True` |
| `"any(s < 0.5 for s in outputs.scores)"` | `evaluate_exit_condition` | `True` (generator expression) |
| `"outputs.get('key', 'fallback') == 'fallback'"` | `evaluate_exit_condition` | `True` (dict method call) |
| `"float(outputs.get('quality_score', 0)) >= 0.8"` | `evaluate_exit_condition` | `True` (nested call — from `examples/report-test-loop.flow.yaml:18`) |
| `"().__class__.__bases__[0].__subclasses__()"` | `evaluate_exit_condition` | raises `ValueError` |
| `"outputs.__class__.__bases__"` | `evaluate_exit_condition` | raises `ValueError` |
| `"x.__class__.__bases__"` | `evaluate_when_condition` | returns `False` (not `ValueError`) |
| `"undefined_var > 5"` | `evaluate_when_condition` | returns `False` (existing behavior) |
| `"val.__class__.__name__"` | `evaluate_derived_outputs` | raises `ValueError` |
| All 12 tests in `TestEvaluateExitCondition` | — | pass unchanged |

### R2: Shell-Quote Inputs at Engine Interpolation Boundary

Apply `shlex.quote()` to user input values when they are substituted into `command` and `check_command` config keys inside `_interpolate_config()` (`src/stepwise/engine.py:78–122`). This is the single point where user inputs enter shell command strings — it feeds both `ScriptExecutor` (via `registry_factory.py:40–44`) and `PollExecutor` (via `registry_factory.py:50–54`). Remove the redundant `Template.safe_substitute()` in `PollExecutor.start()` (`src/stepwise/executors.py:480–482`).

**Acceptance criteria:**

| Config key | Input | Result |
|---|---|---|
| `command: "curl $url"` | `{"url": "http://x; rm -rf /"}` | `"curl 'http://x; rm -rf /'"` |
| `check_command: "gh pr view $pr_number"` | `{"pr_number": "123"}` | `"gh pr view 123"` |
| `check_command: "echo $val"` | `{"val": "$(cat /etc/shadow)"}` | `"echo '$(cat /etc/shadow)'"` |
| `prompt: "Waiting for $name"` | `{"name": "it's a test"}` | `"Waiting for it's a test"` (NOT quoted) |
| `model: "$model_name"` | `{"model_name": "claude"}` | `"claude"` (NOT quoted) |

### R3: Namespaced Input Environment Variables

Prefix user input env vars with `STEPWISE_INPUT_` in `ScriptExecutor.start()` (`src/stepwise/executors.py:321–327`). During a deprecation period, also export bare names except for a system-critical blocklist. Validate that input `local_name` values are valid shell identifiers in `_parse_inputs()` (`src/stepwise/yaml_loader.py:172–218`).

**Acceptance criteria:**

| Input | `STEPWISE_INPUT_url` set? | Bare `url` set? | Notes |
|---|---|---|---|
| `{"url": "https://example.com"}` | Yes | Yes (deprecated) | Normal input |
| `{"PATH": "/evil"}` | Yes | **No** | System blocklist |
| `{"HOME": "/evil"}` | Yes | **No** | System blocklist |
| `{"foo-bar": "x"}` | — | — | YAML parse error (not a valid identifier) |

---

## Assumptions (verified with line numbers)

| # | Assumption | Evidence |
|---|---|---|
| 1 | All `eval()` calls use `SAFE_BUILTINS` as `__builtins__` | `yaml_loader.py:102`, `:119`, `:145` |
| 2 | `_DotDict.__getattr__` cannot block `obj.__class__` — Python MRO resolves `__class__` before `__getattr__` fires | `yaml_loader.py:82–92` — `_DotDict` inherits from `dict`; `dict.__class__` resolves via `type.__dict__['__class__']` descriptor |
| 3 | `_interpolate_config()` is the real interpolation boundary for BOTH executor types | `engine.py:1537–1543` calls `_interpolate_config(exec_ref.config, inputs)` in `_prepare_step_run()` before `registry.create(exec_ref)` at `:1561+` |
| 4 | Script factory receives already-interpolated config | `registry_factory.py:40–44` — factory reads `cfg.get("command")` from the interpolated config dict |
| 5 | Poll factory receives already-interpolated config | `registry_factory.py:50–54` — factory reads `cfg.get("check_command")` from interpolated config; `PollExecutor.start()` re-runs `safe_substitute` at `executors.py:480–481` redundantly |
| 6 | `evaluate_when_condition` intentionally returns `False` on errors | `yaml_loader.py:124–131` — catches `NameError`, `AttributeError`, `TypeError` → `False`; engine depends on this at `engine.py:1073–1084` |
| 7 | Generator expressions are used in real exit rules | `test_yaml_loader.py:45–60` — `any(s < 0.5 for s in outputs.scores)` |
| 8 | `outputs.get()` is used in documented exit rules | `examples/report-test-loop.flow.yaml:18` — `float(outputs.get('quality_score', 0)) >= 0.8`; `CLAUDE.md:343` — `outputs.get('_delegated', False)` |
| 9 | Bare env vars are a documented feature | `docs/executors.md:19` — "Input values are passed as environment variables"; `:27` — "All step inputs as env vars"; `:37` — `url = os.environ["url"]` |
| 10 | Input `local_name` is not validated as a shell identifier | `yaml_loader.py:177` — iterates `local_name` from dict keys; `models.py:654–662` — only checks for duplicate `local_name` |
| 11 | Poll `check_command` runs with `shell=True` | `engine.py:2462–2464` — `subprocess.run(check_command, shell=True, ...)` |

---

## Out of Scope

- SQL injection in `store.py` (parameterized queries)
- Agent prompt injection (separate concern)
- YAML deserialization (`yaml.safe_load()` already used)
- Web UI XSS (React auto-escapes)
- `_DotDict.__getattr__` hardening (MRO bypasses it; AST is the real control)
- Removing `shell=True` from poll commands (they need pipes/jq)

---

## Architecture

```
yaml_loader.py  →  R1: add _validate_expression_ast() before eval() at lines 108, 123, 151
                    R3: add identifier validation in _parse_inputs() at line 177
engine.py       →  R2: modify _interpolate_config() at lines 78–122 to quote command fields
executors.py    →  R2: remove redundant safe_substitute in PollExecutor.start() at lines 480–482
                    R3: dual-export env vars in ScriptExecutor.start() at lines 321–327
models.py       →  R3: add identifier validation in duplicate-check loop at lines 654–662
```

Module DAG unchanged: `models → executors → engine → server`.

---

## Implementation Steps

### Step 1: Add `_validate_expression_ast()` to `yaml_loader.py`

**File:** `src/stepwise/yaml_loader.py`

**1a.** Add `import ast` after line 9 (`import re`).

**1b.** Add function after `SAFE_BUILTINS` dict (after line 71):

```python
def _validate_expression_ast(expr: str) -> None:
    """Reject dangerous AST patterns before eval().

    Raises ValueError if the expression contains:
    - Attribute access starting with _ (blocks __class__, __bases__, etc.)
    - f-strings (can embed arbitrary expressions)
    - Lambda expressions
    - Await expressions
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(
                f"Access to '{node.attr}' is not allowed in expressions"
            )
        if isinstance(node, ast.JoinedStr):
            raise ValueError("f-strings are not allowed in expressions")
        if isinstance(node, ast.Lambda):
            raise ValueError("Lambda expressions are not allowed")
```

**1c.** Modify `evaluate_exit_condition()` at line 107–110 — add validation before eval:

```python
    try:
        _validate_expression_ast(condition)          # NEW
        return bool(eval(condition, namespace))
    except Exception as e:
        raise ValueError(f"Exit condition '{condition}' failed: {e}") from e
```

**1d.** Modify `evaluate_when_condition()` at line 119–131 — validate first, collapse structural rejections to `False`:

```python
    namespace: dict = {"__builtins__": SAFE_BUILTINS}
    for k, v in inputs.items():
        namespace[k] = _DotDict(v) if isinstance(v, dict) else v
    try:
        _validate_expression_ast(condition)           # NEW
    except ValueError:
        import logging
        logging.getLogger("stepwise.engine").warning(
            "when condition %r rejected by AST validator", condition
        )
        return False
    try:
        return bool(eval(condition, namespace))
    except (NameError, AttributeError, TypeError):
        return False
    except Exception:
        import logging
        logging.getLogger("stepwise.engine").warning(
            "when condition %r failed", condition, exc_info=True
        )
        return False
```

**1e.** Modify `evaluate_derived_outputs()` at line 149–155 — add validation inside loop:

```python
    for field_name, expr in derived.items():
        try:
            _validate_expression_ast(expr)            # NEW
            results[field_name] = eval(expr, namespace)
        except Exception as e:
            raise ValueError(
                f"Derived output '{field_name}' expression failed: {e}"
            ) from e
```

**Why AST validation + eval (not a pure AST interpreter):** The codebase relies on generator expressions (`test_yaml_loader.py:45–60`), dict `.get()` method calls (`examples/report-test-loop.flow.yaml:18`), and `is` comparisons (`test_yaml_loader.py:88`). Reimplementing Python expression semantics for all of these is error-prone. Validating the AST then calling restricted `eval()` gets the same security properties with full compatibility.

**Commit:** `security: add AST validation to block dunder traversal in expression eval`

---

### Step 2: Tests for AST validation

**File:** `tests/test_yaml_loader.py`

**2a.** Add imports at line 5–10 — extend the existing import block:

```python
from stepwise.yaml_loader import (
    YAMLLoadError,
    _DotDict,
    evaluate_exit_condition,
    evaluate_when_condition,       # NEW
    evaluate_derived_outputs,      # NEW
    load_workflow_string,
)
```

**2b.** Add test class after `TestDotDict` (after line 118):

```python
class TestExpressionSecurity:
    """AST validator blocks dunder traversal while preserving safe patterns."""

    # ── Blocked patterns ────────────────────────────────────────

    def test_class_traversal_blocked(self):
        with pytest.raises(ValueError, match="__class__"):
            evaluate_exit_condition(
                "().__class__.__bases__[0].__subclasses__()", {}, attempt=1
            )

    def test_dunder_on_outputs(self):
        with pytest.raises(ValueError, match="__class__"):
            evaluate_exit_condition("outputs.__class__", {"x": 1}, attempt=1)

    def test_dunder_globals(self):
        with pytest.raises(ValueError, match="__globals__"):
            evaluate_exit_condition("float.__globals__", {}, attempt=1)

    def test_fstring_blocked(self):
        with pytest.raises(ValueError, match="f-string"):
            evaluate_exit_condition("f'{True}'", {}, attempt=1)

    def test_lambda_blocked(self):
        with pytest.raises(ValueError, match="Lambda"):
            evaluate_exit_condition("(lambda: 1)()", {}, attempt=1)

    def test_import_blocked(self):
        """__import__ is not in SAFE_BUILTINS — eval raises NameError → ValueError."""
        with pytest.raises(ValueError):
            evaluate_exit_condition("__import__('os')", {}, attempt=1)

    def test_derived_blocks_dunder(self):
        with pytest.raises(ValueError, match="__name__"):
            evaluate_derived_outputs(
                {"evil": "val.__class__.__name__"}, {"val": "hello"}
            )

    # ── when collapses to False (not raise) ─────────────────────

    def test_when_dunder_returns_false(self):
        result = evaluate_when_condition("x.__class__.__bases__", {"x": "hi"})
        assert result is False

    def test_when_fstring_returns_false(self):
        result = evaluate_when_condition("f'{x}'", {"x": "hi"})
        assert result is False

    def test_when_missing_var_returns_false(self):
        result = evaluate_when_condition("undefined > 5", {})
        assert result is False

    # ── Backward-compatible patterns that MUST still work ───────

    def test_generator_with_any(self):
        assert evaluate_exit_condition(
            "any(s < 0.5 for s in outputs.scores)",
            {"scores": [0.3, 0.7]}, attempt=1,
        )

    def test_generator_with_all(self):
        assert evaluate_exit_condition(
            "all(s > 0.1 for s in outputs.scores)",
            {"scores": [0.3, 0.7]}, attempt=1,
        )

    def test_dict_get_method(self):
        assert evaluate_exit_condition(
            "outputs.get('missing', 'fallback') == 'fallback'",
            {}, attempt=1,
        )

    def test_dict_get_with_float_cast(self):
        """Real pattern from examples/report-test-loop.flow.yaml:18."""
        assert evaluate_exit_condition(
            "float(outputs.get('quality_score', 0)) >= 0.8",
            {"quality_score": "0.9"}, attempt=1,
        )

    def test_delegated_get_pattern(self):
        """Real pattern from CLAUDE.md:343 — dict .get() with underscore key."""
        assert evaluate_exit_condition(
            "outputs.get('_delegated', False)",
            {"_delegated": True}, attempt=1,
        )

    def test_is_comparison(self):
        """Real pattern from test_yaml_loader.py:88."""
        assert evaluate_exit_condition(
            "max_attempts is not None and attempt >= max_attempts",
            {}, attempt=5, max_attempts=5,
        )

    def test_sorted_indexing(self):
        assert evaluate_exit_condition(
            "sorted(outputs.scores)[0] == 1",
            {"scores": [3, 1, 2]}, attempt=1,
        )
```

**Commit:** `test: add AST validation security and compatibility tests`

**Verify:** `uv run pytest tests/test_yaml_loader.py -v`

---

### Step 3: Shell-quote inputs in `_interpolate_config()`

**File:** `src/stepwise/engine.py`

**3a.** Add `import shlex` after line 10 (`import subprocess`).

**3b.** Add constant before `_interpolate_config()` (before line 78):

```python
# Config keys whose values are passed to subprocess.run(shell=True).
# Values interpolated into these keys must be shell-quoted.
_SHELL_COMMAND_KEYS = frozenset({"command", "check_command"})
```

**3c.** Modify `_interpolate_config()` at lines 106–122. Replace the loop body (lines 108–121):

```python
    # Build a shell-safe copy for command fields
    quoted_inputs = {k: shlex.quote(v) for k, v in str_inputs.items()}

    result = {}
    changed = False
    for k, v in config.items():
        if isinstance(v, str) and "$" in v:
            # Use quoted values for shell-executed fields, raw for others
            effective = quoted_inputs if k in _SHELL_COMMAND_KEYS else str_inputs
            new_v = v
            for sk in sorted(effective, key=len, reverse=True):
                if "." in sk and ("$" + sk) in new_v:
                    new_v = new_v.replace("$" + sk, effective[sk])
            new_v = Template(new_v).safe_substitute(effective)
            if new_v != v:
                changed = True
            result[k] = new_v
        else:
            result[k] = v
    return result if changed else config
```

**Commit:** `security: shell-quote user inputs interpolated into command/check_command`

---

### Step 4: Remove redundant interpolation in `PollExecutor.start()`

**File:** `src/stepwise/executors.py`

**4a.** Replace lines 478–482:

**Before:**
```python
    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # Interpolate $var placeholders in check_command and prompt
        str_inputs = {k: str(v) if v is not None else "" for k, v in inputs.items()}
        check_command = Template(self.check_command).safe_substitute(str_inputs)
        prompt = Template(self.prompt).safe_substitute(str_inputs) if self.prompt else ""
```

**After:**
```python
    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # check_command and prompt are already interpolated by
        # engine._interpolate_config() before executor creation.
        check_command = self.check_command
        prompt = self.prompt
```

**Commit:** `refactor: remove redundant PollExecutor.start() interpolation`

---

### Step 5: Tests for shell quoting

**File:** `tests/test_engine_interpolation_security.py` (new)

```python
"""Tests for shell injection prevention in _interpolate_config()."""
import shlex

from stepwise.engine import _interpolate_config


class TestInterpolateConfigQuoting:
    def test_command_value_quoted(self):
        config = {"command": "curl $url"}
        result = _interpolate_config(config, {"url": "http://x; rm -rf /"})
        assert result["command"] == f"curl {shlex.quote('http://x; rm -rf /')}"

    def test_check_command_value_quoted(self):
        config = {"check_command": "gh pr view $pr_number --json state"}
        result = _interpolate_config(config, {"pr_number": "123; cat /etc/passwd"})
        expected = f"gh pr view {shlex.quote('123; cat /etc/passwd')} --json state"
        assert result["check_command"] == expected

    def test_simple_value_no_extra_quotes(self):
        config = {"check_command": "gh pr view $pr_number --json state"}
        result = _interpolate_config(config, {"pr_number": "123"})
        assert result["check_command"] == "gh pr view 123 --json state"

    def test_command_substitution_attack_blocked(self):
        config = {"command": "echo $user_input"}
        result = _interpolate_config(config, {"user_input": "$(cat /etc/shadow)"})
        assert result["command"] == "echo '$(cat /etc/shadow)'"

    def test_backtick_attack_blocked(self):
        config = {"command": "echo $user_input"}
        result = _interpolate_config(config, {"user_input": "`cat /etc/shadow`"})
        assert result["command"] == "echo '`cat /etc/shadow`'"

    def test_prompt_not_quoted(self):
        config = {"prompt": "Hello $name", "command": "echo $name"}
        result = _interpolate_config(config, {"name": "it's a test"})
        assert result["prompt"] == "Hello it's a test"
        assert "'" in result["command"]  # shlex.quote wraps it

    def test_model_not_quoted(self):
        config = {"model": "$model_name"}
        result = _interpolate_config(config, {"model_name": "anthropic/claude-sonnet-4-20250514"})
        assert result["model"] == "anthropic/claude-sonnet-4-20250514"

    def test_no_change_returns_original(self):
        config = {"command": "echo hello"}
        result = _interpolate_config(config, {"unused": "value"})
        assert result is config  # same object — no copy
```

**File:** `tests/test_poll_executor.py`

**5a.** Update `test_interpolates_inputs_in_check_command` (line 51–57). The `PollExecutor.start()` no longer interpolates, so this test must be updated to verify pass-through:

```python
    def test_passes_through_preinterpolated_command(self):
        """PollExecutor.start() passes command through as-is (engine interpolates upstream)."""
        executor = PollExecutor(
            check_command="gh pr view 42 --json state",  # already interpolated
            interval_seconds=10,
        )
        result = executor.start({"pr_number": "42"}, self._make_context())
        assert result.watch.config["check_command"] == "gh pr view 42 --json state"
```

**Commit:** `test: add shell quoting tests for _interpolate_config`

**Verify:** `uv run pytest tests/test_engine_interpolation_security.py tests/test_poll_executor.py -v`

---

### Step 6: Dual-export env vars with deprecation in `ScriptExecutor.start()`

**File:** `src/stepwise/executors.py`

**6a.** Add after line 15 (`from typing import Any, Callable`):

```python
import logging as _logging
```

**6b.** Add before class `ScriptExecutor` (before line 221):

```python
# Env var names that must never be overridden by step inputs.
_SYSTEM_ENV_BLOCKLIST = frozenset({
    "PATH", "HOME", "USER", "SHELL", "LANG", "TERM",
    "LD_PRELOAD", "LD_LIBRARY_PATH",
    "PYTHONPATH", "PYTHONHOME",
    "JOB_ENGINE_INPUTS", "JOB_ENGINE_WORKSPACE",
    "STEPWISE_STEP_IO", "STEPWISE_PROJECT_DIR",
    "STEPWISE_ATTEMPT", "STEPWISE_FLOW_DIR",
})

_bare_env_warned = False
```

**6c.** Replace lines 320–327 in `ScriptExecutor.start()`:

**Before:**
```python
        # Pass inputs as environment variables for convenience
        for k, v in inputs.items():
            if isinstance(v, str):
                env[k] = v
            elif isinstance(v, (dict, list)):
                env[k] = json.dumps(v)
            elif v is not None:
                env[k] = str(v)
```

**After:**
```python
        # Pass inputs as namespaced env vars (STEPWISE_INPUT_ prefix).
        # Bare names set as deprecated alias except for system-critical names.
        global _bare_env_warned
        for k, v in inputs.items():
            if v is None:
                continue
            str_val = v if isinstance(v, str) else (
                json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            )
            env[f"STEPWISE_INPUT_{k}"] = str_val
            if k not in _SYSTEM_ENV_BLOCKLIST:
                env[k] = str_val
                if not _bare_env_warned:
                    _logging.getLogger("stepwise.executor").warning(
                        "Bare input env vars are deprecated. "
                        "Use $STEPWISE_INPUT_<name> instead."
                    )
                    _bare_env_warned = True
```

**Commit:** `security: namespace input env vars under STEPWISE_INPUT_ prefix`

---

### Step 7: Input name identifier validation

**File:** `src/stepwise/yaml_loader.py`

**7a.** Add after the existing `import re` (line 9) — no new import needed, `re` already imported.

**7b.** Add constant after the `_regex_extract` function (after line 45):

```python
_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
```

**7c.** Add validation in `_parse_inputs()` at line 177, inside the `for local_name` loop:

```python
    for local_name, source in inputs_data.items():
        if not _IDENTIFIER_RE.match(local_name):
            raise ValueError(
                f"Step '{step_name}': input name '{local_name}' is not a valid "
                f"identifier (must match [A-Za-z_][A-Za-z0-9_]*)"
            )
        # ... rest of existing parsing unchanged ...
```

**File:** `src/stepwise/models.py`

**7d.** Add matching validation in the duplicate-check loop at line 654–662:

```python
            import re
            _id_re = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
            for ln in local_names:
                if not _id_re.match(ln):
                    errors.append(
                        f"Step '{name}': input name '{ln}' is not a valid identifier"
                    )
                if ln in seen_locals:
                    errors.append(
                        f"Step '{name}': duplicate local_name '{ln}' in inputs"
                    )
                seen_locals.add(ln)
```

**Commit:** `security: validate input names as shell-safe identifiers`

---

### Step 8: Tests for env vars and identifier validation

**File:** `tests/test_executors.py`

**8a.** Add after `TestScriptExecutorAutoDetect` class (after line ~210):

```python
class TestScriptExecutorEnvNamespace:
    """Input env var namespacing and system-name protection."""

    def test_prefixed_env_var_set(self):
        executor = ScriptExecutor(command="printenv STEPWISE_INPUT_url")
        result = executor.start({"url": "https://example.com"}, _ctx())
        assert result.envelope.artifact.get("stdout") == "https://example.com"

    def test_bare_env_var_set_during_deprecation(self):
        executor = ScriptExecutor(command="printenv url")
        result = executor.start({"url": "https://example.com"}, _ctx())
        assert "https://example.com" in result.envelope.artifact.get("stdout", "")

    def test_system_path_not_overridden(self):
        executor = ScriptExecutor(command="which echo")
        result = executor.start({"PATH": "/nonexistent"}, _ctx())
        assert not (result.executor_state or {}).get("failed")

    def test_system_path_available_prefixed(self):
        executor = ScriptExecutor(command="printenv STEPWISE_INPUT_PATH")
        result = executor.start({"PATH": "/custom"}, _ctx())
        assert "/custom" in result.envelope.artifact.get("stdout", "")
```

**File:** `tests/test_yaml_loader.py`

**8b.** Add after `TestExpressionSecurity`:

```python
class TestInputNameValidation:
    def test_valid_identifier_accepted(self):
        wf = load_workflow_string("""
steps:
  s:
    run: echo ok
    outputs: [x]
    inputs:
      my_url: $job.url
""")
        assert wf.steps["s"].inputs[0].local_name == "my_url"

    def test_hyphenated_name_rejected(self):
        with pytest.raises(Exception, match="not a valid identifier"):
            load_workflow_string("""
steps:
  s:
    run: echo ok
    outputs: [x]
    inputs:
      foo-bar: $job.val
""")

    def test_numeric_prefix_rejected(self):
        with pytest.raises(Exception, match="not a valid identifier"):
            load_workflow_string("""
steps:
  s:
    run: echo ok
    outputs: [x]
    inputs:
      123abc: $job.val
""")
```

**Commit:** `test: add env namespace and identifier validation tests`

**Verify:** `uv run pytest tests/test_executors.py::TestScriptExecutorEnvNamespace tests/test_yaml_loader.py::TestInputNameValidation -v`

---

### Step 9: Update documentation and CHANGELOG

**File:** `CHANGELOG.md` — add under `## [Unreleased]` (after line 6):

```markdown
### Security
- Add AST validation to exit rule, `when`, and derived output expressions — blocks
  `__class__`/`__bases__`/`__globals__` attribute traversal
- Shell-escape user input values interpolated into `command` and `check_command`
  config fields via `shlex.quote()` — prevents shell injection through crafted inputs
- Namespace user step inputs under `STEPWISE_INPUT_` prefix in environment variables

### Deprecated
- Bare input environment variables (`$url`, `os.environ["url"]`). Use
  `$STEPWISE_INPUT_url` / `os.environ["STEPWISE_INPUT_url"]` instead. Bare names
  still exported during deprecation (except system-critical names like PATH, HOME).

### Changed
- Input names must be valid identifiers (`[A-Za-z_][A-Za-z0-9_]*`). Use underscores
  instead of hyphens.
- `$var` placeholders in `command`/`check_command` fields are now automatically
  shell-quoted. Do not pre-quote placeholders.
```

**File:** `docs/executors.md` — update lines 19, 27, 37:

- Line 19: `Input values are passed as environment variables with the `STEPWISE_INPUT_` prefix (e.g., `$STEPWISE_INPUT_url`). Bare names (`$url`) are deprecated.`
- Line 27: `All step inputs as `STEPWISE_INPUT_<name>` env vars (strings, or JSON for dicts/lists). Bare `<name>` vars are deprecated.`
- Line 37: `url = os.environ["STEPWISE_INPUT_url"]  # recommended` + `# url = os.environ["url"]  # deprecated`

**Commit:** `docs: update env var documentation for STEPWISE_INPUT_ prefix`

---

## Testing Strategy

### Commands

```bash
# Full suite — must pass before merge
uv run pytest tests/ -v

# Per-step verification
uv run pytest tests/test_yaml_loader.py -v                    # Steps 1–2
uv run pytest tests/test_engine_interpolation_security.py -v   # Steps 3–5
uv run pytest tests/test_poll_executor.py -v                   # Step 4
uv run pytest tests/test_executors.py -v                       # Steps 6, 8
uv run pytest tests/test_branching.py -v                       # when condition regression
uv run pytest tests/test_engine.py::TestExitRules -v           # exit rule regression
uv run pytest tests/test_engine.py::TestExitRuleEvalSafety -v  # eval error regression
uv run pytest tests/test_derived_outputs.py -v                 # derived outputs regression

# Integration smoke
uv run stepwise validate examples/*.flow.yaml
```

### Regression matrix

| Test file | Class / area | What it verifies | Step |
|---|---|---|---|
| `test_yaml_loader.py` | `TestEvaluateExitCondition` (12 tests) | Existing eval patterns still work | 1 |
| `test_yaml_loader.py` | `TestExpressionSecurity` (17 tests) | Dunder blocked; generators, .get(), .strip() work; when→False | 2 |
| `test_yaml_loader.py` | `TestInputNameValidation` (3 tests) | Hyphen/numeric names rejected | 8 |
| `test_yaml_loader.py` | `TestDotDict` (2 tests) | DotDict attribute access unchanged | 1 |
| `test_engine_interpolation_security.py` | `TestInterpolateConfigQuoting` (8 tests) | command/check_command quoted; prompt/model not | 5 |
| `test_poll_executor.py` | `TestPollExecutorStart` (4 tests) | Poll pass-through, existing behavior | 4 |
| `test_poll_executor.py` | `TestPollWatchEngine` (4 tests) | End-to-end poll watch integration | 4 |
| `test_executors.py` | `TestScriptExecutorEnvNamespace` (4 tests) | Prefixed vars set; PATH protected; bare deprecated | 8 |
| `test_executors.py` | `TestScriptExecutor` (6 tests) | Existing script executor behavior | 6 |
| `test_engine.py` | `TestExitRules` (4 tests) | Engine exit rule dispatch | 1 |
| `test_engine.py` | `TestExitRuleEvalSafety` (3 tests) | Eval errors don't crash engine | 1 |
| `test_branching.py` | `TestWhenSpecific` (6+ tests) | when condition semantics | 1 |
| `test_branching.py` | `TestSettlement` (2+ tests) | Unmet when → SKIPPED | 1 |
| `test_derived_outputs.py` | All (6 tests) | Derived outputs with regex_extract | 1 |

### New test count: 32 tests across 3 new classes + 1 new file

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| AST validator rejects an expression used in a real user flow | Low — only rejects dunder attrs, f-strings, lambdas | `when` → `False` with warning; exit/derived → step failure | Run `stepwise validate` on all example flows before merge |
| `shlex.quote()` changes semantics for pre-quoted `$var` in commands | Low–Medium | Command gets double-quoted output | Document convention: `$var` must be in unquoted word position; `Template.safe_substitute` already doesn't respect shell quoting context, so pre-quoted patterns were already fragile |
| System blocklist doesn't cover all dangerous env vars | Low | A rare system var (e.g., `DISPLAY`) could be overridden | Blocklist covers OWASP-relevant vars; can expand later without breaking changes |
| Dual-export deprecation still breaks scripts reading `os.environ["PATH"]` from step inputs | Medium | Step input named PATH won't appear as bare `$PATH` | This is the security fix — document it. Value is still available as `$STEPWISE_INPUT_PATH` |
| Identifier validation rejects flows with hyphenated input names | Low–Medium | YAML parse error | Clear error message says to use underscores; check example flows pre-merge |
| PollExecutor.start() removal breaks direct callers outside engine | Low — only tests call it directly | Test needs updating | Update `test_poll_executor.py` test expectations (Step 4) |

---

## Dependency Order

Steps 1–2 (AST validation) are independent from Steps 3–5 (shell quoting) and Steps 6–8 (env vars). They can be implemented and merged in any order. Step 9 (docs) must come last.

```
Step 1 → Step 2 (verify)
                          ↘
Step 3 → Step 4 → Step 5   → Step 9
                          ↗
Step 6 → Step 7 → Step 8
```
