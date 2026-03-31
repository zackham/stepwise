# Using Stepwise with Non-Claude Agents

How to run agent steps with Codex, Gemini, or any ACP-compatible agent — and when to use different agents in the same flow.

---

Stepwise agent steps use [acpx](https://agentclientprotocol.com) — a headless client for the Agent Client Protocol (ACP). Any ACP-compatible agent works: Claude, Codex, Gemini, and others as acpx adds support.

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

If you omit `agent`, it defaults to `claude`. The value maps directly to what acpx accepts.

## Why Use Different Agents?

Different agents have different strengths. Codex is fast and cheap for straightforward code tasks. Gemini handles large context windows well. Claude excels at nuanced reasoning.

More practically: adversarial patterns work better with diverse agents. If one agent writes the code and a different agent reviews it, you get genuinely different perspectives instead of one model agreeing with itself.

## Example: Adversarial Code Review

One agent implements, a different agent reviews. If the review fails, the implementer loops with the feedback.

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
stepwise run adversarial-review --input spec="Add rate limiting to /api/submit" --input project_path=/path/to/repo
```

Codex implements. Claude reviews. If Claude says `needs_work`, Codex gets the feedback and tries again. After 3 rounds without approval, the job escalates for human review.

## Example: Model-Appropriate Task Routing

Use a cheap/fast model for grunt work and a stronger agent for judgment calls:

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

Gemini handles broad information gathering. Claude handles critical analysis.

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

Cost limits, duration limits, `working_dir`, `continue_session`, exit rules — all work identically across agent backends. The `agent` field only controls which agent binary acpx launches.

## Mixing LLM and Agent Steps

`executor: agent` and `executor: llm` are different things. An agent step launches a full agentic session with tool access — reading files, running commands, browsing the web. An LLM step is a single API call that returns structured output. Use the right one:

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

A Gemini Flash LLM call classifies the issue (fast, cheap). A Claude agent session investigates and fixes it (thorough, tool-using).

---

See [Executors](executors.md) for the full executor reference. See [Writing Flows](writing-flows.md) for YAML syntax.
