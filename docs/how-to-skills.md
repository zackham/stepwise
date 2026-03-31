# How to Create a Stepwise Agent Skill

How to write `SKILL.md` files that inject domain knowledge into agent sessions, making agents smarter about specific projects and tasks.

---

Agent skills are Markdown files with YAML frontmatter. When an agent activates a skill, it reads the file and gains that capability for the duration of the conversation. Skills don't orchestrate work — they make agents better at a specific domain.

## What's in a Skill?

```markdown
---
name: my-project
description: Conventions for the my-project codebase. Activate when editing files in src/ or writing tests.
---

# My Project

## Architecture

- Backend: FastAPI in src/api/
- Frontend: React in src/web/
- Tests: pytest, run with `uv run pytest tests/`

## Conventions

- All API endpoints return JSON with `{"data": ...}` wrapper
- Use snake_case for Python, camelCase for TypeScript
- Every new endpoint needs a test in tests/test_api.py
```

**Frontmatter fields:**

| Field | Purpose |
|---|---|
| `name` | Skill identifier (kebab-case) |
| `description` | When to activate — the agent framework matches this against user intent |

The `description` field is critical. It's what the agent uses to decide whether to load the skill. Be specific about trigger conditions: mention file paths, keywords, or task types.

## Where Skills Live

Skills are stored in your agent framework's skills directory:

- **Claude Code:** `.claude/skills/<skill-name>/SKILL.md`
- **Other agents:** `.agents/skills/<skill-name>/SKILL.md`

Each skill gets its own directory. You can include additional reference files alongside `SKILL.md` — the stepwise skill, for example, bundles a `FLOW_REFERENCE.md` with the complete YAML format spec.

## The Default Stepwise Skill

When you run `stepwise init`, it offers to install the bundled stepwise skill:

```bash
$ stepwise init
Created .stepwise/ in /path/to/project
Install stepwise agent skill to .claude/skills/stepwise? [Y/n] y
Installed agent skill to .claude/skills/stepwise/
```

The installer detects which agent frameworks you're using and installs to the appropriate location. Use `--no-skill` to skip, or `--skill <dir>` to target a specific directory.

If you have an outdated skill, `stepwise init --force` will update it. The installed files come from `src/stepwise/_templates/agent-skill/` in the stepwise package and teach the agent how to discover flows, run them, handle suspensions, and create new workflows.

## Creating a Project-Specific Skill

The default stepwise skill covers flow orchestration. For project-specific conventions, create your own skill alongside it.

**Example:** Testing conventions for a Django project.

Create `.claude/skills/django-tests/SKILL.md`:

```markdown
---
name: django-tests
description: Django test conventions for this project. Activate when writing or modifying tests, or when test failures need debugging.
---

# Testing Conventions

## Running Tests

- Full suite: `python manage.py test`
- Single app: `python manage.py test myapp`
- Single test: `python manage.py test myapp.tests.TestMyView.test_create`

## Test Structure

- Each app has `tests/` directory with `test_models.py`, `test_views.py`, `test_serializers.py`
- Use `APITestCase` for view tests, `TestCase` for model tests
- Factory Boy factories in `tests/factories.py`

## Fixtures

- Never use Django fixtures (JSON/YAML) — use Factory Boy
- Shared test data: `conftest.py` at project root
- Each test creates its own data — no cross-test dependencies

## Common Patterns

- Auth: `self.client.force_authenticate(user=self.user)`
- File uploads: use `SimpleUploadedFile`
- Async views: use `async_to_sync` wrapper in tests
```

## Tips for Effective Skills

**Be specific in the description.** Vague descriptions like "help with coding" won't trigger at the right time. Instead: "Python backend conventions for the payments service. Activate when editing files in src/payments/ or writing payment-related tests."

**Include concrete examples.** Don't just say "use factories for test data" — show the import path and a usage example. Agents perform better with patterns they can directly adapt.

**Keep it focused.** A skill that covers everything covers nothing well. Split broad topics into separate skills: one for testing, one for API patterns, one for deployment.

**Don't duplicate CLAUDE.md.** Project-wide instructions belong in `CLAUDE.md`. Skills are for specialized knowledge that only applies in certain contexts.

**Reference files, don't inline everything.** For large reference material (API specs, schema docs), put it in a separate file in the skill directory and reference it from `SKILL.md`.

## Skills and Flows Together

Skills and flows complement each other. A flow orchestrates multi-step work; a skill gives the agent domain knowledge at each step. When an agent step sets `working_dir`, the agent loads skills from that directory automatically:

```yaml
steps:
  implement:
    executor: agent
    working_dir: /path/to/project    # loads CLAUDE.md + skills from here
    prompt: "Implement the feature described in $spec"
    outputs: [result]
```

The flow handles coordination (dependencies, retries, human gates). The skills handle context (conventions, patterns, reference material).

---

See [Flows vs Skills](flows-vs-skills.md) for the decision matrix. See [Writing Flows](writing-flows.md) for flow YAML syntax.
