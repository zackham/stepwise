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


# ── Auth management ────────────────────────────────────────────────

AUTH_FILE = CONFIG_DIR / "auth.json"


def load_auth() -> dict[str, str] | None:
    """Load auth credentials from disk. Returns dict or None if missing/invalid."""
    try:
        if AUTH_FILE.exists():
            return json.loads(AUTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save_auth(auth_token: str, github_username: str, registry_url: str) -> None:
    """Save auth credentials with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(
        json.dumps(
            {
                "auth_token": auth_token,
                "github_username": github_username,
                "registry_url": registry_url,
            },
            indent=2,
        )
        + "\n"
    )
    AUTH_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def clear_auth() -> None:
    """Remove auth credentials file."""
    try:
        AUTH_FILE.unlink()
    except FileNotFoundError:
        pass


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


def _ensure_json(resp: httpx.Response, context: str) -> dict[str, Any]:
    """Parse JSON from response, raising RegistryError if not JSON."""
    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise RegistryError(
            f"{context}: expected JSON but got {content_type or 'unknown content type'}",
            resp.status_code,
        )
    return resp.json()


# ── Auth API ───────────────────────────────────────────────────────


def initiate_device_flow(registry_url: str | None = None) -> dict[str, Any]:
    """Start GitHub Device Flow. Returns device_code, user_code, verification_uri, interval, expires_in."""
    url = registry_url or get_registry_url()
    with _client() as client:
        resp = client.post(f"{url}/api/auth/device")
    if resp.status_code != 200:
        raise RegistryError(
            f"Failed to initiate login: {resp.status_code} {resp.text}",
            resp.status_code,
        )
    return _ensure_json(resp, "initiate device flow")


def poll_device_flow(
    device_code: str, registry_url: str | None = None
) -> dict[str, Any]:
    """Poll for Device Flow completion. Returns raw server response."""
    url = registry_url or get_registry_url()
    with _client() as client:
        resp = client.post(
            f"{url}/api/auth/poll", json={"device_code": device_code}
        )
    if resp.status_code != 200:
        raise RegistryError(
            f"Poll failed: {resp.status_code} {resp.text}", resp.status_code
        )
    return _ensure_json(resp, "poll device flow")


def verify_auth(
    auth_token: str, registry_url: str | None = None
) -> dict[str, Any]:
    """Verify an auth token. Returns user info or raises RegistryError."""
    url = registry_url or get_registry_url()
    with _client() as client:
        resp = client.get(
            f"{url}/api/auth/verify",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
    if resp.status_code != 200:
        raise RegistryError(
            f"Auth verification failed: {resp.status_code} {resp.text}",
            resp.status_code,
        )
    return _ensure_json(resp, "verify auth")


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
    data = _ensure_json(resp, f"fetch flow '{slug}'")
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
    return _ensure_json(resp, "search flows")


def publish_flow(
    yaml_content: str,
    author: str | None = None,
    files: dict[str, str] | None = None,
    auth_token: str | None = None,
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

    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    with _client() as client:
        resp = client.post(f"{url}/api/flows", json=payload, headers=headers)

    if resp.status_code == 409:
        raise RegistryError(
            f"Flow already exists. Use 'stepwise share --update' to update.",
            409,
        )
    if resp.status_code not in (200, 201):
        raise RegistryError(
            f"Publish failed: {resp.status_code} {resp.text}", resp.status_code
        )

    data = _ensure_json(resp, "publish flow")
    # Save the update token
    if data.get("update_token") and data.get("slug"):
        save_token(data["slug"], data["update_token"])
    return data


def update_flow(
    slug: str,
    yaml_content: str,
    changelog: str | None = None,
    auth_token: str | None = None,
) -> dict[str, Any]:
    """Update an existing flow in the registry.

    Uses per-flow update token if available, falls back to session auth_token.
    """
    token = get_token(slug)
    if not token:
        token = auth_token
    if not token:
        raise RegistryError(
            f"No update token for '{slug}'. "
            f"Run `stepwise login` or publish from the original machine."
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
    return _ensure_json(resp, f"update flow '{slug}'")
