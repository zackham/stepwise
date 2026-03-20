# Evaluation Report Generation

You are generating a comprehensive evaluation report for Stepwise 1.0 readiness. You receive the complete scorecard, gate results, and evidence from all evaluation phases.

## Inputs

- `$scorecard` — JSON object with all dimension scores
- `$gates` — JSON object with hard gate pass/fail status
- `$recommendation` — "GO" or "NO-GO"
- `$recommendation_reason` — text explaining the recommendation
- `$remediation` — JSON array of prioritized remediation items
- `$overall_avg` — numeric overall score percentage
- `$security_veto` — boolean, true if security blocker found
- `$insufficient_evidence_warnings` — JSON array of dimensions with >30% insufficient evidence
- `$adversarial_results` — JSON array of adversarial probe results
- `$adversarial_critical` — JSON array of critical findings
- `$new_user_evidence` — JSON array of new-user rubric items
- `$new_user_confusion` — JSON array of new-user confusion points
- `$version` — current Stepwise version
- `$python_loc` — Python lines of code
- `$typescript_loc` — TypeScript lines of code

## Report Structure

Generate a Markdown report with these sections:

### 1. Executive Summary (2-3 paragraphs)
- Version evaluated, overall score, recommendation
- Key strengths (top 3 scoring dimensions)
- Key gaps (bottom 3 scoring dimensions or failed gates)

### 2. Hard Gate Results
- Table: Gate | Dimension | Score | Threshold | Status
- Detail any security blocker findings
- Note if security veto was triggered

### 3. Full Scorecard
- Table: Dimension | Pass | Fail | Insufficient | Score% | Status
- Group by: Hard Gates, Non-Gate Dimensions, Synthesis Dimensions

### 4. Adversarial Probe Findings
- Summary: X/14 tests handled gracefully
- List any critical (blocker) findings with details
- Note patterns (e.g., "injection handling is strong but resource exhaustion needs work")

### 5. New User Experience
- Did the simulated user succeed?
- List confusion points by severity
- Documentation gaps identified

### 6. Remediation Roadmap
- P0: Failed hard gates (must fix before 1.0)
- P1: Dimensions below 60% (should fix)
- P2: Dimensions below 80% (nice to fix)
- For each, provide specific actionable items

### 7. Insufficient Evidence
- List dimensions where >30% of rubric items were insufficient
- Explain what would be needed to fully assess

### 8. Path to 1.0
- Clear statement: what needs to happen for 1.0 readiness
- Estimated effort for P0 items
- Suggested order of attack

## Output Format

Output a JSON object to stdout:

```json
{
  "report_content": "# Stepwise 1.0 Readiness Evaluation\n\n## Executive Summary\n..."
}
```

The `report_content` field should contain the full Markdown report as a string.

## Rules

1. Be factual — cite scores and evidence, don't editorialize
2. The recommendation has already been determined by the aggregation script — do not change it
3. Focus on actionable findings, not general commentary
4. Keep the report concise but complete (target: 1500-2500 words)
