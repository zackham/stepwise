You are revising a research proposal based on expert feedback.

**Topic:** $topic
**Report path:** $report_path

## Council synthesis (expert feedback)

$synthesis

## Human feedback

$human_feedback

## Instructions

1. Read the current report at `$report_path`

2. Apply the feedback systematically:
   - If human feedback is provided (non-empty, not "None"), prioritize it over council feedback — the human's direction takes precedence
   - Address each actionable recommendation from the council synthesis
   - Strengthen evidence where reviewers flagged gaps
   - Resolve any contradictions or inconsistencies noted
   - Don't blindly accept every suggestion — use judgment about what improves the proposal vs. what would dilute it

3. Update the report file in-place at `$report_path`. Preserve the YAML frontmatter. Keep `status: draft`.

4. Write your outputs as JSON to `output.json`:

```json
{
  "report_content": "The full updated report text (without frontmatter).",
  "result": "Brief summary of what changed in this revision — key improvements, what feedback was addressed, what was deferred and why."
}
```

The `result` field is shown to the human reviewer, so make it a clear, concise changelog of this revision.
