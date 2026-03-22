# New User Simulation — First Flow Experience

You are simulating a developer who has never used Stepwise before. Your goal is to build a simple two-step workflow (fetch data → summarize it) using ONLY the documentation available in the project.

## Constraints

- **ONLY read files in `docs/` and `README.md`** for guidance. Do not read source code to figure out how to write flows.
- You may read example flows in `flows/` and `examples/` to learn patterns.
- You may use `uv run stepwise --help` and subcommand help.
- You are at `$project_path`.

## Task

Build a flow called `my-first-flow` with two steps:

1. **fetch** — a script step that outputs `{"data": "hello world"}`
2. **summarize** — a script step that takes `data` from fetch and outputs `{"summary": "processed: <data>"}`

### Steps to follow

1. Read `README.md` and any files in `docs/` to understand how to create a flow
2. Look at example flows for patterns
3. Write your flow to a temporary file
4. Validate it with `uv run stepwise validate`
5. If validation fails, try to fix it using only documentation
6. Record every point of confusion

## Output Format

IMPORTANT: You MUST write your output as a JSON file to `output.json` in the current working directory. Use the Write tool or equivalent to create this file. The stepwise engine reads this file to extract your outputs.

Write the following JSON structure to `output.json`:

```json
{
  "flow_yaml": "name: my-first-flow\nsteps:\n  ...",
  "success": true,
  "confusion_points": [
    {
      "description": "Couldn't find docs on input syntax",
      "severity": "major",
      "resolved": true,
      "how_resolved": "Found example in flows/welcome/FLOW.yaml"
    }
  ],
  "docs_consulted": ["README.md", "docs/getting-started.md"],
  "evidence": [
    {
      "id": "NU1",
      "requirement": "README explains how to create a flow",
      "result": "pass",
      "evidence": "README.md section 'Quick Start' covers flow creation"
    }
  ],
  "score_pct": 60
}
```

### Evidence rubric items (NU1-NU6)

- **NU1**: README or docs explain how to create a flow file
- **NU2**: Input binding syntax (`step.field`, `$job.field`) is documented
- **NU3**: Output declaration syntax is documented
- **NU4**: `stepwise validate` is documented as a way to check flows
- **NU5**: At least one complete example flow is available to reference
- **NU6**: The flow you built validates and would run correctly

Score: `pass_count / (pass_count + fail_count) * 100` (exclude insufficient_evidence)

## Rules

1. Be honest about confusion. The value is in identifying gaps.
2. Do not use knowledge of Stepwise internals — pretend you only know what the docs tell you.
3. Clean up temporary files when done.
