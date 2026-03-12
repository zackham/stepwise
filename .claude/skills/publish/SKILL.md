# Skill: Publish

## When to Use

Activate when the user says:
- "publish", "release", "ship it"
- "bump version"

## Instructions

Stepwise distributes from GitHub, not PyPI. Publishing means: bump version, update changelog, commit, push.

### Steps

1. Read the current version from `pyproject.toml`
2. Look at `CHANGELOG.md` `[Unreleased]` section to understand what changed
3. Decide version bump: minor for features, patch for fixes. Confirm with the user if ambiguous.
4. Edit `pyproject.toml`: update `version = "X.Y.Z"`
5. Edit `CHANGELOG.md`: rename `[Unreleased]` to `[X.Y.Z] — YYYY-MM-DD` and add a fresh empty `[Unreleased]` section above it
6. Run tests: `uv run pytest tests/`
7. Commit and push:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "release vX.Y.Z"
   git tag vX.Y.Z
   git push && git push --tags
   ```
8. Print confirmation: users will get the update on next `stepwise update`
