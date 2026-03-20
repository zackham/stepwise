You are finalizing a research proposal for publication.

**Topic:** $topic
**Title:** $title
**Report path:** $report_path

## Instructions

1. Read the current report at `$report_path`

2. Do a cleanup pass:
   - Ensure consistent formatting, tone, and structure throughout
   - Fix any rough, incomplete, or contradictory sections
   - Verify all references and links are present and properly formatted
   - Ensure the executive summary accurately reflects the final content
   - Check that the YAML frontmatter is complete (title, date, tags, project)
   - Update `status: draft` to `status: published` in the frontmatter

3. Save the final version back to `$report_path`

4. Write your outputs as JSON to `output.json`:

```json
{
  "result": "Final report: {title} — saved to {report_path}"
}
```
