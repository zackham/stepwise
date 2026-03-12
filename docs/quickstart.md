# Quickstart

Get from zero to a running workflow in under 5 minutes.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
```

Or install directly: `uv tool install stepwise` / `pipx install stepwise` / `pip install stepwise`.

## Initialize a Project

```bash
mkdir my-project && cd my-project
stepwise init
```

This creates a `.stepwise/` directory for job storage (SQLite DB, workspace, templates).

## Your First Flow

Create `hello.flow.yaml`:

```yaml
name: hello-world
description: Fetch a quote, score it, decide if it's good enough

steps:
  fetch:
    run: |
      python3 -c "
      import json, random
      quotes = [
        'The best way to predict the future is to invent it.',
        'Talk is cheap. Show me the code.',
        'Simplicity is the ultimate sophistication.',
      ]
      q = random.choice(quotes)
      print(json.dumps({'quote': q, 'length': len(q)}))
      "
    outputs: [quote, length]

  score:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Rate this quote for insight and memorability.
      Quote: "$quote"
      Return JSON: {"score": 0.0-1.0, "reasoning": "why"}
    temperature: 0.3
    max_tokens: 256
    outputs: [score, reasoning]
    inputs:
      quote: fetch.quote

  publish:
    run: |
      python3 -c "
      import json, os
      print(json.dumps({
        'message': f'Published quote (score: {os.environ[\"score\"]})',
        'published': True
      }))
      "
    outputs: [message, published]
    inputs:
      score: score.score
    sequencing: [score]
```

Three steps: a script fetches a random quote, an LLM scores it, another script "publishes" it.

## Run It

### Headless (CLI output only)

```bash
stepwise run hello.flow.yaml
```

Prints step-by-step progress to the terminal. Exits when the job completes.

### With Live UI

```bash
stepwise run hello.flow.yaml --watch
```

Starts an ephemeral web server and opens the browser. You see the DAG execute in real-time — steps light up as they run, and you can click into any step to inspect inputs, outputs, and timing.

### Generate a Report

```bash
stepwise run hello.flow.yaml --report
```

Runs the flow and generates `hello-report.html` — a self-contained HTML document with:
- SVG DAG visualization
- Step timeline with durations
- Expandable details for every step (inputs, outputs, errors, cost)
- YAML source appendix

Open it in any browser. No server needed.

## Adding a Loop

Make the workflow iterative — if the score is too low, fetch a new quote and try again:

```yaml
name: best-quote
description: Keep fetching quotes until we find a great one

steps:
  fetch:
    run: |
      python3 -c "
      import json, random
      quotes = [
        'The best way to predict the future is to invent it.',
        'Talk is cheap. Show me the code.',
        'Move fast and break things.',
        'Simplicity is the ultimate sophistication.',
        'The only way to do great work is to love what you do.',
      ]
      q = random.choice(quotes)
      print(json.dumps({'quote': q, 'length': len(q)}))
      "
    outputs: [quote, length]

  score:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Rate this quote for insight and memorability (0.0-1.0).
      Be harsh — only truly great quotes score above 0.8.
      Quote: "$quote"
      Return JSON: {"score": 0.0, "reasoning": "why"}
    temperature: 0.3
    max_tokens: 256
    outputs: [score, reasoning]
    inputs:
      quote: fetch.quote

    exits:
      - name: great
        when: "outputs.score >= 0.8"
        action: advance

      - name: try_again
        when: "outputs.score < 0.8 and attempt < 5"
        action: loop
        target: fetch

      - name: settle
        when: "attempt >= 5"
        action: advance

  publish:
    run: |
      python3 -c "
      import json, os
      print(json.dumps({
        'message': f'Published: {os.environ[\"quote\"]} (score: {os.environ[\"score\"]})',
        'published': True
      }))
      "
    outputs: [message, published]
    inputs:
      quote: fetch.quote
      score: score.score
```

Now the flow loops: fetch → score → (if score < 0.8, loop back to fetch). Up to 5 attempts. The report shows every attempt with its score.

## Adding a Human Gate

Replace the automatic advance with a human approval step:

```yaml
  review:
    executor: human
    prompt: |
      Best quote found:

      "$quote"

      Score: $score — $reasoning

      Approve for publishing?
    outputs: [approved, note]
    inputs:
      quote: fetch.quote
      score: score.score
      reasoning: score.reasoning

  publish:
    run: |
      python3 -c "
      import json, os
      print(json.dumps({'url': '/quotes/' + os.environ['quote'][:20].replace(' ', '-'), 'published': True}))
      "
    outputs: [url, published]
    inputs:
      quote: fetch.quote
    sequencing: [review]
```

When the flow reaches the `review` step, it pauses. Open the web UI (`stepwise run --watch`), and you'll see the prompt with the quote and score. Provide your decision, and the flow continues.

## What's Next

- [Concepts](concepts.md) — understand the full mental model
- [Executors](executors.md) — deep dive into the four executor types
- [YAML Format](yaml-format.md) — complete reference for flow files
- [Why Stepwise](why-stepwise.md) — the motivation and design philosophy
