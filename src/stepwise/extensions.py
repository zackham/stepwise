"""Extension discovery for stepwise.

Implements git-style PATH discovery: executables named `stepwise-foo` on PATH
become `stepwise foo` subcommands. Each discovered extension is queried for
metadata via `stepwise-foo --manifest`, which should return JSON:

    {
        "name": "telegram",
        "version": "0.1.0",
        "description": "Route stepwise events to Telegram",
        "capabilities": ["event_consumer", "fulfillment"],
        "config_keys": ["telegram_bot_token", "telegram_chat_id"]
    }

If --manifest is not supported, the extension is listed with just name + path.
Results are cached in .stepwise/extensions.json with a 1-hour TTL.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional


CACHE_TTL_SECONDS = 3600  # 1 hour
MANIFEST_TIMEOUT_SECONDS = 5
EXTENSION_PREFIX = "stepwise-"


@dataclass
class Extension:
    name: str
    path: str
    version: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[List[str]] = None
    config_keys: Optional[List[str]] = None


def _find_executables_on_path() -> list[tuple[str, str]]:
    """Scan PATH for executables named `stepwise-*`.

    Returns a list of (name, full_path) tuples, deduplicated by name
    (first PATH entry wins, same as shell behavior).
    """
    seen_names: dict[str, str] = {}
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)

    for directory in path_dirs:
        if not directory:
            continue
        try:
            entries = os.listdir(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            continue

        for entry in sorted(entries):
            if not entry.startswith(EXTENSION_PREFIX):
                continue
            name = entry[len(EXTENSION_PREFIX):]
            if not name:
                continue
            if name in seen_names:
                continue  # first PATH entry wins
            full_path = os.path.join(directory, entry)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                seen_names[name] = full_path

    return list(seen_names.items())


def _query_manifest(executable_path: str) -> dict | None:
    """Run `<executable> --manifest` and parse JSON response.

    Returns the parsed dict, or None if the executable doesn't support
    --manifest or if parsing fails.
    """
    try:
        result = subprocess.run(
            [executable_path, "--manifest"],
            capture_output=True,
            text=True,
            timeout=MANIFEST_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _load_cache(cache_path: Path) -> dict | None:
    """Load extension cache, returning None if missing, expired, or invalid."""
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text())
        ts = data.get("timestamp", 0)
        if time.time() - ts > CACHE_TTL_SECONDS:
            return None
        return data
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _save_cache(cache_path: Path, extensions: list[Extension]) -> None:
    """Write extension list to cache with current timestamp."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": time.time(),
            "extensions": [asdict(ext) for ext in extensions],
        }
        cache_path.write_text(json.dumps(payload, indent=2))
    except OSError:
        pass  # cache write failure is non-fatal


def _extensions_from_cache(data: dict) -> list[Extension]:
    """Deserialize Extension objects from cache data."""
    result = []
    for item in data.get("extensions", []):
        result.append(Extension(
            name=item.get("name", ""),
            path=item.get("path", ""),
            version=item.get("version"),
            description=item.get("description"),
            capabilities=item.get("capabilities"),
            config_keys=item.get("config_keys"),
        ))
    return result


def scan_extensions(
    dot_dir: Path | None = None,
    refresh: bool = False,
) -> list[Extension]:
    """Scan PATH for stepwise extensions and return their metadata.

    Args:
        dot_dir: Path to the .stepwise/ directory for caching. If None,
                 caching is skipped.
        refresh: If True, bypass cache and force a fresh scan.

    Returns:
        List of Extension objects sorted by name.
    """
    cache_path = (dot_dir / "extensions.json") if dot_dir is not None else None

    # Try cache first
    if not refresh and cache_path is not None:
        cached = _load_cache(cache_path)
        if cached is not None:
            return _extensions_from_cache(cached)

    # Fresh scan
    found = _find_executables_on_path()
    extensions = []

    for name, path in found:
        manifest = _query_manifest(path)
        if manifest and isinstance(manifest, dict):
            ext = Extension(
                name=manifest.get("name", name),
                path=path,
                version=manifest.get("version"),
                description=manifest.get("description"),
                capabilities=manifest.get("capabilities"),
                config_keys=manifest.get("config_keys"),
            )
        else:
            ext = Extension(name=name, path=path)
        extensions.append(ext)

    extensions.sort(key=lambda e: e.name)

    # Save to cache
    if cache_path is not None:
        _save_cache(cache_path, extensions)

    return extensions
