# Open Source / Hosted Boundary

What stays open source forever, what becomes paid, and why.

---

## Principles

**Open source is permanent, not a marketing phase.** The engine shipped open from day one and stays that way. Contributors and self-hosters are first-class citizens indefinitely.

**No features removed from OSS to force upgrades.** If it works locally today, it works locally tomorrow. The hosted product competes on being better, not on making the alternative worse.

**Hosted adds value on top, doesn't subtract from local.** The paid layer solves problems that don't exist for a single developer on their laptop: team coordination, secrets at scale, sandboxed execution, compliance requirements.

**Users can export everything and leave at any time.** Job history, flow definitions, run artifacts, audit logs — all exportable in standard formats. No proprietary data gravity.

---

## What stays open source (forever)

Everything you need to author, run, debug, and share workflows locally or on your own infrastructure.

**Core engine**
- Job execution, DAG resolution, step readiness, exit rules, settlement
- All step types: script, agent, LLM, external, poll, for-each, callable
- Input resolution, conditional branching (`when`), optional inputs
- Step result caching (content-addressable, local SQLite)
- Named sessions across loop iterations and across steps (via `session:` and `fork_from:`)
- Agent flow emission and iterative delegation
- Decorators: retry, timeout, fallback

**CLI**
- `stepwise run` (all modes: headless, `--wait`, `--async`, `--watch`)
- `stepwise server` (start, stop, restart, status)
- `stepwise validate`, `stepwise cache`, all job management commands
- Server delegation (CLI auto-delegates to running server)

**Web UI**
- Full dashboard: job list, detail, events timeline
- Interactive DAG viewer with pan/zoom and follow-flow
- Visual flow editor with YAML and canvas modes
- Agent-assisted flow editing (chat panel)
- Step detail panels, agent stream viewer, handoff inspector
- External input fulfillment (schema-driven forms)
- Settings and configuration management

**Flow registry**
- Publish, search, pull, fork community flows
- `stepwise share`, `stepwise get`, `stepwise search`, `stepwise info`
- Registry browser and fork-to-local in the web UI

**Local server**
- FastAPI REST API + WebSocket for live updates
- SQLite persistence with WAL mode
- Agent output streaming
- All API endpoints (jobs, config, editor, flows, registry)

**Flow authoring and validation**
- YAML format with full feature set
- Validator (unbounded loops, dead inputs, uncovered exits, type safety)
- Flow bundling and sharing

---

## What becomes hosted/paid

Problems that emerge at team scale, in production, or under compliance requirements. Each has real operational cost behind it.

**Managed runtime**
We run your jobs in sandboxed containers. You don't manage uptime, scaling, or worker infrastructure. Agent steps execute in isolated environments with controlled network access. Same YAML, zero ops.

**Provider proxy**
One billing relationship for all LLM providers. We route to Claude, Gemini, GPT, etc., handle rate limits, retry logic, and key rotation. You stop managing six different API accounts.

**Multi-user**
Organizations, role-based access control, shared workspaces. Multiple people viewing and managing the same jobs concurrently. The local server is single-user by design; the hosted version handles teams.

**Auth & SSO**
Team login via OAuth/SAML, API key management with scoping and rotation, SSO integration for enterprise identity providers. The local server has no auth because it doesn't need it.

**Secrets management**
Encrypted vault for API keys, credentials, and tokens. Scoped to flows, steps, or environments. Rotation and access logging. Locally you put secrets in env vars; at scale you need real secrets infrastructure.

**Audit & compliance**
Exportable audit logs with configurable retention. Who ran what, when, with what inputs, what the agent did, what it produced. Immutable run records for regulated industries.

**Notifications**
Slack, email, and webhook integrations for job events — completions, failures, suspensions waiting for human input. The local server has WebSocket push; the hosted version connects to your team's communication tools.

**Public shareable URLs**
Share job replays — the full DAG execution, step results, agent streams — with non-authenticated viewers via a link.

**Priority support and SLA**
Guaranteed response times, dedicated support channel, migration assistance.

---

## The gray zone (decisions TBD)

These are real questions we haven't locked in yet. They'll be decided based on what we learn from early users.

**Flow marketplace monetization.** The registry is free to publish and pull. Do we eventually support paid premium templates? Leaning toward: free forever for community flows, paid only for commercially-supported "solution packs" from verified publishers.

**Usage-based billing model.** Per job? Per agent-minute? Per LLM token passed through the proxy? Flat tier with limits? The answer depends on which usage patterns actually correlate with cost.

**Enterprise on-prem vs hosted-only.** Some hosted features (like managed runtime) are inherently cloud. Others (like RBAC, audit logs) could ship as an enterprise self-hosted edition. The question is whether the operational complexity of supporting on-prem enterprise is worth it.

**Telemetry.** The open-source version sends nothing home today. If we add opt-in telemetry, it will be off by default with a clear prompt during setup.

---

## Why this split

The engine is commodity infrastructure. Keeping it open builds trust, drives adoption, and lets the community find bugs and contribute improvements faster than we could alone.

The moat is **packaged trust**. Observable runs where you can see exactly what an agent did. Scoped delegation where agents only access what they need. Human gates that pause execution for approval. Full replays for debugging and auditing. Secrets that don't leak. Auth that works for teams. These require operational infrastructure, security posture, and ongoing maintenance — not a weekend hack.

The pattern is proven. n8n open-sourced the workflow engine and monetizes cloud hosting. GitLab open-sourced the DevOps platform and monetizes enterprise features. Sentry open-sourced error tracking and monetizes the hosted service. In each case, the open-source version is genuinely useful on its own, and the paid version solves problems that appear at scale.

We're following the same playbook: the engine is the distribution mechanism, and trust infrastructure is the product.
