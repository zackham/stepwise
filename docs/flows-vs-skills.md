# Flows vs Skills

When to use a Stepwise flow versus a Claude Code skill, and how they work together.

---

## Skills: Knowledge Injection

A skill is a `SKILL.md` file in `.claude/skills/`. It injects domain knowledge, conventions, and instructions into a single agent session. No state, no scheduling, no multi-step coordination.

**Examples:**
- Project coding conventions (naming, testing patterns, architecture rules)
- API reference material for a specific SDK
- PR review standards and common pitfalls
- Database migration procedures

A skill is the right tool when the answer to "what do I need?" is **context** — the agent does the work in one shot with that context loaded.

## Flows: Orchestrated Execution

A flow is a `.flow.yaml` that defines a multi-step workflow with typed inputs, outputs, dependencies, and execution rules. The engine handles scheduling, state, retries, and coordination between LLMs, scripts, agents, and humans.

**Examples:**
- Plan, implement, test, review, merge (with loops on test failure)
- Research across sub-areas in parallel, then synthesize
- Deploy to staging, wait for human approval, deploy to production
- Fetch data with retries, transform with an LLM, validate with a script

A flow is the right tool when the answer to "what do I need?" is **coordination** — multiple steps, quality gates, error handling, or work spanning minutes to hours.

## Decision Matrix

| You need... | Use a... | Why |
|---|---|---|
| Domain knowledge for a task | Skill | One-shot context injection |
| Multi-step coordination | Flow | Steps need scheduling, dependencies, state |
| Project conventions during coding | Skill | Agent applies them inline |
| Human approval gates | Flow | `executor: external` pauses for input |
| Retry logic on failure | Flow | Exit rules + loop actions |
| A reusable prompt template | Skill | Context, not orchestration |
| Parallel LLM calls with fan-out | Flow | `for_each` runs instances concurrently |
| Coding style guide | Skill | Reference material, not a process |
| "Do X, then if Y do Z, else do W" | Flow | Conditional branching via `when` |

**The one-sentence test:** If the task completes in a single agent turn with the right context, it's a skill. If it requires multiple steps, decisions between steps, or coordination across time, it's a flow.

## They Work Together

A flow can benefit from skills. When an agent step runs with `working_dir` pointed at a project, it loads that project's `CLAUDE.md` and skills from `.claude/skills/`. The flow provides orchestration; the skills provide domain knowledge the agent needs at each step.

```yaml
steps:
  implement:
    executor: agent
    working_dir: /path/to/project    # agent loads CLAUDE.md + skills from here
    prompt: "Implement the feature described in $spec"
    inputs:
      spec: plan.spec
    outputs: [result]
```

The `implement` step gets both: flow-level orchestration (runs after `plan`, feeds downstream steps, retries on test failure) and skill-level knowledge (project conventions, API patterns, style guides from the working directory).

## Common Mistakes

**Over-engineering with a flow:** "I want Claude to follow our coding conventions." You don't need a pipeline for this — write a skill. The agent applies conventions naturally during code generation.

**Under-powering with a skill:** "I want to plan, implement, test, fix failures, and get sign-off." A skill can describe this process but can't enforce it. No retry on test failure, no human gate, no persistent state if the session ends. You need a flow.

**The hybrid case:** "I want an agent to implement features following our conventions, with test validation." The flow handles the plan-implement-test-fix loop. A skill teaches the agent your conventions. Both tools doing what they're best at.

---

See [Writing Flows](writing-flows.md) for flow YAML syntax. See [How to Create a Skill](how-to-skills.md) for skill authoring.
