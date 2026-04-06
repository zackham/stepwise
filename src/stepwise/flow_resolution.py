"""Flow discovery and name-based resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FLOW_DIR_MARKER = "FLOW.yaml"
FLOW_FILE_SUFFIX = ".flow.yaml"
FLOW_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+$")
KIT_DIR_MARKER = "KIT.yaml"


@dataclass
class FlowInfo:
    """A discovered flow."""
    name: str
    path: Path          # path to the YAML file (FLOW.yaml or *.flow.yaml)
    is_directory: bool  # True if this is a directory flow
    kit_name: str | None = None  # populated for kit member flows


@dataclass
class KitInfo:
    """A discovered kit."""
    name: str
    path: Path              # path to KIT.yaml
    flow_names: list[str] = field(default_factory=list)   # member flow names (bare)
    flow_paths: list[Path] = field(default_factory=list)   # paths to member FLOW.yaml files


class FlowResolutionError(Exception):
    """Could not resolve a flow name to a file."""


def resolve_flow(name_or_path: str, project_dir: Path | None = None) -> Path:
    """Resolve a flow name or path to the actual YAML file path.

    Resolution order:
    1. Exact path (file or directory containing FLOW.yaml)
    2. Kit-qualified name: "kit/flow" (exactly one slash, no yaml extension)
    3. Search discovery directories: project root -> flows/ -> .stepwise/flows/
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
        # Check if it's a kit directory
        if (candidate / KIT_DIR_MARKER).is_file():
            raise FlowResolutionError(
                f"'{name_or_path}' is a kit, not a flow. "
                f"Use '{candidate.name}/<flow-name>' to run a specific flow."
            )

    # If it looks like a filesystem path (absolute, relative, or yaml extension), reject as path
    if name_or_path.startswith(("/", ".", "~")) or name_or_path.endswith((".yaml", ".yml")):
        raise FlowResolutionError(f"Flow not found: {name_or_path}")

    # Kit-qualified name: "kit/flow" (exactly one slash, bare names only)
    if "/" in name_or_path:
        parts = name_or_path.split("/")
        if len(parts) == 2:
            return _resolve_kit_flow(parts[0], parts[1], project_dir or Path.cwd())
        raise FlowResolutionError(
            f"Invalid flow reference '{name_or_path}': "
            f"use 'kit/flow' format (one level only, no nesting)"
        )

    # Registry flow syntax: @author:slug
    if name_or_path.startswith("@") and ":" in name_or_path:
        parts = name_or_path[1:].split(":", 1)
        if len(parts) == 2:
            return resolve_registry_flow(parts[0], parts[1], project_dir or Path.cwd())

    # Validate as a flow name
    if not FLOW_NAME_PATTERN.match(name_or_path):
        raise FlowResolutionError(
            f"Invalid flow name: '{name_or_path}'. "
            f"Flow names must match [a-zA-Z0-9_.+-]+"
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

        # Check if it's a kit (has KIT.yaml but not FLOW.yaml)
        kit_marker = search_dir / name_or_path / KIT_DIR_MARKER
        if kit_marker.is_file():
            raise FlowResolutionError(
                f"'{name_or_path}' is a kit, not a flow. "
                f"Use '{name_or_path}/<flow-name>' to run a specific flow. "
                f"Available flows: {_list_kit_flows(name_or_path, project_dir)}"
            )

        # Check single-file flow: <dir>/<name>.flow.yaml
        file_flow = search_dir / f"{name_or_path}{FLOW_FILE_SUFFIX}"
        if file_flow.is_file():
            matches.append(file_flow)

    if not matches:
        # Check if the name exists inside any kit
        hint = _kit_hint_for_bare_name(name_or_path, project_dir)
        msg = f"Flow '{name_or_path}' not found."
        if hint:
            msg += f" {hint}"
        else:
            msg += f" Searched: {', '.join(str(d) for d in search_dirs if d.is_dir())}"
        raise FlowResolutionError(msg)

    if len(matches) > 1:
        import logging
        logging.getLogger(__name__).warning(
            "Multiple flows found for '%s', using %s (also found at %s)",
            name_or_path, matches[0], ", ".join(str(m) for m in matches[1:]),
        )

    return matches[0]


def _resolve_kit_flow(kit_name: str, flow_name: str, project_dir: Path) -> Path:
    """Resolve kit/flow to the FLOW.yaml path. Raises FlowResolutionError."""
    search_dirs = _discovery_dirs(project_dir)
    kit_found = False

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        kit_dir = search_dir / kit_name
        if not kit_dir.is_dir():
            continue
        kit_marker = kit_dir / KIT_DIR_MARKER
        if not kit_marker.is_file():
            continue
        kit_found = True

        flow_dir = kit_dir / flow_name
        flow_marker = flow_dir / FLOW_DIR_MARKER
        if flow_marker.is_file():
            return flow_marker

    if kit_found:
        raise FlowResolutionError(
            f"Flow '{flow_name}' not found in kit '{kit_name}'. "
            f"Available flows: {_list_kit_flows(kit_name, project_dir)}"
        )

    # Kit dir exists but no KIT.yaml — it's a regular directory, not a kit
    for search_dir in search_dirs:
        kit_dir = search_dir / kit_name
        if kit_dir.is_dir() and not (kit_dir / KIT_DIR_MARKER).is_file():
            raise FlowResolutionError(
                f"'{kit_name}' is a directory but not a kit (no KIT.yaml). "
                f"Did you mean to run a flow by path?"
            )

    raise FlowResolutionError(f"Kit '{kit_name}' not found.")


def _list_kit_flows(kit_name: str, project_dir: Path) -> str:
    """List flow names in a kit for error messages."""
    for search_dir in _discovery_dirs(project_dir):
        kit_dir = search_dir / kit_name
        if kit_dir.is_dir() and (kit_dir / KIT_DIR_MARKER).is_file():
            names = sorted(
                c.name for c in kit_dir.iterdir()
                if c.is_dir() and (c / FLOW_DIR_MARKER).is_file()
            )
            return ", ".join(names) if names else "(empty kit)"
    return "(unknown)"


def _kit_hint_for_bare_name(name: str, project_dir: Path) -> str | None:
    """If a bare flow name exists inside kits, suggest the kit-qualified form."""
    kit_dirs = _find_kit_dirs(project_dir)
    matches: list[str] = []
    for resolved_dir, kit_name in kit_dirs.items():
        flow_dir = resolved_dir / name
        if flow_dir.is_dir() and (flow_dir / FLOW_DIR_MARKER).is_file():
            matches.append(f"{kit_name}/{name}")
    if len(matches) == 1:
        return f"Did you mean '{matches[0]}'?"
    if len(matches) > 1:
        return f"Did you mean: {', '.join(sorted(matches))}?"
    return None


def _find_kit_dirs(project_dir: Path) -> dict[Path, str]:
    """Find all kit directories across discovery paths.
    Returns {resolved_kit_dir: kit_name}.
    """
    kit_dirs: dict[Path, str] = {}
    for search_dir in _discovery_dirs(project_dir):
        if not search_dir.is_dir():
            continue
        for child in search_dir.iterdir():
            if child.is_dir() and (child / KIT_DIR_MARKER).is_file():
                resolved = child.resolve()
                if resolved not in kit_dirs:
                    kit_dirs[resolved] = child.name
    return kit_dirs


def discover_kits(project_dir: Path) -> list[KitInfo]:
    """Find all kits in a project directory."""
    kit_dirs = _find_kit_dirs(project_dir)
    kits: list[KitInfo] = []
    for resolved_dir, kit_name in sorted(kit_dirs.items(), key=lambda x: x[1]):
        flow_names: list[str] = []
        flow_paths: list[Path] = []
        for child in sorted(resolved_dir.iterdir()):
            if child.is_dir():
                marker = child / FLOW_DIR_MARKER
                if marker.is_file():
                    flow_names.append(child.name)
                    flow_paths.append(marker)
        kits.append(KitInfo(
            name=kit_name,
            path=resolved_dir / KIT_DIR_MARKER,
            flow_names=flow_names,
            flow_paths=flow_paths,
        ))
    return kits


def discover_flows(project_dir: Path) -> list[FlowInfo]:
    """Find all flows in a project directory.

    Searches: project root -> flows/ -> .stepwise/flows/
    Returns deduplicated list (by resolved path).
    Kit member flows have kit_name set.
    """
    flows: list[FlowInfo] = []
    seen: set[Path] = set()
    kit_dir_set = set(_find_kit_dirs(project_dir).keys())

    search_dirs = _discovery_dirs(project_dir)

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue

        # Find directory flows (containing FLOW.yaml) and kit member flows
        for child in sorted(search_dir.iterdir()):
            if not child.is_dir():
                continue

            # Check if this is a kit directory
            if (child / KIT_DIR_MARKER).is_file():
                # It's a kit — enumerate member flows
                for member in sorted(child.iterdir()):
                    if member.is_dir():
                        marker = member / FLOW_DIR_MARKER
                        if marker.is_file():
                            resolved = marker.resolve()
                            if resolved not in seen:
                                seen.add(resolved)
                                flows.append(FlowInfo(
                                    name=member.name,
                                    path=marker,
                                    is_directory=True,
                                    kit_name=child.name,
                                ))
                continue  # skip the FLOW_DIR_MARKER check for the kit dir itself

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
                if resolved in seen:
                    continue
                # Skip if this FLOW.yaml is inside a kit directory
                if any(resolved.is_relative_to(kd) for kd in kit_dir_set):
                    continue  # already discovered as kit member in phase 1
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
