# Skill: Publish

## When to Use

Activate when the user says:
- "publish", "release", "ship it"
- "bump version"
- `/publish`

## Instructions

Stepwise distributes from GitHub master, not PyPI. Publishing means: test, build, update changelog, bump version, commit, tag, push.

### Full Release Pipeline

**1. Run all tests**

```bash
uv run pytest tests/ -q
cd web && npm run test -- --run
```

Stop if any tests fail. Fix or ask the user before proceeding.

**2. Build web assets**

```bash
make build-web
```

This runs `npm install && npm run build` in `web/`, then copies `web/dist/` to `src/stepwise/_web/`.

**3. Generate changelog entry**

Read commits since the last version tag:

```bash
git log --oneline $(git describe --tags --abbrev=0)..HEAD
```

Read the existing `CHANGELOG.md` to match the style. Write a proper changelog entry with Added/Changed/Fixed sections as appropriate. Categorize commits by reading what they actually changed (not just the commit message). Keep it concise — one line per change, no milestone codes.

If `[Unreleased]` already has content, use it as a starting point and merge in any commits not yet captured.

**4. Decide version bump**

- New features or capabilities → **minor** (0.2.0 → 0.3.0)
- Bug fixes or polish only → **patch** (0.2.0 → 0.2.1)
- Pre-1.0: no major bumps. 1.0.0 = stable API commitment.

Confirm with the user if ambiguous.

**5. Update files**

- `pyproject.toml`: update `version = "X.Y.Z"`
- `CHANGELOG.md`: rename `[Unreleased]` to `[X.Y.Z] — YYYY-MM-DD`, add fresh empty `[Unreleased]` above it

**6. Commit, tag, push**

```bash
git add pyproject.toml CHANGELOG.md src/stepwise/_web/
git commit -m "release: vX.Y.Z"
git tag vX.Y.Z
git push origin master --tags
```

**7. Confirm**

Print: "Released vX.Y.Z — users will get it on next `stepwise update`."

**8. Review agent-facing documentation**

These files are injected into agent contexts and must accurately reflect current capabilities. Review the changelog entry and commits in this release, then read each file and check for staleness:

- `src/stepwise/_templates/agent-skill/SKILL.md` — Bundled Claude skill for interactive flow running (interaction modes, CLI reference, exit codes)
- `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` — Complete YAML format spec loaded when agents create/modify flows (executor types, input bindings, exit rules, decorators)
- `src/stepwise/agent_help.py` — `generate_agent_help()` output for `stepwise agent-help`, and `build_emit_flow_instructions()` injected into emit_flow agent prompts

Things to look for:
- New executor types, config fields, or CLI commands not documented
- Changed YAML syntax, exit rule behavior, or input binding formats
- New features (decorators, model labels, etc.) that agents should know about
- Removed or renamed capabilities still referenced in the docs

Present a summary of what's current and what needs updating. Do NOT edit these files without explicit approval — just flag what you found.

**9. Check website for needed updates**

After the release is complete, check if the stepwise.run website (`~/work/stepwise.run`) needs updates to reflect changes in this release.

Review the changelog entry you just wrote and the commits in this release. Then read these website files and compare against current stepwise capabilities:

- `~/work/stepwise.run/site/sections/features.html` — feature cards describing executor types and capabilities
- `~/work/stepwise.run/site/sections/code-example.html` — YAML syntax examples and terminal output
- `~/work/stepwise.run/site/sections/quickstart.html` — installation instructions and CLI commands
- `~/work/stepwise.run/site/sections/meta-story.html` — description of the generation flow
- `~/work/stepwise.run/site/index.html` — hero section copy and DAG visualization

Things to look for:
- New executor types not reflected in feature descriptions or badge color schemes
- CLI commands or YAML syntax that changed
- New capabilities described in the changelog that the marketing copy doesn't mention
- Outdated descriptions that no longer match how stepwise works

If nothing needs updating, say so and move on. If updates are warranted, present a summary of suggested changes with specific file paths and what to update, then ask the user what they'd like to do. Do NOT make changes to the website without explicit approval — just present recommendations.
