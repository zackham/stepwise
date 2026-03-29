# Open Source / Hosted Boundary

What stays open source forever, what becomes paid, and why.

---

## Principles

**Open source is permanent, not a marketing phase.** The engine shipped open from day one and stays that way. This isn't a bait-and-switch where we open-source to get adoption, then slowly gate features behind a login. Contributors and self-hosters are first-class citizens indefinitely.

**No features removed from OSS to force upgrades.** If it works locally today, it works locally tomorrow. We will never take a working capability out of the open-source release to create artificial pressure to upgrade. The hosted product competes on being better, not on making the alternative worse.

**Hosted adds value on top, doesn't subtract from local.** The paid layer solves problems that don't exist for a single developer on their laptop: team coordination, secrets at scale, sandboxed execution, compliance paperwork. These are real operational costs, not invented ones.

**Users can export everything and leave at any time.** Job history, flow definitions, run artifacts, audit logs — all exportable in standard formats. No proprietary data gravity. If we lose a customer, it should be because they don't need us, not because they're trapped.

---

## What stays open source (forever)

Everything you need to author, run, debug, and share workflows locally or on your own infrastructure.

**Core engine**
- Job execution, DAG resolution, step readiness, exit rules, settlement
- All step types: script, agent, LLM, external, poll, for-each, callable
- Input resolution, conditional branching (`when`), optional inputs
- Step result caching (content-addressable, local SQLite)
- Session continuity across loop iterations
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
- Publish, search, pull, install community flows
- `stepwise registry` commands
- Registry browser in the web UI

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

Problems that emerge at team scale, in production, or under compliance requirements. Each of these has real operational cost behind it.

**Managed runtime**
We run your jobs in sandboxed containers. You don't manage uptime, scaling, or worker infrastructure. Agent steps execute in isolated environments with controlled network access. This is the core convenience play — same YAML, zero ops.

**Provider proxy**
One billing relationship for all LLM providers. We route to Claude, Gemini, GPT, etc., handle rate limits, retry logic, and key rotation. You stop managing six different API accounts and worrying about which key is in which env var.

**Multi-user**
Organizations, role-based access control, shared workspaces. Multiple people viewing and managing the same jobs concurrently. Ownership, permissions, and visibility controls that don't exist in a single-user SQLite database.

**Auth & SSO**
Team login via OAuth/SAML, API key management with scoping and rotation, SSO integration for enterprise identity providers. The local server has no auth because it doesn't need it — the hosted version does.

**Secrets management**
Encrypted vault for API keys, credentials, and tokens. Scoped to flows, steps, or environments. Rotation and access logging. Locally you put secrets in env vars; at scale you need a real secrets infrastructure.

**Audit & compliance**
Exportable audit logs with configurable retention. Who ran what, when, with what inputs, what the agent did, what it produced. Immutable run records for regulated industries. SOC 2 posture for customers who need to show their auditors something.

**Notifications**
Slack, email, and webhook integrations for job events — completions, failures, escalations, suspensions waiting for human input. The local server has WebSocket push; the hosted version connects to your team's communication tools.

**Public shareable URLs**
Share job replays — the full DAG execution, step results, agent streams — with non-authenticated viewers via a link. Useful for demos, debugging with collaborators, or showing stakeholders what happened.

**Priority support and SLA**
Guaranteed response times, dedicated support channel, migration assistance. The open-source project has community support via GitHub issues; paying customers get a human on the other end with a commitment.

---

## The gray zone (decisions TBD)

These are real questions we haven't locked in yet. They'll be decided based on what we learn from early users.

**Flow marketplace monetization.** The registry is free to publish and pull. Do we eventually support paid premium templates? If so, what's the revenue split with authors? Leaning toward: free forever for community flows, paid only for commercially-supported "solution packs" from verified publishers.

**Usage-based billing model.** Per job? Per agent-minute? Per LLM token passed through the proxy? Flat tier with limits? The answer depends on which usage patterns actually correlate with cost. We need real data before committing.

**Enterprise on-prem vs hosted-only.** Some features in the hosted tier (like managed runtime) are inherently cloud. Others (like RBAC, audit logs) could ship as an enterprise self-hosted edition. The question is whether the operational complexity of supporting on-prem enterprise is worth it, or whether we point those customers at the hosted version.

**Telemetry and analytics.** The open-source version sends nothing home today. If we add opt-in telemetry (crash reports, anonymous usage stats), what data, how transparent, and how easy to disable? Default: off, with a clear prompt during setup.

---

## Why this split

The engine is commodity infrastructure. Every orchestration tool has one, and the differences between them shrink over time. Keeping it open builds trust, drives adoption, and lets the community find bugs and contribute improvements faster than we could alone. Trying to monetize the engine directly means competing on features that get copied in months.

The moat is **packaged trust**. Observable runs where you can see exactly what an agent did. Scoped delegation where agents only access what they need. Human gates that pause execution for approval. Full replays for debugging and auditing. Secrets that don't leak. Auth that works for teams. These aren't features you can bolt on with a weekend hack — they require operational infrastructure, security posture, and ongoing maintenance.

The pattern is proven. n8n open-sourced the workflow engine and monetizes cloud hosting and an expert network. GitLab open-sourced the DevOps platform and monetizes enterprise features and hosting. Sentry open-sourced error tracking and monetizes the hosted service. In each case, the open-source version is genuinely useful on its own, and the paid version solves problems that appear at scale.

We're following the same playbook: the engine is the distribution mechanism, and trust infrastructure is the product.
