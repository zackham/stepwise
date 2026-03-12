"""Client for the Stepwise flow registry at stepwise.run."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx

from stepwise.config import CONFIG_DIR

DEFAULT_REGISTRY_URL = "https://stepwise.run"
TOKENS_FILE = CONFIG_DIR / "tokens.json"
CACHE_DIR = Path.home() / ".cache" / "stepwise" / "flows"


def get_registry_url() -> str:
    """Get the registry URL from env var or default."""
    url = os.environ.get("STEPWISE_REGISTRY_URL", DEFAULT_REGISTRY_URL)
    return url.rstrip("/")


# ── Token management ───────────────────────────────────────────────


def _load_tokens() -> dict[str, str]:
    """Load publish tokens from disk."""
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {}


def _save_tokens(tokens: dict[str, str]) -> None:
    """Save publish tokens with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2) + "\n")
    TOKENS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def get_token(slug: str) -> str | None:
    """Get the publish token for a flow slug."""
    return _load_tokens().get(slug)


def save_token(slug: str, token: str) -> None:
    """Store a publish token for a flow slug."""
    tokens = _load_tokens()
    tokens[slug] = token
    _save_tokens(tokens)


# ── Disk cache ─────────────────────────────────────────────────────


def _cache_path(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.flow.yaml"


def get_cached(slug: str) -> str | None:
    """Return cached YAML content for a slug, or None."""
    p = _cache_path(slug)
    if p.exists():
        return p.read_text()
    return None


def cache_flow(slug: str, yaml_content: str) -> None:
    """Cache a flow's YAML content on disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(slug).write_text(yaml_content)


# ── API client ─────────────────────────────────────────────────────


class RegistryError(Exception):
    """Error communicating with the registry."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def fetch_flow(slug: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Fetch flow metadata + YAML from the registry.

    Returns dict with keys: name, slug, author, version, description,
    tags, yaml, steps, loops, etc.
    """
    url = get_registry_url()
    with _client() as client:
        resp = client.get(f"{url}/api/flows/{slug}")
    if resp.status_code == 404:
        raise RegistryError(f"Flow '{slug}' not found in registry", 404)
    if resp.status_code != 200:
        raise RegistryError(
            f"Registry error: {resp.status_code} {resp.text}", resp.status_code
        )
    data = resp.json()
    # Cache the YAML content
    if data.get("yaml"):
        cache_flow(slug, data["yaml"])
    return data


def fetch_flow_yaml(slug: str, *, use_cache: bool = True) -> str:
    """Fetch just the YAML content for a flow. Uses cache if available."""
    if use_cache:
        cached = get_cached(slug)
        if cached:
            return cached

    url = get_registry_url()
    with _client() as client:
        resp = client.get(f"{url}/api/flows/{slug}/raw")
    if resp.status_code == 404:
        raise RegistryError(f"Flow '{slug}' not found in registry", 404)
    if resp.status_code != 200:
        raise RegistryError(
            f"Registry error: {resp.status_code} {resp.text}", resp.status_code
        )
    yaml_content = resp.text
    cache_flow(slug, yaml_content)
    return yaml_content


def search_flows(
    query: str = "",
    tag: str | None = None,
    sort: str = "downloads",
    limit: int = 20,
) -> dict[str, Any]:
    """Search the registry. Returns {flows: [...], total: int}."""
    url = get_registry_url()
    params: dict[str, Any] = {"sort": sort, "limit": limit}
    if query:
        params["q"] = query
    if tag:
        params["tag"] = tag

    with _client() as client:
        resp = client.get(f"{url}/api/flows", params=params)
    if resp.status_code != 200:
        raise RegistryError(
            f"Registry error: {resp.status_code} {resp.text}", resp.status_code
        )
    return resp.json()


def publish_flow(
    yaml_content: str,
    author: str | None = None,
    files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Publish a flow to the registry.

    Returns the flow metadata dict including update_token on first publish.
    If files dict is present, includes bundled co-located files in the payload.
    """
    url = get_registry_url()
    payload: dict[str, Any] = {"yaml": yaml_content, "source": "cli"}
    if author:
        payload["author"] = author
    if files:
        payload["files"] = files

    with _client() as client:
        resp = client.post(f"{url}/api/flows", json=payload)

    if resp.status_code == 409:
        raise RegistryError(
            f"Flow already exists. Use 'stepwise share --update' to update.",
            409,
        )
    if resp.status_code not in (200, 201):
        raise RegistryError(
            f"Publish failed: {resp.status_code} {resp.text}", resp.status_code
        )

    data = resp.json()
    # Save the update token
    if data.get("update_token") and data.get("slug"):
        save_token(data["slug"], data["update_token"])
    return data


def update_flow(
    slug: str,
    yaml_content: str,
    changelog: str | None = None,
) -> dict[str, Any]:
    """Update an existing flow in the registry (requires stored token)."""
    token = get_token(slug)
    if not token:
        raise RegistryError(
            f"No update token for '{slug}'. "
            f"You can only update flows you published from this machine."
        )

    url = get_registry_url()
    payload: dict[str, Any] = {"yaml": yaml_content}
    if changelog:
        payload["changelog"] = changelog

    with _client() as client:
        resp = client.put(
            f"{url}/api/flows/{slug}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 403:
        raise RegistryError("Invalid update token — you may not own this flow.", 403)
    if resp.status_code == 404:
        raise RegistryError(f"Flow '{slug}' not found in registry", 404)
    if resp.status_code != 200:
        raise RegistryError(
            f"Update failed: {resp.status_code} {resp.text}", resp.status_code
        )
    return resp.json()
