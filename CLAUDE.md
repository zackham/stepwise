# Stepwise

Portable workflow orchestration for agents and humans. Package: `stepwise-run`. CLI command: `stepwise`.

## Development

```bash
uv sync                  # install deps
uv run pytest tests/     # run tests
uv run stepwise --help   # run CLI from dev
```

## Distribution

Installs directly from GitHub — no PyPI publishing needed. The install script and `self-update` both pull from `master`:

```bash
# How users install
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh

# Which runs:
uv tool install stepwise-run@git+https://github.com/zackham/stepwise.git
```

**Push to master = users get it on next `stepwise self-update`.**

## Project structure

- `src/stepwise/` — Python package (engine, CLI, server, executors)
- `web/` — React frontend (Vite, TanStack, Tailwind)
- `tests/` — pytest test suite
- `docs/` — markdown documentation
- `install.sh` — universal `curl | sh` installer

## Important conventions

- Package name is `stepwise-run` (not `stepwise` — that's taken on PyPI)
- The CLI command is `stepwise` (via `[project.scripts]`)
- `httpx` is a core dependency (no `[llm]` extra)
- Tests must pass before pushing: `uv run pytest tests/`
