# Code Quality Synthesis

You are evaluating Stepwise's code quality for 1.0 readiness. You receive security findings and adversarial probe results as evidence. Your job is to assess code quality rubric items by reading the actual source code.

## Inputs

- `$security_results` — JSON array of security rubric items from test_security.py
- `$security_has_blocker` — boolean indicating if any security blocker was found
- `$adversarial_results` — JSON array of adversarial probe test results
- `$adversarial_critical` — JSON array of critical (blocker) findings from adversarial probe
- `$project_path` — path to the Stepwise project

## Instructions

1. Read key source files in `src/stepwise/`:
   - `engine.py` — core engine logic
   - `models.py` — data model definitions
   - `executors.py` — executor implementations
   - `store.py` — database layer
   - `server.py` — FastAPI server
   - `agent.py` — agent executor
2. Review the security and adversarial evidence provided
3. Evaluate each rubric item by examining actual code

## Rubric Items

For each item, cite specific files and line numbers.

### CQ1: Module Dependency DAG
- **Pass**: No circular imports; dependency direction is `models → executors → engine → server`
- **Fail**: Circular imports detected or wrong-direction imports
- **Check**: Read import statements in each module

### CQ2: Error Handling Consistency
- **Pass**: Errors are caught, logged, and propagated consistently; no bare `except:` clauses
- **Fail**: Bare except clauses, swallowed errors, or inconsistent error handling
- **Check**: Search for `except:` (bare), `except Exception` patterns

### CQ3: Type Safety
- **Pass**: Dataclasses have complete `to_dict()`/`from_dict()` pairs; no raw dict usage where models exist
- **Fail**: Missing serialization methods or raw dicts where models should be used
- **Check**: Verify all dataclasses in models.py have both methods

### CQ4: Test Coverage Quality
- **Pass**: Tests cover happy path, error cases, and edge cases; not just smoke tests
- **Fail**: Tests are shallow or missing error/edge case coverage
- **Check**: Sample 3-5 test files and assess depth

### CQ5: Input Validation at Boundaries
- **Pass**: User inputs (API requests, CLI args, YAML content) are validated before processing
- **Fail**: Raw user input flows into processing without validation
- **Check**: Examine API endpoints in server.py and YAML parsing in yaml_loader.py

### CQ6: Resource Cleanup
- **Pass**: Database connections, file handles, and subprocesses are properly cleaned up
- **Fail**: Resource leaks (connections not closed, temp files not cleaned)
- **Check**: Look for context managers, try/finally blocks, cleanup code

## Output Format

Output a JSON object to stdout:

```json
{
  "rubric_results": [
    {
      "id": "CQ1",
      "requirement": "Module dependency DAG is acyclic and correctly directed",
      "result": "pass",
      "evidence": "Verified imports: models.py imports nothing from engine/executors, executors.py imports only from models",
      "file": "src/stepwise/models.py:1-10"
    }
  ],
  "qualitative_summary": "Code quality is generally strong with clean module boundaries...",
  "score_pct": 83
}
```

Score: `pass_count / (pass_count + fail_count) * 100` (exclude insufficient_evidence)

## Rules

1. Read actual source code — do not guess based on file names
2. Cite specific files and line numbers for every finding
3. The security and adversarial evidence informs your assessment but doesn't override your own reading
4. Focus on 1.0 readiness, not perfection
