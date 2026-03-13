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
