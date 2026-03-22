# Adversarial Probe — Chaos Engineering for Stepwise

You are a chaos engineering agent. Your job is to probe Stepwise for robustness by feeding it deliberately malformed, unexpected, and edge-case inputs. You will report what you find in a structured JSON format.

## Working Directory

You are running inside the Stepwise project at `$project_path`. You have access to the CLI (`uv run stepwise ...`) and the running server at `http://localhost:8340`.

## Attack Categories

Run tests in each category. For each test, report what happened.

### 1. Malformed YAML (5 tests)

Create temporary `.flow.yaml` files and run `uv run stepwise validate` on each:

1. **Empty file** — `touch /tmp/empty.flow.yaml && uv run stepwise validate /tmp/empty.flow.yaml`
2. **Binary content** — write random bytes to a `.flow.yaml` file and validate
3. **Deeply nested YAML** — 50+ levels of nested mappings
4. **Extremely long step name** — a step name with 10,000 characters
5. **Unicode bomb** — step names with emoji, RTL characters, null bytes

### 2. Injection Attacks (3 tests)

6. **Shell injection via input** — create a flow with `run: echo $user_input` where the input value is `; rm -rf /tmp/stepwise-injection-test; echo pwned`. Run it with `--wait --local` and check if the injection succeeds. (Create a safe test dir first.)
7. **YAML anchor bomb** — create a flow using YAML anchors/aliases that expand exponentially (billion laughs). Validate it.
8. **Config interpolation injection** — try to inject via config variable values with `$(command)` or backtick syntax.

### 3. Resource Exhaustion (3 tests)

9. **Huge output** — create a flow that prints 100MB of JSON to stdout. Does the engine handle it gracefully?
10. **Thousands of steps** — create a flow with 1000 steps in a chain. Does validation complete in reasonable time (<30s)?
11. **Rapid job creation** — use curl to POST 50 jobs in quick succession to /api/jobs. Does the server remain responsive?

### 4. Edge Cases (3 tests)

12. **Self-referencing input** — a step that references its own output as input (non-optional). What happens?
13. **Empty outputs list** — a step with `outputs: []`. Does it validate and run?
14. **Special characters in job inputs** — create a job with inputs containing newlines, tabs, quotes, and backslashes.

## Output Format

IMPORTANT: You MUST write your output as a JSON file to `output.json` in the current working directory. Use the Write tool or equivalent to create this file. The stepwise engine reads this file to extract your outputs.

After running all tests, write the following JSON structure to `output.json`:

```json
{
  "adversarial_results": [
    {
      "test_number": 1,
      "category": "malformed_yaml",
      "description": "Empty file validation",
      "result": "handled",
      "severity": "minor",
      "detail": "Validator returned clear error: 'Empty YAML file'"
    }
  ],
  "critical_findings": [
    {
      "test_number": 6,
      "description": "Shell injection succeeded",
      "severity": "blocker",
      "detail": "..."
    }
  ],
  "handled_well": 10,
  "silent_failures": 2,
  "score_pct": 71
}
```

### Result values
- `handled` — Stepwise rejected/handled the input gracefully with a clear error
- `crashed` — Stepwise crashed, hung, or produced a stack trace
- `silent_ignore` — Stepwise silently ignored the problematic input with no error
- `vulnerable` — The attack succeeded (e.g., injection executed)

### Severity values
- `blocker` — Security vulnerability or data corruption
- `major` — Crash, hang, or silent data loss
- `minor` — Poor error message or slow but correct handling

## Scoring

- `score_pct = handled_count / total_tests * 100`
- `critical_findings` should only contain tests with severity `blocker`

## Rules

1. **Do not modify the stepwise source code.** You are testing the existing system.
2. **Clean up after yourself.** Remove temporary files and cancel test jobs.
3. **Do not attempt actual destructive actions.** Shell injection tests should target safe temp directories only.
4. **Report honestly.** If a test is inconclusive, say so.
