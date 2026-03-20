# Executors

Executors are what do the actual work inside steps. Each step has exactly one executor. Stepwise ships with four types — covering the spectrum from deterministic scripts to full agentic sessions.

## Script Executor

Runs a shell command. The simplest executor — deterministic, fast, composable.

```yaml
fetch_data:
  run: python3 scripts/fetch.py
  outputs: [data, count]
  inputs:
    url: $job.api_url
```

**How it works:**
- The `run` command executes in the job's workspace directory
- Input values are passed as environment variables (e.g., `$url`)
- The script prints JSON to stdout — that's the output
- Non-zero exit code = step failure

**Environment variables set by Stepwise:**
- `STEPWISE_PROJECT_DIR` — absolute path to the project root
- `STEPWISE_FLOW_DIR` — absolute path to the flow directory (for directory flows)
- `PYTHONPATH` — project root is prepended, so `import` works for project modules
- All step inputs as env vars (strings, or JSON for dicts/lists)

**When to use:** Data fetching, file processing, API calls, build commands, anything deterministic. If you can write it as a script, use a script. It's faster, cheaper, and easier to debug than an LLM call.

**Output format:** The script must print a single JSON object to stdout. The keys must match the step's declared `outputs`:

```python
# scripts/fetch.py
import json, os, requests

url = os.environ["url"]
resp = requests.get(url)
data = resp.json()

print(json.dumps({
    "data": data,
    "count": len(data)
}))
```

## LLM Executor

A single LLM API call via OpenRouter. For tasks that need language understanding but not tool use or iteration.

```yaml
score_content:
  executor: llm
  model: anthropic/claude-sonnet-4
  system: "You are a content quality scorer."
  prompt: |
    Score this content on clarity, accuracy, and completeness.
    Each dimension 0.0-1.0.

    Content: $content

    Return JSON: {"clarity": 0.0, "accuracy": 0.0, "completeness": 0.0, "overall": 0.0}
  temperature: 0.2
  max_tokens: 1024
  outputs: [clarity, accuracy, completeness, overall]
  inputs:
    content: draft.content
```

**How it works:**
- Sends a single request to the model via OpenRouter
- The response is parsed as JSON to extract declared output fields
- Cost and token usage are tracked per step

**Configuration:**

| Field | Required | Description |
|-------|----------|-------------|
| `model` | Yes | Full model ID (e.g., `anthropic/claude-sonnet-4`) or tier alias (e.g., `balanced`) |
| `prompt` | Yes | The user message. Supports `$variable` substitution from inputs. |
| `system` | No | System prompt |
| `temperature` | No | Sampling temperature (default: model-dependent) |
| `max_tokens` | No | Maximum output tokens |

**Model registry** is configured in `~/.config/stepwise/config.json`. Models are referenced by full ID or by tier alias:

```json
{
  "openrouter_api_key": "sk-or-...",
  "default_model": "anthropic/claude-sonnet-4",
  "model_registry": [
    {"id": "anthropic/claude-opus-4", "name": "Opus", "provider": "anthropic", "tier": "strong"},
    {"id": "anthropic/claude-sonnet-4", "name": "Sonnet", "provider": "anthropic", "tier": "balanced"},
    {"id": "google/gemini-2.0-flash-001", "name": "Flash", "provider": "google", "tier": "fast"}
  ]
}
```

Using `model: balanced` in a flow resolves to the first model with `"tier": "balanced"`. Using `model: anthropic/claude-sonnet-4` bypasses the registry and uses the ID directly.

**When to use:** Scoring, classification, summarization, structured extraction, text generation — any task where a single LLM call with the right prompt gets the job done. If the task needs tool use or multiple iterations, use an agent instead.

**Structured output:** The LLM is instructed to return JSON matching the declared outputs. The executor validates that all declared fields are present in the response. If parsing fails, the step fails with a clear error.

## Agent Executor

A full agentic session — an LLM with tools, iterating autonomously until the task is done. For complex tasks that require exploration, tool use, and multi-step reasoning.

```yaml
implement_feature:
  executor: agent
  prompt: |
    Implement the following feature in the codebase:

    Spec: $spec
    Test requirements: $test_plan

    Write the code, run the tests, and fix any failures.
  outputs: [files_changed, test_results]
  inputs:
    spec: plan.spec
    test_plan: plan.test_plan
  limits:
    cost_usd: 5.00
    duration_minutes: 30
    max_iterations: 50
```

**How it works:**
- Spawns an agent subprocess via the ACP (Agent Client Protocol)
- The agent has access to tools (file read/write, shell execution, search)
- The agent runs in the engine's thread pool via `asyncio.to_thread()` — it doesn't block the event loop
- Output is streamed in real-time to the web UI via WebSocket
- On completion, the agent's final output is parsed as the step's result

**Configuration:**

| Field | Required | Description |
|-------|----------|-------------|
| `prompt` | Yes | The agent's objective. Supports `$variable` substitution from inputs. |
| `limits.cost_usd` | No | Maximum cost in USD before the agent is stopped |
| `limits.duration_minutes` | No | Maximum wall-clock time |
| `limits.max_iterations` | No | Maximum tool-use iterations |

**Agent selection:** Use `config: { agent: "claude" }` (or `"codex"`, `"gemini"`) to choose the agent backend. The `model:` shorthand is only for the LLM executor.

**When to use:** Code generation, research with web browsing, complex analysis requiring tool use, any task where the number of steps isn't known in advance. Agents are powerful but expensive — use LLM executor for simpler tasks.

**Cost controls:** Agent steps can burn through significant API credits. The `limits` field provides hard guardrails:

```yaml
limits:
  cost_usd: 2.00          # kill the agent if cost exceeds $2
  duration_minutes: 15     # kill after 15 minutes wall time
  max_iterations: 30       # kill after 30 tool-use rounds
```

When a limit is hit, the step fails with a descriptive error (e.g., `cost_limit_exceeded`). Exit rules can catch this and route to a fallback.

**Streaming:** While an agent runs, its output streams to the web UI in real-time — you can watch it think, use tools, and iterate. After completion, the full trace is available in the step's detail panel and in `--report` output.

**Context chains:** Agent steps can participate in context chains. When an agent step completes, its full conversation transcript is automatically captured and saved. Downstream chain members receive the prior conversations as a `<prior_context>` XML block prepended to their prompt. This gives multi-step agent workflows session continuity while maintaining the pure-function step model. See [YAML Format](yaml-format.md#context-chains) for configuration.

## Human Executor

Suspends the step and waits for human input via the web UI. For decisions that need judgment, approvals, or creative direction.

```yaml
approve_deployment:
  executor: human
  prompt: |
    Review this deployment package:

    Version: $version
    Changes: $changelog
    Test results: $test_summary

    Approve or reject with a reason.
  outputs: [approved, reason]
  inputs:
    version: build.version
    changelog: build.changelog
    test_summary: test.summary
```

**How it works:**
- The step immediately suspends with a watch
- The web UI shows the prompt and the step's inputs
- The user provides the declared outputs as JSON via a "Fulfill" form
- The step completes and the workflow continues

**When to use:** Deployment approvals, content sign-off, budget authorization, creative direction, any decision where you want a human in the loop. Human steps are particularly powerful combined with exit rules:

```yaml
review:
  executor: human
  prompt: "Review and score this draft (0-10)"
  outputs: [score, feedback]
  inputs:
    content: draft.content
  exits:
    - name: ship_it
      when: "outputs.score >= 8"
      action: advance
    - name: revise
      when: "outputs.score < 8 and attempt < 3"
      action: loop
      target: draft
    - name: kill_it
      when: "attempt >= 3"
      action: abandon
```

This creates an iterative human-in-the-loop review cycle: the LLM drafts, the human scores, and if it's not good enough, the LLM revises with the feedback. Up to 3 attempts before abandoning.

## Decorators

Decorators wrap any executor to add cross-cutting behavior:

```yaml
deploy:
  run: scripts/deploy.sh
  outputs: [url, status]
  decorators:
    - type: timeout
      config: { seconds: 600 }
    - type: retry
      config: { max_retries: 2 }
```

| Decorator | What it does |
|-----------|-------------|
| `timeout` | Kills the executor after N seconds |
| `retry` | Re-runs the executor up to N times on failure |

Decorators are applied in order — first listed is outermost. They're transparent to the step's output contract.

**Rule of thumb:** If you'd want to inspect or intervene mid-execution, make it separate steps. If it's an atomic behavior you'd never look at individually (timeout, retry), make it a decorator.

## Choosing an Executor

```
Is the task deterministic?
  → Yes: Script executor

Does it need tool use or iteration?
  → No: LLM executor (single call, structured output)
  → Yes: Agent executor (full agentic session)

Does it need human judgment?
  → Yes: Human executor
```

You can mix all four in a single workflow. A typical pipeline might use script steps for data fetching, an LLM step for scoring, an agent step for implementation, and a human step for final approval. The engine doesn't care — every executor produces a handoff envelope with the declared outputs.
