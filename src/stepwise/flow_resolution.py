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


def _discovery_dirs(project_dir: Path) -> list[Path]:
    """Return ordered list of directories to search for flows."""
    return [
        project_dir,
        project_dir / "flows",
        project_dir / ".stepwise" / "flows",
        Path.home() / ".stepwise" / "flows",
    ]
