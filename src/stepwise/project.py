"""Project directory resolution and path helpers.

Stepwise uses a project-local `.stepwise/` directory (like `.git/`) for runtime
artifacts. Global config lives at `~/.config/stepwise/`.
"""

from __future__ import annotations

import filecmp
import shutil
from dataclasses import dataclass, field
from pathlib import Path


DOT_DIR_NAME = ".stepwise"
SKILL_NAME = "stepwise"

# Agent framework directories, checked in order
AGENT_FRAMEWORK_DIRS = [
    (".claude", "Claude Code"),
    (".agents", "Agents (Codex, etc.)"),
]


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

    @property
    def logs_dir(self) -> Path:
        return self.dot_dir / "logs"


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
    (dot / "hooks").mkdir(exist_ok=True)
    (dot / "logs").mkdir(exist_ok=True)

    # Scaffold example hook scripts
    from stepwise.hooks import scaffold_hooks
    scaffold_hooks(dot)

    # Project config files
    config_path = dot / "config.yaml"
    if not config_path.exists():
        config_path.write_text(
            "# Stepwise project config — committed to git\n"
            "# Override label assignments or add custom labels here.\n"
            "labels: {}\n"
        )

    config_local_path = dot / "config.local.yaml"
    if not config_local_path.exists():
        config_local_path.write_text(
            "# Local overrides — NOT committed to git\n"
            "# API keys and personal label overrides go here.\n"
        )

    # Self-ignoring safety net — ignore everything except config.yaml
    gitignore_inner = dot / ".gitignore"
    gitignore_inner.write_text("*\n!config.yaml\nconfig.local.yaml\n")

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


def get_bundled_skill_dir() -> Path:
    """Return path to bundled agent skill templates."""
    return Path(__file__).parent / "_templates" / "agent-skill"


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


# ── Agent skill installation ────────────────────────────────────────


@dataclass
class AgentSkillLocation:
    """A detected agent framework directory that could hold skills."""

    path: Path  # e.g., /project/.claude
    resolved: Path  # after resolving symlinks
    framework_dir: str  # ".claude" or ".agents"
    label: str  # "Claude Code" or "Agents (Codex, etc.)"
    scope: str  # "local" or "global"
    has_skill: bool = False
    skill_current: bool = False


@dataclass
class SkillDetectionResult:
    """Result of scanning for agent framework dirs and existing skills."""

    locations: list[AgentSkillLocation] = field(default_factory=list)
    symlinked_groups: list[list[AgentSkillLocation]] = field(default_factory=list)

    @property
    def any_installed(self) -> bool:
        return any(loc.has_skill for loc in self.locations)

    @property
    def all_current(self) -> bool:
        return all(loc.skill_current for loc in self.locations if loc.has_skill)


def detect_agent_skill_locations(root: Path) -> SkillDetectionResult:
    """Scan for agent framework directories and existing stepwise skills.

    Checks both local (project) and global (~/) directories. Detects symlinks
    between .claude and .agents to avoid double-installing.
    """
    result = SkillDetectionResult()
    seen_resolved: dict[Path, AgentSkillLocation] = {}

    home = Path.home()
    bundled = get_bundled_skill_dir()

    # Check local and global for each framework
    for framework_dir, label in AGENT_FRAMEWORK_DIRS:
        for scope, base in [("local", root), ("global", home)]:
            candidate = base / framework_dir
            if not candidate.exists():
                continue

            resolved = candidate.resolve()
            skill_dir = candidate / "skills" / SKILL_NAME

            loc = AgentSkillLocation(
                path=candidate,
                resolved=resolved,
                framework_dir=framework_dir,
                label=label,
                scope=scope,
                has_skill=skill_dir.is_dir() and (skill_dir / "SKILL.md").exists(),
            )

            if loc.has_skill:
                loc.skill_current = _is_skill_current(skill_dir, bundled)

            # Track symlink groups
            if resolved in seen_resolved:
                # This dir resolves to the same place as another one
                existing = seen_resolved[resolved]
                # Find or create the symlink group
                found_group = False
                for group in result.symlinked_groups:
                    if existing in group:
                        group.append(loc)
                        found_group = True
                        break
                if not found_group:
                    result.symlinked_groups.append([existing, loc])
            else:
                seen_resolved[resolved] = loc

            result.locations.append(loc)

    return result


def _is_skill_current(installed_dir: Path, bundled_dir: Path) -> bool:
    """Check if installed skill files match bundled templates."""
    if not bundled_dir.is_dir():
        return True  # No bundled templates to compare against

    for bundled_file in bundled_dir.iterdir():
        if bundled_file.is_file():
            installed_file = installed_dir / bundled_file.name
            if not installed_file.exists():
                return False
            if not filecmp.cmp(bundled_file, installed_file, shallow=False):
                return False
    return True


def install_agent_skill(target_agent_dir: Path) -> Path:
    """Install bundled skill templates into an agent framework directory.

    Args:
        target_agent_dir: The agent dir (e.g., /project/.claude or /project/.agents)

    Returns:
        Path to the installed skill directory.
    """
    bundled = get_bundled_skill_dir()
    skill_dir = target_agent_dir / "skills" / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)

    for src_file in bundled.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, skill_dir / src_file.name)

    return skill_dir


def uninstalled_framework_dirs(root: Path) -> list[tuple[str, str]]:
    """Return framework dirs that exist in root but don't have the skill yet.

    Returns list of (framework_dir, label) tuples.
    """
    result = []
    seen_resolved: set[Path] = set()

    for framework_dir, label in AGENT_FRAMEWORK_DIRS:
        candidate = root / framework_dir
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue  # Symlink to already-seen dir
        seen_resolved.add(resolved)

        skill_dir = candidate / "skills" / SKILL_NAME
        if not (skill_dir.is_dir() and (skill_dir / "SKILL.md").exists()):
            result.append((framework_dir, label))

    return result
