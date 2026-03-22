# UX and Developer Experience Synthesis

You are evaluating Stepwise's UX and developer experience for 1.0 readiness. You will read the web UI source code and CLI implementation to assess usability rubric items.

## Inputs

- `$project_path` — path to the Stepwise project
- `$new_user_confusion` — JSON array of confusion points from the new-user simulation
- `$new_user_success` — boolean indicating if the new user successfully built a flow

## Instructions

1. Read key web UI files:
   - `web/src/router.tsx` — route structure
   - `web/src/components/layout/AppLayout.tsx` — navigation and layout
   - `web/src/pages/` — page components
   - `web/src/components/dag/` — DAG visualization components
   - `web/src/components/jobs/` — job detail components
   - `web/src/components/editor/` — flow editor components
2. Read CLI code:
   - `src/stepwise/cli.py` — CLI commands and output formatting
   - `src/stepwise/io.py` — terminal I/O adapters
3. Review the new-user confusion points

## Rubric Items

For each item, cite specific component files.

### UX1: Job Status Visibility
- **Pass**: Job list shows status with clear visual indicators; real-time updates via WebSocket
- **Fail**: No status indicators or stale data
- **Check**: Read JobList and status-colors.ts

### UX2: DAG Visualization
- **Pass**: Step DAG renders correctly with status colors, edges, and step details on click
- **Fail**: No DAG view or broken rendering
- **Check**: Read FlowDagView, StepNode, DagEdges components

### UX3: Human Input UX
- **Pass**: Human steps present clear input forms with field types, validation, and submit
- **Fail**: No human input UI or confusing interface
- **Check**: Read HumanInputPanel, FulfillWatchDialog, TypedField

### UX4: Error Presentation
- **Pass**: Errors shown clearly in UI with context (step name, error message, timestamp)
- **Fail**: Errors hidden, truncated, or shown as raw JSON
- **Check**: Read StepDetailPanel, job event views

### UX5: CLI Help Quality
- **Pass**: Every command has helpful --help text with examples
- **Fail**: Missing or unhelpful help text
- **Check**: Read cli.py argparse setup

### UX6: Flow Editor Usability
- **Pass**: Editor has YAML editing, live validation, step management, and agent chat
- **Fail**: No editor or bare-minimum editing
- **Check**: Read EditorPage, YamlEditor, ChatPanel, StepDefinitionPanel

### UX7: Agent Output Streaming
- **Pass**: Agent output streams in real-time to the UI during execution
- **Fail**: No streaming or batch-only display
- **Check**: Read AgentStreamView, useAgentStream hook

### UX8: Navigation and Information Architecture
- **Pass**: Clear navigation between jobs, editor, settings; breadcrumbs or back links
- **Fail**: Confusing navigation or dead ends
- **Check**: Read AppLayout, router.tsx

## Output Format

IMPORTANT: You MUST write your output as a JSON file to `output.json` in the current working directory. Use the Write tool or equivalent to create this file. The stepwise engine reads this file to extract your outputs.

Write the following JSON structure to `output.json`:

```json
{
  "rubric_results": [
    {
      "id": "UX1",
      "requirement": "Job status visible with real-time updates",
      "result": "pass",
      "evidence": "JobList component uses useStepwiseWebSocket for live updates; status-colors.ts maps all states to distinct colors",
      "file": "web/src/components/jobs/JobList.tsx"
    }
  ],
  "qualitative_summary": "Web UI is feature-rich with DAG visualization and real-time updates...",
  "score_pct": 75
}
```

Score: `pass_count / (pass_count + fail_count) * 100` (exclude insufficient_evidence)

## Rules

1. Read actual component source code — do not guess from file names
2. Cite specific files for every rubric item
3. Consider the new-user confusion points as user research data
4. Evaluate from the perspective of a developer adopting Stepwise
