"""Project directory resolution and path helpers.

Stepwise uses a project-local `.stepwise/` directory (like `.git/`) for runtime
artifacts. Global config lives at `~/.config/stepwise/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DOT_DIR_NAME = ".stepwise"


class ProjectNotFoundError(Exception):
    """No .stepwise/ directory found walking up from start."""


@dataclass
class StepwiseProject:
    """Resolved project paths."""

    root: Path  # directory containing .stepwise/
    dot_dir: Path  # root / .stepwise
    db_path: Path  # dot_dir / stepwise.db
    jobs_dir: Path  # dot_dir / jobs
    templates_dir: Path  # dot_dir / templates (user-saved)


def find_project(start: Path | None = None) -> StepwiseProject:
    """Walk up from start (default cwd) looking for .stepwise/.

    Raises ProjectNotFoundError if not found.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        dot = current / DOT_DIR_NAME
        if dot.is_dir():
            return _project_from_root(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise ProjectNotFoundError(
        f"No {DOT_DIR_NAME}/ found (searched up from {start or Path.cwd()}). "
        f"Run 'stepwise init' to create a project."
    )


def init_project(target: Path | None = None, force: bool = False) -> StepwiseProject:
    """Create .stepwise/ in target (default cwd).

    Creates subdirs, .gitignore inside .stepwise/, and appends to parent .gitignore.
    Raises FileExistsError if .stepwise/ already exists (unless force=True).
    """
    root = (target or Path.cwd()).resolve()
    dot = root / DOT_DIR_NAME

    if dot.exists() and not force:
        raise FileExistsError(
            f"{DOT_DIR_NAME}/ already exists in {root}. "
            f"Use --force to reinitialize."
        )

    # Create directory structure
    dot.mkdir(exist_ok=True)
    (dot / "jobs").mkdir(exist_ok=True)
    (dot / "templates").mkdir(exist_ok=True)

    # Self-ignoring safety net
    gitignore_inner = dot / ".gitignore"
    gitignore_inner.write_text("*\n")

    # Append to parent .gitignore if it exists and doesn't already contain .stepwise/
    gitignore_outer = root / ".gitignore"
    entry = f"{DOT_DIR_NAME}/\n"
    if gitignore_outer.exists():
        content = gitignore_outer.read_text()
        if DOT_DIR_NAME + "/" not in content and DOT_DIR_NAME not in content.split("\n"):
            with open(gitignore_outer, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(entry)
    else:
        gitignore_outer.write_text(entry)

    return _project_from_root(root)


def get_bundled_templates_dir() -> Path:
    """Return path to templates bundled with the package."""
    return Path(__file__).parent / "_templates"


def get_bundled_web_dir() -> Path:
    """Return path to web UI assets bundled with the package."""
    return Path(__file__).parent / "_web"


def _project_from_root(root: Path) -> StepwiseProject:
    dot = root / DOT_DIR_NAME
    return StepwiseProject(
        root=root,
        dot_dir=dot,
        db_path=dot / "stepwise.db",
        jobs_dir=dot / "jobs",
        templates_dir=dot / "templates",
    )
