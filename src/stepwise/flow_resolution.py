"""Flow discovery and name-based resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FLOW_DIR_MARKER = "FLOW.yaml"
FLOW_FILE_SUFFIX = ".flow.yaml"
FLOW_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass
class FlowInfo:
    """A discovered flow."""
    name: str
    path: Path          # path to the YAML file (FLOW.yaml or *.flow.yaml)
    is_directory: bool  # True if this is a directory flow


class FlowResolutionError(Exception):
    """Could not resolve a flow name to a file."""


def resolve_flow(name_or_path: str, project_dir: Path | None = None) -> Path:
    """Resolve a flow name or path to the actual YAML file path.

    Resolution order:
    1. Exact path (file or directory containing FLOW.yaml)
    2. Search discovery directories: project root -> flows/ -> .stepwise/flows/
       Within each: check <name>/FLOW.yaml first, then <name>.flow.yaml

    Raises FlowResolutionError if not found.
    """
    candidate = Path(name_or_path)

    # 1. Exact path
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        marker = candidate / FLOW_DIR_MARKER
        if marker.is_file():
            return marker
    # If it looks like a path (contains / or ends with .yaml), don't do name resolution
    if "/" in name_or_path or name_or_path.endswith((".yaml", ".yml")):
        raise FlowResolutionError(f"Flow not found: {name_or_path}")

    # Validate as a flow name
    if not FLOW_NAME_PATTERN.match(name_or_path):
        raise FlowResolutionError(
            f"Invalid flow name: '{name_or_path}'. "
            f"Flow names must match [a-zA-Z0-9_-]+"
        )

    # 2. Search discovery directories
    if project_dir is None:
        project_dir = Path.cwd()

    search_dirs = _discovery_dirs(project_dir)
    matches: list[Path] = []

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue

        # Check directory flow first: <dir>/<name>/FLOW.yaml
        dir_flow = search_dir / name_or_path / FLOW_DIR_MARKER
        if dir_flow.is_file():
            matches.append(dir_flow)
            continue  # directory takes precedence within this search dir

        # Check single-file flow: <dir>/<name>.flow.yaml
        file_flow = search_dir / f"{name_or_path}{FLOW_FILE_SUFFIX}"
        if file_flow.is_file():
            matches.append(file_flow)

    if not matches:
        raise FlowResolutionError(
            f"Flow '{name_or_path}' not found. "
            f"Searched: {', '.join(str(d) for d in search_dirs if d.is_dir())}"
        )

    if len(matches) > 1:
        import sys
        print(
            f"Warning: multiple flows found for '{name_or_path}', "
            f"using {matches[0]} (also found at {', '.join(str(m) for m in matches[1:])})",
            file=sys.stderr,
        )

    return matches[0]


def discover_flows(project_dir: Path) -> list[FlowInfo]:
    """Find all flows in a project directory.

    Searches: project root -> flows/ -> .stepwise/flows/
    Returns deduplicated list (by resolved path).
    """
    flows: list[FlowInfo] = []
    seen: set[Path] = set()

    search_dirs = _discovery_dirs(project_dir)

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue

        # Find directory flows (containing FLOW.yaml)
        for child in sorted(search_dir.iterdir()):
            if not child.is_dir():
                continue
            marker = child / FLOW_DIR_MARKER
            if marker.is_file():
                resolved = marker.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    flows.append(FlowInfo(
                        name=child.name,
                        path=marker,
                        is_directory=True,
                    ))

        # Find single-file flows (*.flow.yaml)
        for child in sorted(search_dir.glob(f"*{FLOW_FILE_SUFFIX}")):
            resolved = child.resolve()
            if resolved not in seen:
                seen.add(resolved)
                name = child.name.removesuffix(FLOW_FILE_SUFFIX)
                # Skip if a directory flow with same name already found
                if any(f.name == name for f in flows):
                    continue
                flows.append(FlowInfo(
                    name=name,
                    path=child,
                    is_directory=False,
                ))

        # Recurse into subdirectories for .stepwise/flows/ and flows/
        if search_dir != project_dir:
            for child in sorted(search_dir.rglob(f"*{FLOW_FILE_SUFFIX}")):
                resolved = child.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    name = child.name.removesuffix(FLOW_FILE_SUFFIX)
                    flows.append(FlowInfo(
                        name=name,
                        path=child,
                        is_directory=False,
                    ))
            for child in sorted(search_dir.rglob(FLOW_DIR_MARKER)):
                resolved = child.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    flows.append(FlowInfo(
                        name=child.parent.name,
                        path=child,
                        is_directory=True,
                    ))

    return flows


def flow_display_name(flow_path: Path) -> str:
    """Derive a human-readable flow name from a flow file path.

    - agent-test/FLOW.yaml  → "agent-test"
    - my-flow.flow.yaml     → "my-flow"
    - something.yaml        → "something"
    """
    if flow_path.name == FLOW_DIR_MARKER:
        return flow_path.parent.name
    name = flow_path.name
    if name.endswith(FLOW_FILE_SUFFIX):
        return name.removesuffix(FLOW_FILE_SUFFIX)
    return flow_path.stem


def resolve_registry_flow(author: str, slug: str, project_dir: Path | None = None) -> Path:
    """Resolve a registry flow ref (@author:slug) to its cached FLOW.yaml path.

    Looks in .stepwise/registry/@author/slug/FLOW.yaml within the project.
    Raises FlowResolutionError if not cached locally.
    """
    if project_dir is None:
        project_dir = Path.cwd()

    registry_dir = project_dir / ".stepwise" / "registry" / f"@{author}" / slug
    marker = registry_dir / FLOW_DIR_MARKER
    if marker.is_file():
        return marker

    raise FlowResolutionError(
        f"Registry flow '@{author}:{slug}' not cached locally. "
        f"Expected at: {registry_dir}"
    )


@dataclass
class RegistryFlowInfo:
    """A discovered registry flow."""
    author: str
    slug: str
    path: Path  # path to FLOW.yaml

    @property
    def ref(self) -> str:
        return f"@{self.author}:{self.slug}"


def discover_registry_flows(project_dir: Path) -> list[RegistryFlowInfo]:
    """Find all cached registry flows in .stepwise/registry/."""
    registry_dir = project_dir / ".stepwise" / "registry"
    if not registry_dir.is_dir():
        return []

    flows: list[RegistryFlowInfo] = []
    for author_dir in sorted(registry_dir.iterdir()):
        if not author_dir.is_dir() or not author_dir.name.startswith("@"):
            continue
        author = author_dir.name[1:]  # strip @
        for slug_dir in sorted(author_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            marker = slug_dir / FLOW_DIR_MARKER
            if marker.is_file():
                flows.append(RegistryFlowInfo(
                    author=author,
                    slug=slug_dir.name,
                    path=marker,
                ))
    return flows


def registry_flow_dir(author: str, slug: str, project_dir: Path) -> Path:
    """Return the directory path for a registry flow cache."""
    return project_dir / ".stepwise" / "registry" / f"@{author}" / slug


def parse_registry_ref(ref: str) -> tuple[str, str] | None:
    """Parse @author:name into (author, slug). Returns None if not a registry ref."""
    if not ref.startswith("@"):
        return None
    body = ref[1:]  # strip @
    if ":" in body:
        author, slug = body.split(":", 1)
        return (author, slug)
    return None


def _discovery_dirs(project_dir: Path) -> list[Path]:
    """Return ordered list of directories to search for flows."""
    return [
        project_dir,
        project_dir / "flows",
        project_dir / ".stepwise" / "flows",
        Path.home() / ".stepwise" / "flows",
    ]
