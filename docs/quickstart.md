# Quickstart

Get from zero to a running workflow in under 5 minutes.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
```

Or install directly: `uv tool install stepwise-run@git+https://github.com/zackham/stepwise.git`

## Try it instantly

Run a real-world code-review flow from the Stepwise registry — no setup, no files to create:

```bash
stepwise run @stepwise:code-review --watch
```

This downloads and runs a multi-step code review workflow, opening a browser with real-time DAG visualization. An agent reviews your code, an external step pauses for your decision, and the flow continues based on your input.

No API keys? Try the welcome tour instead:

```bash
stepwise run @stepwise:welcome --watch
```

## Create your own flow

Create `code-review.flow.yaml`:

```yaml
name: code-review
description: AI-powered code review with human approval

steps:
  gather-context:
    run: |
      git diff main --stat && git log main..HEAD --oneline
    outputs: [diff_summary, commits]

  review:
    executor: agent
    prompt: |
      Review this code change. Identify bugs, style issues, and suggest improvements.
      Diff: $diff_summary
      Commits: $commits
    inputs:
      diff_summary: gather-context.diff_summary
      commits: gather-context.commits
    outputs: [verdict, issues, suggestions]

  decide:
    executor: external
    prompt: |
      Review found these issues: $issues

      Apply fixes or skip?
    outputs: [decision]
    inputs:
      issues: review.issues

  apply-fixes:
    executor: agent
    prompt: "Apply these fixes: $suggestions"
    inputs:
      suggestions: review.suggestions
    outputs: [result]
    after: [decide]
```

Four steps, three executor types:

- **gather-context** — a `script` step (the `run:` shorthand). Runs shell commands, parses JSON from stdout.
- **review** — an `agent` step. An autonomous AI agent reviews the diff with tool access and streaming output.
- **decide** — an `external` step. The job pauses and waits for your input via the web UI or terminal.
- **apply-fixes** — another agent step. Only runs after `decide` completes (via `after`).

Dependencies are implicit from `inputs:` — `review` waits for `gather-context` because it needs `diff_summary` and `commits`. Steps with no data dependencies run in parallel automatically.

## Run it

### Headless (CLI output only)

```bash
stepwise run code-review
```

Prints step-by-step progress to the terminal. Exits when the job completes or fails.

### With live UI

```bash
stepwise run code-review --watch
```

Starts an ephemeral web server and opens the browser. You see the DAG execute in real time — steps light up as they run, agents stream output live, and external steps show an inline input form.

### Generate a report

```bash
stepwise run code-review --report
```

Runs the flow and generates a self-contained HTML report with DAG visualization, step timeline, expandable details for every step, and the YAML source.

## Adding a loop

Make the review iterative — if the human requests changes, loop back to the review step:

```yaml
  decide:
    executor: external
    prompt: |
      Review verdict: $verdict
      Issues: $issues

      Accept, or request changes?
    outputs: [decision]
    inputs:
      verdict: review.verdict
      issues: review.issues
    exits:
      - name: accept
        when: "outputs.decision == 'accept'"
        action: advance

      - name: request-changes
        when: "outputs.decision == 'request-changes'"
        action: loop
        target: review

      - name: give-up
        when: "attempt >= 3"
        action: advance
```

The `exits:` block evaluates rules in order after the step completes. `action: loop` with `target: review` re-runs the review step with fresh context. The `attempt` variable tracks how many times a step has run, so you can set a ceiling.

## Adding an external gate

Any step can be replaced with an external executor to add an approval gate:

```yaml
  approve-deploy:
    executor: external
    prompt: |
      All fixes applied. Deploy to production?

      Changes: $result
    outputs: [approved, note]
    inputs:
      result: apply-fixes.result
```

When the flow reaches this step, it suspends. With `--watch`, you see the prompt in the web UI with typed input fields. Provide your decision and the flow continues. In headless mode, the step pauses at the terminal prompt.

## Call it from your agent

Stepwise flows are callable as tools by AI agents (Claude Code, Codex, etc.) via plain CLI:

```bash
stepwise run code-review --wait --input repo_path="/path/to/repo"
```

```json
{"status": "completed", "job_id": "job-abc123", "outputs": [{"verdict": "approve", "issues": [], "suggestions": []}]}
```

`--wait` prints **only** JSON to stdout — zero logging, zero progress noise. Your agent parses the output and acts on it. Missing an input? The error tells you exactly which `--input` flags to add.

See [Agent Integration](agent-integration.md) for the full guide.

## Stage and batch jobs

For multi-job workflows, stage jobs before running them. This lets you build a batch, wire data between jobs, review the plan, and release everything at once.

```bash
# Create two staged jobs in a group
stepwise job create my-flow --input task="Build API" --group sprint-1
stepwise job create my-flow --input task="Write tests" --group sprint-1

# Wire data: second job uses first job's output
stepwise job create my-flow \
  --input spec=job-<first-id>.result \
  --group sprint-1

# Review staged jobs
stepwise job show --group sprint-1

# Release the batch — engine executes in dependency order
stepwise job run --group sprint-1
```

Jobs auto-start when their dependencies complete. See [Concepts: Job Staging](concepts.md#job-staging) for the full mental model and [CLI: Job Staging](cli.md#job-staging-commands) for all commands.

## What's next

- [Concepts](concepts.md) — understand the full mental model (jobs, steps, runs, currency)
- [Executors](executors.md) — deep dive into all executor types
- [YAML Format](yaml-format.md) — complete reference for flow files
- [Why Stepwise](why-stepwise.md) — the motivation and design philosophy
