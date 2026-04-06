"""Bundle support for flow directories — collect for sharing, unpack on get."""

from __future__ import annotations

import json
from pathlib import Path

MAX_BUNDLE_SIZE = 500 * 1024  # 500KB total
MAX_FILE_COUNT = 20
ALLOWED_EXTENSIONS = {".py", ".sh", ".bash", ".md", ".txt", ".yaml", ".yml", ".json", ".prompt"}
BLOCKED_FILES = {".env", ".pem", "id_rsa", "credentials.json", ".DS_Store", "config.local.yaml"}
BLOCKED_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}


class BundleError(Exception):
    """Error collecting or unpacking a bundle."""


def collect_bundle(flow_dir: Path) -> dict[str, str]:
    """Collect files from a flow directory for publishing.

    Returns a dict mapping relative paths to file contents.
    Excludes FLOW.yaml itself (that's sent separately as the primary artifact).

    Raises BundleError if limits are exceeded or blocked files found.
    """
    if not flow_dir.is_dir():
        raise BundleError(f"Not a directory: {flow_dir}")

    files: dict[str, str] = {}
    total_size = 0

    for path in sorted(flow_dir.rglob("*")):
        if not path.is_file():
            continue

        rel = path.relative_to(flow_dir)
        rel_str = str(rel)

        # Skip the FLOW.yaml itself
        if rel_str == "FLOW.yaml":
            continue

        # Check blocked directories
        if any(part in BLOCKED_DIRS for part in rel.parts):
            continue

        # Check blocked files (before hidden-file skip so .env etc. are caught)
        if path.name in BLOCKED_FILES:
            if path.name == "config.local.yaml":
                continue  # silently skip user config files
            raise BundleError(
                f"Blocked file found: {rel_str}. "
                f"Remove it before sharing, or add it to .gitignore."
            )
        # Skip single-file flow config siblings (e.g. my-flow.config.local.yaml)
        if path.name.endswith(".config.local.yaml"):
            continue

        # Check hidden files (except .origin.json)
        if any(part.startswith(".") for part in rel.parts) and rel_str != ".origin.json":
            continue

        # Check extension
        if path.suffix not in ALLOWED_EXTENSIONS:
            continue  # silently skip non-allowed extensions

        # Check UTF-8
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # skip binary files silently

        total_size += len(content.encode("utf-8"))
        files[rel_str] = content

    # Check limits
    if len(files) > MAX_FILE_COUNT:
        raise BundleError(
            f"Too many files: {len(files)} (max {MAX_FILE_COUNT}). "
            f"Remove unnecessary files from the flow directory."
        )

    if total_size > MAX_BUNDLE_SIZE:
        raise BundleError(
            f"Bundle too large: {total_size:,} bytes (max {MAX_BUNDLE_SIZE:,}). "
            f"Reduce file sizes or remove unnecessary files."
        )

    return files


def unpack_bundle(
    target_dir: Path,
    yaml_content: str,
    files: dict[str, str] | None = None,
    origin: dict | None = None,
) -> Path:
    """Unpack a flow bundle into a directory.

    Creates target_dir/FLOW.yaml and writes any co-located files.
    Optionally writes .origin.json for provenance.

    Returns path to the created FLOW.yaml.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    # Write FLOW.yaml
    flow_path = target_dir / "FLOW.yaml"
    flow_path.write_text(yaml_content)

    # Write co-located files
    if files:
        for rel_path, content in files.items():
            file_path = target_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)

    # Write origin tracking
    if origin:
        origin_path = target_dir / ".origin.json"
        origin_path.write_text(json.dumps(origin, indent=2) + "\n")

    return flow_path


def collect_kit_bundle(kit_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """Collect a kit directory for publishing.

    Returns (kit_yaml_content, bundled_flows) where bundled_flows is:
    [{"name": "flow-name", "yaml": "...", "files": {"path": "content"} | None}, ...]
    """
    kit_yaml_path = kit_dir / "KIT.yaml"
    if not kit_yaml_path.is_file():
        raise BundleError(f"No KIT.yaml in {kit_dir}")

    kit_yaml = kit_yaml_path.read_text(encoding="utf-8")
    bundled_flows: list[dict] = []

    for sub in sorted(kit_dir.iterdir()):
        if not sub.is_dir():
            continue
        flow_yaml = sub / "FLOW.yaml"
        if not flow_yaml.is_file():
            continue
        name = sub.name
        yaml_content = flow_yaml.read_text(encoding="utf-8")
        files = collect_bundle(sub)
        bundled_flows.append({
            "name": name,
            "yaml": yaml_content,
            "files": files if files else None,
        })

    if not bundled_flows:
        raise BundleError(
            f"Kit '{kit_dir.name}' has no bundled flows "
            f"(no subdirectories with FLOW.yaml)"
        )

    return kit_yaml, bundled_flows


def unpack_kit_bundle(
    target_dir: Path,
    kit_yaml: str,
    bundled_flows: list[dict],
    origin: dict | None = None,
) -> Path:
    """Unpack a kit bundle into a directory.

    Creates: target_dir/KIT.yaml, target_dir/{flow}/FLOW.yaml, target_dir/.origin.json
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    kit_path = target_dir / "KIT.yaml"
    kit_path.write_text(kit_yaml)

    for flow in bundled_flows:
        flow_name = flow["name"]
        yaml_content = flow.get("yaml_content") or flow.get("yaml", "")
        files = flow.get("files_json") or flow.get("files")
        unpack_bundle(
            target_dir=target_dir / flow_name,
            yaml_content=yaml_content,
            files=files,
        )

    if origin:
        origin_path = target_dir / ".origin.json"
        origin_path.write_text(json.dumps(origin, indent=2) + "\n")

    return kit_path
