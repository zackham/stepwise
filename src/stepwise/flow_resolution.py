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
class IncludedFlow:
    """A flow included in a kit via the include field."""
    name: str               # the name it's known as within the kit
    path: Path              # resolved path to FLOW.yaml
    source_ref: str         # original include string (e.g., "@author:slug@^1.0")
    source_type: str        # "registry", "kit", or "standalone"


@dataclass
class KitInfo:
    """A discovered kit."""
    name: str
    path: Path              # path to KIT.yaml
    flow_names: list[str] = field(default_factory=list)   # bundled flow names (bare)
    flow_paths: list[Path] = field(default_factory=list)   # paths to bundled FLOW.yaml files
    included_flows: list[IncludedFlow] = field(default_factory=list)  # resolved includes

    @property
    def all_flow_names(self) -> list[str]:
        """All flow names: bundled + included."""
        return self.flow_names + [f.name for f in self.included_flows]

    @property
    def all_flow_paths(self) -> list[Path]:
        """All flow paths: bundled + included."""
        return self.flow_paths + [f.path for f in self.included_flows]


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

        # Check bundled flows first
        flow_dir = kit_dir / flow_name
        flow_marker = flow_dir / FLOW_DIR_MARKER
        if flow_marker.is_file():
            return flow_marker

        # Check included flows
        try:
            from stepwise.yaml_loader import load_kit_yaml, KitLoadError
            kit_def = load_kit_yaml(kit_marker)
            if kit_def.include:
                bundled = set()
                for child in kit_dir.iterdir():
                    if child.is_dir() and (child / FLOW_DIR_MARKER).is_file():
                        bundled.add(child.name)
                included = resolve_kit_includes(
                    kit_name, kit_def.include, project_dir, bundled,
                )
                for inc in included:
                    if inc.name == flow_name:
                        return inc.path
        except Exception:
            pass

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


def get_kit_defaults_for_flow(flow_path: Path) -> dict | None:
    """If a flow is inside a kit directory, load and return the kit's defaults.

    Returns None if the flow is not in a kit or the kit has no defaults.
    """
    flow_yaml = Path(flow_path)
    # Expected structure: flows/<kit>/<flow>/FLOW.yaml
    # flow_yaml.parent = flow dir, .parent = kit dir, .parent = flows dir
    kit_dir = flow_yaml.parent.parent
    kit_marker = kit_dir / KIT_DIR_MARKER
    if not kit_marker.is_file():
        return None

    from stepwise.yaml_loader import load_kit_yaml, KitLoadError
    try:
        kit_def = load_kit_yaml(kit_marker)
    except (KitLoadError, Exception):
        return None

    if not kit_def.defaults:
        return None
    return dict(kit_def.defaults)


class IncludeRef:
    """Parsed include reference."""
    __slots__ = ("raw", "ref_type", "author", "slug", "version_constraint", "kit", "flow")

    def __init__(self, raw: str, ref_type: str, **kwargs):
        self.raw = raw
        self.ref_type = ref_type  # "registry", "kit", "standalone"
        self.author = kwargs.get("author", "")
        self.slug = kwargs.get("slug", "")
        self.version_constraint = kwargs.get("version_constraint", "")
        self.kit = kwargs.get("kit", "")
        self.flow = kwargs.get("flow", "")

    @property
    def flow_name(self) -> str:
        """The bare flow name this will be known as in the kit."""
        if self.ref_type == "registry":
            return self.slug
        if self.ref_type == "kit":
            return self.flow
        return self.raw  # standalone


def parse_include_ref(ref: str) -> IncludeRef:
    """Parse an include string into a structured reference.

    Formats:
        @author:slug           → registry, latest
        @author:slug@^1.0     → registry, version constraint
        @author:slug@1.2.3    → registry, exact version
        kit/flow              → local kit flow
        flow-name             → local standalone flow
    """
    ref = ref.strip()
    if not ref:
        raise FlowResolutionError("Empty include reference")

    # Registry: @author:slug[@constraint]
    if ref.startswith("@"):
        rest = ref[1:]  # strip leading @
        # Split on @ for version constraint (second @ is the version delimiter)
        if "@" in rest:
            ref_part, version_part = rest.split("@", 1)
        else:
            ref_part = rest
            version_part = ""

        if ":" not in ref_part:
            raise FlowResolutionError(
                f"Invalid registry include: {ref!r}. "
                f"Expected @author:slug or @author:slug@constraint"
            )
        author, slug = ref_part.split(":", 1)
        if not author or not slug:
            raise FlowResolutionError(f"Invalid registry include: {ref!r}")
        return IncludeRef(
            ref, "registry", author=author, slug=slug,
            version_constraint=version_part,
        )

    # Kit flow: kit/flow (one slash, no @ prefix)
    if "/" in ref:
        parts = ref.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise FlowResolutionError(
                f"Invalid kit flow include: {ref!r}. Expected kit/flow format."
            )
        return IncludeRef(ref, "kit", kit=parts[0], flow=parts[1])

    # Standalone flow
    if not FLOW_NAME_PATTERN.match(ref):
        raise FlowResolutionError(f"Invalid include reference: {ref!r}")
    return IncludeRef(ref, "standalone")


def resolve_kit_includes(
    kit_name: str,
    includes: list[str],
    project_dir: Path,
    bundled_names: set[str],
    auto_fetch: bool = True,
) -> list[IncludedFlow]:
    """Resolve include references to actual flow paths.

    Args:
        kit_name: Name of the kit being resolved (for error messages).
        includes: List of include reference strings from KIT.yaml.
        project_dir: Project root directory.
        bundled_names: Set of flow names already bundled in the kit.
        auto_fetch: If True, auto-install missing registry flows.

    Returns:
        List of resolved IncludedFlow objects.

    Raises:
        FlowResolutionError: If an include can't be resolved or collides.
    """
    import logging
    logger = logging.getLogger(__name__)
    resolved: list[IncludedFlow] = []
    seen_names: set[str] = set()

    for ref_str in includes:
        ref = parse_include_ref(ref_str)
        flow_name = ref.flow_name

        # Check for collision with bundled flows
        if flow_name in bundled_names:
            raise FlowResolutionError(
                f"Kit '{kit_name}': include '{ref_str}' conflicts with "
                f"bundled flow '{flow_name}'. Flow names must be unique within a kit."
            )

        # Check for collision with other includes
        if flow_name in seen_names:
            raise FlowResolutionError(
                f"Kit '{kit_name}': duplicate include name '{flow_name}' "
                f"from '{ref_str}'. Flow names must be unique within a kit."
            )

        try:
            if ref.ref_type == "registry":
                path = _resolve_registry_include(
                    ref, project_dir, auto_fetch=auto_fetch,
                )
            elif ref.ref_type == "kit":
                path = _resolve_kit_flow(ref.kit, ref.flow, project_dir)
            else:
                path = resolve_flow(ref.raw, project_dir)

            resolved.append(IncludedFlow(
                name=flow_name,
                path=path,
                source_ref=ref_str,
                source_type=ref.ref_type,
            ))
            seen_names.add(flow_name)
        except FlowResolutionError as e:
            logger.warning("Kit '%s': failed to resolve include '%s': %s", kit_name, ref_str, e)
            # Don't hard-fail on unresolvable includes — warn and skip
            # This allows kits to work with partial includes when offline

    return resolved


def _resolve_registry_include(
    ref: IncludeRef, project_dir: Path, auto_fetch: bool = True,
) -> Path:
    """Resolve a registry include, optionally auto-fetching."""
    # Check if already installed
    reg_dir = project_dir / ".stepwise" / "registry" / f"@{ref.author}" / ref.slug
    flow_yaml = reg_dir / FLOW_DIR_MARKER
    if not flow_yaml.is_file():
        # Try single-file variant
        flow_yaml = reg_dir / f"{ref.slug}{FLOW_FILE_SUFFIX}"

    if flow_yaml.is_file():
        # Installed — check version constraint if specified
        if ref.version_constraint:
            _check_version_constraint(flow_yaml, ref)
        return flow_yaml

    if not auto_fetch:
        raise FlowResolutionError(
            f"Registry flow @{ref.author}:{ref.slug} not installed. "
            f"Run: stepwise get @{ref.author}:{ref.slug}"
        )

    # Auto-fetch from registry
    import logging
    logging.getLogger(__name__).info(
        "Auto-installing registry flow @%s:%s for kit include", ref.author, ref.slug,
    )
    try:
        from stepwise.registry_client import fetch_flow
        flow_data = fetch_flow(ref.slug)
        if not flow_data:
            raise FlowResolutionError(
                f"Registry flow @{ref.author}:{ref.slug} not found in registry"
            )

        from stepwise.bundle import unpack_bundle
        files = flow_data.get("files") or {}
        yaml_content = flow_data.get("yaml", "")
        origin = {
            "registry": "https://stepwise.run",
            "author": flow_data.get("author", ref.author),
            "slug": ref.slug,
            "version": flow_data.get("version", ""),
        }
        target = reg_dir
        unpack_bundle(target, yaml_content, files, origin)

        flow_yaml = target / FLOW_DIR_MARKER
        if not flow_yaml.is_file():
            raise FlowResolutionError(
                f"Failed to install @{ref.author}:{ref.slug} — FLOW.yaml not created"
            )

        if ref.version_constraint:
            _check_version_constraint(flow_yaml, ref)

        return flow_yaml

    except ImportError:
        raise FlowResolutionError(
            f"Registry client not available. "
            f"Run: stepwise get @{ref.author}:{ref.slug}"
        )
    except Exception as e:
        raise FlowResolutionError(
            f"Failed to auto-install @{ref.author}:{ref.slug}: {e}"
        )


def _check_version_constraint(flow_yaml: Path, ref: IncludeRef) -> None:
    """Check if an installed flow's version satisfies the constraint."""
    from stepwise.version import version_matches, VersionError

    # Read version from the flow's origin.json or YAML metadata
    origin_path = flow_yaml.parent / ".origin.json"
    version = ""
    if origin_path.is_file():
        import json
        try:
            origin = json.loads(origin_path.read_text())
            version = origin.get("version", "")
        except Exception:
            pass

    if not version:
        # Try reading from YAML metadata
        try:
            from stepwise.yaml_loader import load_workflow_yaml
            wf = load_workflow_yaml(flow_yaml, kit_defaults={})  # empty to skip auto-detect
            version = wf.metadata.version
        except Exception:
            pass

    if not version:
        import logging
        logging.getLogger(__name__).warning(
            "Cannot check version constraint for @%s:%s — no version found",
            ref.author, ref.slug,
        )
        return

    try:
        if not version_matches(version, ref.version_constraint):
            raise FlowResolutionError(
                f"@{ref.author}:{ref.slug} version {version} does not satisfy "
                f"constraint {ref.version_constraint!r}"
            )
    except VersionError as e:
        raise FlowResolutionError(str(e))


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


def discover_kits(project_dir: Path, resolve_includes: bool = True) -> list[KitInfo]:
    """Find all kits in a project directory.

    Args:
        project_dir: Project root.
        resolve_includes: If True, resolve include references. Set False to
            avoid network calls or circular resolution.
    """
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

        # Resolve includes
        included_flows: list[IncludedFlow] = []
        if resolve_includes:
            kit_yaml_path = resolved_dir / KIT_DIR_MARKER
            try:
                from stepwise.yaml_loader import load_kit_yaml, KitLoadError
                kit_def = load_kit_yaml(kit_yaml_path)
                if kit_def.include:
                    included_flows = resolve_kit_includes(
                        kit_name=kit_name,
                        includes=kit_def.include,
                        project_dir=project_dir,
                        bundled_names=set(flow_names),
                    )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to resolve includes for kit '%s'", kit_name, exc_info=True,
                )

        kits.append(KitInfo(
            name=kit_name,
            path=resolved_dir / KIT_DIR_MARKER,
            flow_names=flow_names,
            flow_paths=flow_paths,
            included_flows=included_flows,
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


def registry_kit_dir(author: str, slug: str, project_dir: Path) -> Path:
    """Return the directory path for a registry kit install."""
    return project_dir / ".stepwise" / "registry" / f"@{author}" / slug


def _discovery_dirs(project_dir: Path) -> list[Path]:
    """Return ordered list of directories to search for flows."""
    return [
        project_dir,
        project_dir / "flows",
        project_dir / ".stepwise" / "flows",
        Path.home() / ".stepwise" / "flows",
    ]
