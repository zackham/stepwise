# M3: LLM Executor Implementation Plan

## Design Principles (from bio-zack)

1. **Stepwise knows nothing about Vita.** Own API key, own config. No references to external systems.
2. **No retries.** If the LLM fails to produce valid output, the step fails. Period.
3. **No tool calls beyond structured output.** Tool use only for enforcing structured output format, not for data fetching or agent behavior.
4. **Don't hide models.** Maintain a model registry with real model IDs. Tiers (fast/balanced/strong) are workflow-level aliases that map to concrete models. The UI always shows what model is actually being used.
5. **Fully testable.** Mock infrastructure so the entire LLM surface area can be tested without real API calls.

---

## Deliverables

### 1. Stepwise Configuration System

Currently all config is env vars. M3 adds a persistent config file for things that change at runtime (model registry, API keys).

**Config file:** `~/.config/stepwise/config.json`

```json
{
  "openrouter_api_key": "sk-or-...",
  "model_registry": {
    "anthropic/claude-sonnet-4-20250514": {
      "name": "Claude Sonnet 4",
      "provider": "anthropic",
      "tier": "balanced"
    },
    "google/gemini-flash-1.5": {
      "name": "Gemini Flash 1.5",
      "provider": "google",
      "tier": "fast"
    },
    "anthropic/claude-opus-4-20250514": {
      "name": "Claude Opus 4",
      "provider": "anthropic",
      "tier": "strong"
    }
  },
  "default_model": "anthropic/claude-sonnet-4-20250514"
}
```

**Tier aliases in workflows:** When a workflow says `model: fast`, the engine resolves to the first model with that tier in the registry. But the step run always records the concrete model ID. The UI always shows the real model.

**API endpoints:**
- `GET /api/config/models` — list registered models with tiers
- `PUT /api/config/models` — update model registry
- `GET /api/config` — full config (redacted API key)
- `PUT /api/config` — update config

**Web UI:** Settings page at `/settings` with:
- Model registry table (add/remove/edit models, assign tiers)
- API key input (masked)
- Default model selector

**Files:**
- `src/stepwise/config.py` — config loading, model resolution, persistence
- `web/src/pages/SettingsPage.tsx` — settings UI
- `web/src/hooks/useConfig.ts` — config API hooks

### 2. LLMExecutor

Implements the Executor ABC. Single prompt → structured response.

**Config in ExecutorRef:**
```python
ExecutorRef("llm", {
    "model": "anthropic/claude-sonnet-4-20250514",  # or tier: "fast"
    "prompt": "Classify this issue: ${title}\n${body}",
    "system": "You are a classifier.",  # optional
    "temperature": 0.0,  # optional, default 0
    "max_tokens": 2048,  # optional
})
```

**Execution flow:**
1. Resolve model (tier alias → concrete ID from registry)
2. Render prompt template (`string.Template` with step inputs as namespace)
3. Build messages array (system + user)
4. Build output schema from step's declared `outputs` field names
5. Call OpenRouter `/chat/completions` with `tools` parameter for structured output
6. Parse tool_call arguments as the step artifact
7. If no tool_call, try parsing content as JSON
8. If single output field and no JSON, wrap raw content
9. Validate all declared output fields are present
10. Return `ExecutorResult(type="data", envelope=HandoffEnvelope(...))`

**No retries.** Step fails if output can't be parsed or fields are missing.

**Cost tracking:** Extract from OpenRouter response headers/body → store in `executor_meta`:
```python
{
    "model": "anthropic/claude-sonnet-4-20250514",
    "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
    "cost_usd": 0.0054,
    "latency_ms": 1850
}
```

**Files:**
- `src/stepwise/executors/llm.py` — LLMExecutor class (~150 lines)
- `src/stepwise/executors/openrouter.py` — OpenRouter client (~80 lines)

### 3. Mock Infrastructure

The existing `MockLLMExecutor` simulates LLM behavior for testing. For M3, we need a way to mock the OpenRouter HTTP layer so LLMExecutor itself can be tested.

**Approach:** Dependency injection. LLMExecutor accepts an optional `llm_client` parameter. In production, this is the real OpenRouter client. In tests, it's a mock that returns canned responses.

```python
class LLMClient(Protocol):
    def chat_completion(self, model: str, messages: list, tools: list | None,
                        temperature: float, max_tokens: int) -> LLMResponse: ...

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict] | None
    usage: dict  # {prompt_tokens, completion_tokens}
    model: str
    cost_usd: float | None
    latency_ms: int
```

**MockLLMClient:** Returns configurable responses per step, supports failure simulation.

**Files:**
- `src/stepwise/executors/llm_client.py` — protocol + response types
- `tests/mock_llm_client.py` — mock implementation

### 4. YAML Support

Extend `yaml_loader.py` to parse LLM executor config:

```yaml
steps:
  classify:
    executor:
      type: llm
      config:
        model: fast
        system: You are a precise classifier.
        prompt: |
          Classify this issue into exactly one category.

          Title: ${title}
          Body: ${body}

          Categories: bug, feature, question, chore
    inputs:
      title: $job.title
      body: $job.body
    outputs: [category, confidence]

  summarize:
    executor:
      type: llm
      config:
        model: balanced
        prompt: |
          Summarize the following analysis in 2-3 sentences.

          Category: ${category}
          Original: ${body}
    inputs:
      category: classify.category
      body: $job.body
    outputs: [summary]
    sequencing: [classify]
```

### 5. Web UI Additions

**StepDetailPanel:** When viewing an LLM step run, show:
- Model used (concrete ID)
- Prompt sent (rendered, not template)
- Response received
- Token usage and cost
- Latency

Store the rendered prompt and raw response in `executor_meta` for observability.

**Settings page:** Model registry management (described above).

### 6. Demo Jobs

Create seed data / test workflows that exercise the LLM executor:

1. **Simple classification** — single LLM step, one input, categorizes into enum
2. **Two-step pipeline** — LLM classifies → LLM summarizes based on classification
3. **Iterative refinement** — LLM drafts → LLM scores → exit rule loops back or advances
4. **Mixed executor** — script prepares data → LLM analyzes → script formats output
5. **Sub-job delegation** — classify complexity → spawn appropriate sub-job (simple vs complex workflow)

These should be runnable with MockLLMClient for tests, and with real OpenRouter for live demos (if API key configured).

---

## Implementation Order

### Phase 1: Foundation
1. `config.py` — config file loading, model registry, tier resolution
2. `llm_client.py` — LLMClient protocol, LLMResponse dataclass
3. `openrouter.py` — real OpenRouter client implementing LLMClient
4. `llm.py` — LLMExecutor implementing Executor ABC
5. Register "llm" in server.py lifespan
6. Config API endpoints (GET/PUT models, config)

### Phase 2: Testing
7. `mock_llm_client.py` — mock client for testing
8. `test_llm_executor.py` — unit tests for LLMExecutor (parsing, validation, error handling)
9. `test_config.py` — config loading, model resolution
10. `test_llm_integration.py` — end-to-end with mock client (create job → tick → verify outputs)

### Phase 3: YAML & UI
11. Extend `yaml_loader.py` for llm executor type
12. `test_yaml_llm.py` — YAML parsing tests
13. Settings page (model registry UI)
14. StepDetailPanel LLM info section
15. Config API hooks

### Phase 4: Demo
16. Create demo workflows (YAML + seed scripts)
17. Seed stepwise.db with completed demo jobs showing LLM results
18. Verify everything renders correctly in the UI

---

## Architecture Notes

### Output Schema Generation

Step's `outputs` list (e.g., `["category", "confidence"]`) becomes a tool schema:

```python
def _build_output_tool(self, output_fields: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "step_output",
            "description": "Provide the step output fields",
            "parameters": {
                "type": "object",
                "properties": {
                    field: {"type": "string"} for field in output_fields
                },
                "required": output_fields,
            }
        }
    }
```

All fields are typed as `string` in M3 (LLMs output strings). The step's exit rules and downstream steps handle type interpretation. This keeps M3 simple — no schema type system.

### Prompt Rendering

`string.Template` with `safe_substitute` (doesn't error on missing vars, just leaves `${var}` in place). Step inputs are the namespace. `injected_context` from ExecutionContext is appended as an additional section.

```python
def _render_prompt(self, template_str: str, inputs: dict, context: ExecutionContext) -> str:
    from string import Template
    prompt = Template(template_str).safe_substitute(inputs)
    if context.injected_context:
        prompt += "\n\nAdditional context:\n" + "\n".join(context.injected_context)
    return prompt
```

### Error Handling

- **API error (network, auth, rate limit):** Step fails with error in sidecar. No retry.
- **Parse error (no tool_call, invalid JSON):** Step fails with raw response in executor_meta for debugging.
- **Missing output fields:** Step fails with validation error listing missing fields.
- **Timeout:** Handled by existing TimeoutDecorator if configured.

All failures are visible in the UI via StepDetailPanel — the raw prompt, response, and error are stored in executor_meta.
