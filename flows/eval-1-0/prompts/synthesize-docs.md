# Documentation Quality Synthesis

You are evaluating Stepwise's documentation quality for 1.0 readiness. You receive evidence from the new-user simulation and project metadata. Your job is to assess documentation rubric items and provide a qualitative synthesis.

## Inputs

- `$confusion_points` — JSON array of confusion points from the new-user simulation
- `$new_user_evidence` — JSON array of rubric items from the new-user test
- `$doc_files` — JSON array of documentation file paths
- `$project_path` — path to the Stepwise project

## Instructions

1. Read all files listed in `$doc_files`
2. Read `README.md` at the project root
3. Read `CLAUDE.md` (the LLM instruction file)
4. Review the confusion points from the new-user test

## Rubric Items

Evaluate each item. For each, cite the specific file and line/section where the information is (or should be) found.

### D1: Getting Started Guide
- **Pass**: A clear guide exists that takes a new user from install to running their first flow
- **Fail**: No getting started guide, or the guide is incomplete/outdated
- **Insufficient evidence**: Guide exists but could not verify accuracy

### D5: API Reference
- **Pass**: REST API endpoints are documented (at least /api/jobs, /api/flows, /api/health)
- **Fail**: No API documentation
- **Insufficient evidence**: Partial documentation

### D6: Flow YAML Reference
- **Pass**: Complete reference for flow YAML syntax (steps, inputs, outputs, exits, config, for-each)
- **Fail**: Missing or incomplete YAML reference
- **Insufficient evidence**: Reference exists but major sections missing

### D7: Error Messages Reference
- **Pass**: Common error messages are documented with resolution steps
- **Fail**: No error documentation
- **Insufficient evidence**: Some errors documented

### D8: Architecture Overview
- **Pass**: High-level architecture (engine, executors, store, server) is documented
- **Fail**: No architecture documentation
- **Insufficient evidence**: Partial architecture docs

## Output Format

IMPORTANT: You MUST write your output as a JSON file to `output.json` in the current working directory. Use the Write tool or equivalent to create this file. The stepwise engine reads this file to extract your outputs.

Write the following JSON structure to `output.json`:

```json
{
  "rubric_results": [
    {
      "id": "D1",
      "requirement": "Getting Started Guide exists and is complete",
      "result": "pass",
      "evidence": "docs/getting-started.md covers install through first flow run",
      "file": "docs/getting-started.md"
    }
  ],
  "qualitative_summary": "Documentation covers basics but lacks API reference...",
  "score_pct": 40
}
```

Score: `pass_count / (pass_count + fail_count) * 100` (exclude insufficient_evidence)

## Rules

1. Cite specific files and sections for every rubric item
2. Do not override script-determined pass/fail from the new-user test
3. Focus on what a new user needs to be productive
4. Be specific about what's missing, not vague
