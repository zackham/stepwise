# Flows vs Skills — When to Use Which

Stepwise flows and Claude Code skills solve different problems. They overlap at the edges, but if you pick the wrong one for a given task, you'll either over-engineer something simple or under-power something complex.

## What's a Skill?

A skill is a `SKILL.md` file dropped into `.claude/skills/`. It injects knowledge, instructions, and context into a single Claude Code session. When a user invokes a skill (via slash command or auto-trigger), Claude reads the file and gains that capability for the duration of the conversation.

Skills are **single-turn knowledge injection**. They don't have state, they don't schedule work, and they don't coordinate multiple steps. They make Claude better at one thing, right now.

**Examples of skills:**

- "Configure my Hyprland window manager" — loads keybinding conventions, config file locations, theme variables
- "Write code using the Anthropic SDK" — loads API patterns, model IDs, SDK idioms
- "Review this PR" — loads team review standards, common pitfalls, style preferences
- "Create a new database migration" — loads schema conventions, naming rules, migration toolchain

A skill is the right tool when the answer to "what do I need?" is *context* — domain knowledge, project conventions, reference material. The LLM does the work in one shot with that context loaded.

## What's a Flow?

A flow is a `.flow.yaml` file that defines a multi-step workflow with typed inputs, outputs, dependencies, and execution rules. Stepwise's engine runs it — scheduling steps, managing state, handling retries, and coordinating between LLMs, scripts, agents, and humans.

Flows are **orchestrated execution**. They have persistent state, branch on conditions, loop on failure, pause for human approval, and resume after crashes.

**Examples of flows:**

- Plan → implement → test → review → merge (with loops on test failure)
- Research a topic across 5 sub-areas in parallel, then synthesize findings
- Fetch data from a flaky API (with retries), transform with an LLM, validate with a script
- Deploy to staging, wait for human approval, deploy to production or rollback

A flow is the right tool when the answer to "what do I need?" is *coordination* — multiple steps that depend on each other, quality gates, error handling, or work that spans minutes to hours.

## Decision Matrix

| You need... | Use a... | Why |
|---|---|---|
| Domain knowledge for a task | Skill | One-shot context injection is enough |
| Multi-step coordination | Flow | Steps need scheduling, dependencies, state |
| Project conventions during coding | Skill | Claude applies them inline as it works |
| Human approval gates | Flow | `external` executor pauses for input |
| Retry logic on failure | Flow | Exit rules + loop actions handle this |
| A reusable prompt template | Skill | Just context — no orchestration needed |
| Parallel LLM calls with fan-out | Flow | `for_each` runs instances concurrently |
| Agent with specific tool access | Flow | Step-level executor config controls this |
| Coding style guide | Skill | Reference material, not a process |
| "Do X, then if Y do Z, else do W" | Flow | Conditional branching via `when` |

**The one-sentence test:** If the task completes in a single agent turn with the right context, it's a skill. If it requires multiple steps, decisions between steps, or coordination across time, it's a flow.

## They're Not Mutually Exclusive

A flow can benefit from skills. When an agent step runs with `working_dir` pointed at a project, it loads that project's `CLAUDE.md` and any skills in `.claude/skills/`. The flow provides the orchestration; the skills provide the domain knowledge the agent needs at each step.

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

The `implement` step gets both: flow-level orchestration (it runs after `plan`, its outputs feed downstream steps, it retries on test failure) and skill-level knowledge (project conventions, API patterns, style guides loaded from the working directory).

## When People Reach for the Wrong One

**Over-engineering with a flow:** "I want Claude to follow our coding conventions." You don't need a multi-step pipeline for this — write a skill that describes the conventions. The LLM applies them naturally during code generation.

**Under-powering with a skill:** "I want to plan a feature, implement it, run tests, fix failures, and get human sign-off." A skill can describe this process, but it can't enforce it. There's no retry on test failure, no human gate, no persistent state if the session ends. You need a flow.

**The hybrid case:** "I want an agent to implement features in our codebase following our conventions, with test validation." The flow handles the plan → implement → test → fix loop. A skill (loaded via `working_dir`) teaches the agent your conventions. Both tools doing what they're best at.

## Summary

Skills make Claude smarter for a single conversation. Flows make work reliable across multiple steps. Use skills for knowledge, flows for orchestration, and both together when you need an informed agent running a structured process.
