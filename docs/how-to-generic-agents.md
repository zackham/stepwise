# Using Stepwise with Non-Claude Agents

Stepwise agent steps aren't locked to Claude. The `AgentExecutor` uses [acpx](https://agentclientprotocol.com) — a headless client for the Agent Client Protocol (ACP) — which supports any ACP-compatible agent. Today that includes Claude, Codex, and Gemini, with more backends as acpx adds them.

## Specifying the Agent

Set the `agent` field in the step's config:

```yaml
steps:
  implement:
    executor: agent
    config:
      agent: codex          # or "claude", "gemini"
    prompt: "Implement the feature described in $spec"
    inputs:
      spec: $job.spec
    outputs: [result]
```

If you omit `agent`, it defaults to `claude`. The value maps directly to what acpx accepts — `acpx codex`, `acpx gemini`, etc.

## Why Use Different Agents?

Different agents have different strengths. Codex is fast and cheap for straightforward code tasks. Gemini handles large context windows well. Claude excels at nuanced reasoning and careful analysis. In a multi-step flow, you can use different agents for different steps based on what the step actually needs.

More practically: adversarial patterns work better with diverse agents. If one agent writes the code and a different agent reviews it, you get genuinely different perspectives rather than one model agreeing with itself.

## Example: Adversarial Code Review

A flow where one agent implements a feature and a different agent reviews the implementation. If the review fails, the implementer loops with the feedback.

```yaml
name: adversarial-review
steps:
  implement:
    executor: agent
    config:
      agent: codex
    working_dir: $project_path
    prompt: |
      Implement this feature: $spec
      $feedback
    inputs:
      spec: $job.spec
      project_path: $job.project_path
      feedback:
        from: review.feedback
        optional: true          # None on first pass
    outputs: [result]

  review:
    executor: agent
    config:
      agent: claude
    working_dir: $project_path
    prompt: |
      Review the implementation for correctness, edge cases, and style.
      Feature spec: $spec
      Provide structured feedback. Set status to "approved" or "needs_work".
    inputs:
      spec: $job.spec
      project_path: $job.project_path
      result: implement.result
    outputs: [status, feedback]
    exits:
      - name: approved
        when: "outputs.status == 'approved'"
        action: advance
      - name: stuck
        when: "attempt >= 3"
        action: escalate        # human decides after 3 rounds
      - name: needs-work
        when: "outputs.status == 'needs_work'"
        action: loop
        target: implement
```

Run it:

```bash
stepwise run adversarial-review --input spec="Add rate limiting to the /api/submit endpoint" --input project_path=/path/to/repo
```

Codex implements. Claude reviews. If Claude says `needs_work`, Codex gets the feedback and tries again. After 3 rounds without approval, the job escalates for human review.

## Example: Model-Appropriate Task Routing

Use a cheap/fast agent for grunt work and a stronger agent for judgment calls:

```yaml
name: research-and-analyze
steps:
  gather:
    executor: agent
    config:
      agent: gemini
    prompt: |
      Search for and summarize recent developments in $topic.
      Collect at least 5 distinct sources.
    inputs:
      topic: $job.topic
    outputs: [findings]

  analyze:
    executor: agent
    config:
      agent: claude
    prompt: |
      Analyze these research findings critically.
      Identify contradictions, gaps, and the strongest conclusions.
      Findings: $findings
    inputs:
      findings: gather.findings
    outputs: [analysis]
```

Gemini handles the broad information gathering. Claude handles the critical analysis that requires more nuanced reasoning.

## Configuration and Limits

All agent configuration works the same regardless of backend:

```yaml
steps:
  work:
    executor: agent
    config:
      agent: codex
    prompt: "Do the thing"
    limits:
      cost_usd: 1.50             # kill if cost exceeds $1.50
      duration_minutes: 10        # kill after 10 minutes
    working_dir: /path/to/project # agent CWD
    continue_session: true        # reuse session across loop iterations
    outputs: [result]
```

Cost limits, duration limits, `working_dir`, `continue_session`, `emit_flow`, exit rules — all of these work identically across agent backends. The `agent` field only controls which agent binary acpx launches.

## What ACP Compatibility Means

ACP (Agent Client Protocol) standardizes how clients talk to agents. When stepwise spawns an agent step, it:

1. Calls `acpx {agent} prompt --session {name} --working-dir {path}` with the resolved prompt
2. Monitors the process for completion
3. Captures the agent's output as the step artifact

Any agent that acpx supports works with stepwise. As new backends are added to acpx, they're automatically available in your flows — just change the `agent` field.

## Mixing LLM and Agent Steps

Remember that `executor: agent` and `executor: llm` are different things. An agent step launches a full agentic session with tool access — the agent can read files, run commands, browse the web. An LLM step is a single API call that returns structured output. Use the right one:

```yaml
steps:
  quick-classify:
    executor: llm
    config:
      model: google/gemini-2.5-flash    # cheap, fast
      prompt: "Classify this issue: $description"
    inputs:
      description: $job.description
    outputs: [category, priority]

  deep-investigate:
    executor: agent
    config:
      agent: claude                      # full agent session
    working_dir: $project_path
    prompt: "Investigate and fix this $category issue: $description"
    inputs:
      category: quick-classify.category
      description: $job.description
      project_path: $job.project_path
    outputs: [result]
```

A Gemini Flash LLM call classifies the issue (fast, cheap). A Claude agent session investigates and fixes it (thorough, tool-using). Each step uses the right tool for the job.
