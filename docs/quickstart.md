# Quickstart

Zero to running workflow in 5 minutes.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
```

Or install directly: `uv tool install stepwise-run@git+https://github.com/zackham/stepwise.git`

## Try it instantly

Run the interactive demo — no setup, no API keys, no files to create:

```bash
stepwise run @stepwise:demo --watch
```

This opens a browser with the live DAG viewer. You'll see steps execute in real time, provide input at an external gate, and watch the flow branch based on your decision.

Have API keys configured? Try a real code review:

```bash
stepwise run @stepwise:code-review --watch
```

An agent reviews your code, pauses for your decision, and continues based on your input. Three executor types in one flow.

Want to browse a flow without running it?

```bash
stepwise open @stepwise:demo
```

## Your first flow

No `.stepwise/` directory needed. No configuration. Just a YAML file.

Create `hello.flow.yaml`:

```yaml
name: hello
steps:
  greet:
    run: echo '{"message": "Hello from Stepwise!"}'
    outputs: [message]

  shout:
    run: echo "{\"loud\": \"$(echo $message | tr '[:lower:]' '[:upper:]')\"}"
    inputs:
      message: greet.message
    outputs: [loud]
```

Run it:

```bash
stepwise run hello.flow.yaml
```

Two steps, one dependency. `greet` runs first and outputs JSON to stdout. `shout` runs with `greet`'s output wired in as `$message`. The engine figures out the order from the `inputs:` declaration.

That's the core model: steps produce typed outputs, downstream steps consume them, the engine resolves the DAG.

## A real workflow

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

- **gather-context** — a shell script. Runs commands, outputs JSON to stdout.
- **review** — an agent step. A full agentic session with tools, streaming output.
- **decide** — an external step. The job pauses, waits for your input via the web UI or terminal.
- **apply-fixes** — another agent step. Runs after `decide` completes.

Dependencies are implicit from `inputs:`. Steps with no dependencies run in parallel automatically.

## Running modes

### With the live DAG viewer

```bash
stepwise run code-review --watch
```

Opens the browser. Steps light up as they run, agents stream output live, external steps show an inline input form. This is the fastest way to understand what Stepwise does.

### Headless

```bash
stepwise run code-review
```

Step-by-step progress in the terminal. External steps prompt at the command line. Exits when done.

### As a tool for agents

```bash
stepwise run code-review --wait --input repo_path="/path/to/repo"
```

Pure JSON on stdout. Zero logging, zero progress noise. Your agent parses the output and acts on it. See [Agent Integration](agent-integration.md).

### Generate a report

```bash
stepwise run code-review --report
```

Runs the flow and produces a self-contained HTML report with DAG visualization, step timeline, and expandable details for every step.

## Adding a loop

Make the review iterative — if the human requests changes, loop back:

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

`exits:` evaluates rules in order after the step completes. `action: loop` with `target: review` re-runs the review step. The `attempt` variable tracks iterations, so you can cap it.

## Adding an external gate

Any step can be replaced with an `external` executor to add an approval point:

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

The flow suspends. With `--watch`, you see the prompt in the web UI with typed input fields. Provide your decision and the flow continues. In headless mode, it prompts at the terminal.

## Staging and batching jobs

For multi-job workflows, stage jobs before running them:

```bash
# Create staged jobs in a group
stepwise job create my-flow --input task="Build API" --group sprint-1
stepwise job create my-flow --input task="Write tests" --group sprint-1

# Wire data: second job uses first job's output
stepwise job create my-flow \
  --input spec=job-<first-id>.result \
  --group sprint-1

# Review, then release the batch
stepwise job show --group sprint-1
stepwise job run --group sprint-1
```

Jobs auto-start when their dependencies complete. See [Concepts: Job Staging](concepts.md#job-staging).

## What's next

- [Concepts](concepts.md) — the full mental model: jobs, steps, executors, trust, agents
- [Why Stepwise](why-stepwise.md) — the philosophy: the harness, not the intelligence
- [Writing Flows](writing-flows.md) — all step types, wiring, and control flow
- [Use Cases](use-cases.md) — real patterns: podcast pipelines, research synthesis, deploy gates
- [Flow Reference](flow-reference.md) — complete `.flow.yaml` schema
