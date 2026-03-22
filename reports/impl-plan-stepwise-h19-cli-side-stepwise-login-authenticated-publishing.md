---
title: "Implementation Plan: H19 CLI-Side — stepwise login + Authenticated Publishing"
date: "2026-03-21T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# H19 CLI-Side: `stepwise login` + Authenticated Publishing

## Overview

Add `stepwise login` (GitHub Device Flow) and `stepwise logout` commands, and require authentication for `stepwise share` publishing. Auth state is stored in `~/.config/stepwise/auth.json` alongside the existing per-flow `tokens.json`.

## Requirements

### R1: `stepwise login` command
- **AC1**: Calls `POST {registry_url}/api/auth/device` to initiate GitHub Device Flow. On network error or non-200, prints error and exits with code 2 (`EXIT_USAGE_ERROR`, `cli.py:52`).
- **AC2**: Displays "Visit {verification_uri} and enter code: {user_code}" via `io.log("info", ...)`.
- **AC3**: Polls `POST {registry_url}/api/auth/poll` with `{"device_code": ...}` every `interval` seconds (value returned by server in AC1 response). Handles `slow_down` by adding 5s to interval per GitHub Device Flow spec.
- **AC4**: On success (response contains `auth_token`), writes `{"auth_token": "...", "github_username": "...", "registry_url": "..."}` to `~/.config/stepwise/auth.json` with file mode `0o600`.
- **AC5**: On success, prints "Logged in as @{username}. You can now publish flows with `stepwise share`." and exits 0.
- **AC6**: If already logged in (auth.json exists), calls `GET {registry_url}/api/auth/verify` with Bearer header. If valid (200), prints "Already logged in as @{username}" and exits 0. If invalid (401/network error), falls through to re-authenticate.
- **AC7**: On `expired_token` or `access_denied` poll response, prints specific error and exits with code 2. On `KeyboardInterrupt`, prints "Login cancelled." and exits 2.

### R2: `stepwise logout` command
- **AC1**: If `~/.config/stepwise/auth.json` exists, deletes it.
- **AC2**: After deletion, prints "Logged out." via `io.log("success", ...)` and exits 0.
- **AC3**: If file doesn't exist, prints "Not logged in." via `io.log("info", ...)` and exits 0.

### R3: Authenticated `stepwise share`
- **AC1**: Before publish/update `try` block (`cli.py:2429`), calls `load_auth()` to read `auth.json`.
- **AC2**: For new publishes (`not do_update`): if `load_auth()` returns `None`, prints "You need to log in first. Run `stepwise login`." via `io.log("error", ...)` and returns `EXIT_USAGE_ERROR`.
- **AC3**: Passes `auth_token=auth["auth_token"]` to `publish_flow()` call at `cli.py:2439`. The function adds `Authorization: Bearer {token}` header to the `POST /api/flows` request.
- **AC4**: In the `except RegistryError` block (`cli.py:2447`): if `e.status_code == 401`, appends "Your session may have expired. Run `stepwise login` to re-authenticate." to the error output.
- **AC5**: For updates (`do_update` path, `cli.py:2430-2437`): passes `auth_token` to `update_flow()` as fallback. Per-flow token from `tokens.json` takes priority; auth token is used only when no per-flow token exists.

### R4: CLI help and docstring updates
- **AC1**: Module docstring at `cli.py:1-29` updated to include `stepwise login` and `stepwise logout` entries.
- **AC2**: Parser help strings match the command descriptions: "Log in to the Stepwise registry via GitHub" and "Log out of the Stepwise registry".

## Assumptions

| # | Assumption | Verification |
|---|-----------|-------------|
| A1 | `CONFIG_DIR` is `~/.config/stepwise` and is the standard location for user-level credentials | Confirmed: `src/stepwise/config.py:21` — `CONFIG_DIR = Path.home() / ".config" / "stepwise"`. Imported by `registry_client.py:13`. |
| A2 | Secure file write pattern: `CONFIG_DIR.mkdir(parents=True, exist_ok=True)` + write + `chmod(stat.S_IRUSR \| stat.S_IWUSR)` | Confirmed: `src/stepwise/registry_client.py:38-40` — `_save_tokens()` does exactly this. |
| A3 | `get_registry_url()` returns the base URL (no trailing slash) from `STEPWISE_REGISTRY_URL` env or default `https://stepwise.run` | Confirmed: `src/stepwise/registry_client.py:20-23` — `url.rstrip("/")`. |
| A4 | CLI commands use lazy imports inside the handler function body, not at module top-level | Confirmed: `src/stepwise/cli.py:2339-2342` — `cmd_share` imports `publish_flow`, `update_flow`, etc. inside function. Also `cli.py:3488-3490` (`cmd_cache`). This avoids circular imports and speeds up CLI startup. |
| A5 | CLI commands use `io = _io(args)` for all user-facing output, never bare `print()` for messages | Confirmed: `src/stepwise/cli.py:2368` — `cmd_share` calls `_io(args)` then `io.log("success", ...)`, `io.log("error", ...)`, etc. Exception: some older commands use `print(..., file=sys.stderr)` for errors (e.g., `cli.py:2346`). |
| A6 | `publish_flow()` currently sends NO auth header on the POST request | Confirmed: `src/stepwise/registry_client.py:185-186` — `client.post(f"{url}/api/flows", json=payload)` with no `headers` kwarg. |
| A7 | `update_flow()` uses `Authorization: Bearer {token}` with per-flow token from `get_token(slug)` | Confirmed: `src/stepwise/registry_client.py:211-228` — loads token at line 211, sends at line 227. |
| A8 | `_client()` returns `httpx.Client(timeout=30.0)` used as context manager | Confirmed: `src/stepwise/registry_client.py:87-88`. Same pattern for all API calls in the module. |
| A9 | `_ensure_json()` validates content-type and parses JSON, raising `RegistryError` on non-JSON | Confirmed: `src/stepwise/registry_client.py:91-99`. Used by `fetch_flow`, `search_flows`, `publish_flow`, `update_flow`. |
| A10 | `time.sleep()` is used elsewhere in `cli.py` for polling loops | Confirmed: `src/stepwise/cli.py:511` (server start health probe), `cli.py:549` (server stop wait). Pattern: `import time` inside function, loop with `time.sleep(interval)`. |
| A11 | Server `POST /api/auth/device` returns `{device_code, user_code, verification_uri, interval, expires_in}` | Per spec — standard GitHub Device Flow response fields. |
| A12 | Server `POST /api/auth/poll` returns `{auth_token, github_username}` on success; `{error: "authorization_pending"}`, `{error: "slow_down"}`, `{error: "expired_token"}`, or `{error: "access_denied"}` while waiting | Per spec — matches GitHub Device Flow error codes. |
| A13 | Server `GET /api/auth/verify` returns 200 with user info on valid token, 401 on invalid/expired | Per spec. |
| A14 | Exit codes: `EXIT_SUCCESS = 0`, `EXIT_USAGE_ERROR = 2` are the appropriate return values | Confirmed: `src/stepwise/cli.py:50-52`. `cmd_share` returns these two values exclusively (`cli.py:2347,2363,2449` for error; `cli.py:2427,2451` for success). |
| A15 | Test files use `monkeypatch.setattr("stepwise.registry_client.CONSTANT", value)` to redirect file paths to `tmp_path` | Confirmed: `tests/test_registry.py:54-55` — `monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tokens_file)` and same for `CONFIG_DIR`. |
| A16 | Mock httpx client pattern: `MagicMock()` with `__enter__`/`__exit__` + `monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)` | Confirmed: `tests/test_registry.py:101-106`. |

## Out of Scope

| Item | Reason |
|------|--------|
| Token refresh/expiration handling | Spec states tokens don't expire yet. Can be added later when server implements expiration. |
| `stepwise whoami` command | Spec explicitly lists as out of scope. Login already prints username. |
| Migrating existing flows to verified authors | Manual process per spec; no automated migration. |
| Browser-based login (auto-open URL) | Spec says Device Flow is CLI-native. Could be a follow-up UX enhancement. |
| Server-side auth endpoints | Already implemented per spec. This plan covers CLI side only. |
| Changes to `web/` frontend | No web UI for login; this is a CLI-only feature. |
| `_load_tokens()` error handling | Existing function (`registry_client.py:29-33`) doesn't handle corrupt JSON either; out of scope for this PR. |

## Architecture

### File Changes Summary

| File | Change Type | Lines Affected |
|------|------------|---------------|
| `src/stepwise/registry_client.py` | Add functions + modify existing | After line 53 (new section), lines 168-202 (modify `publish_flow`), lines 205-238 (modify `update_flow`) |
| `src/stepwise/cli.py` | Add functions + modify existing | Lines 1-29 (docstring), after `cmd_share` (new `cmd_login`, `cmd_logout`), lines 2337-2451 (modify `cmd_share`), lines 3271-3485 (`build_parser`), lines 3676-3704 (`main` handlers) |
| `tests/test_auth.py` | New file | ~250 lines |
| `tests/test_registry.py` | No changes | Regression-only — existing tests must still pass |

### Design Decisions

**1. Auth file separate from tokens file.**
`auth.json` stores per-user identity (one GitHub account). `tokens.json` stores per-flow update tokens (one per published flow, `registry_client.py:16,48-52`). They have different lifecycles: `logout` clears identity but preserves per-flow tokens; a user may have tokens from before auth was required.

**2. All auth functions in `registry_client.py`.**
This module already owns all registry communication (`_client()` at line 87, `publish_flow` at line 168, `update_flow` at line 205) and credential storage (`_load_tokens`/`_save_tokens` at lines 29-40, `get_token`/`save_token` at lines 43-52). Adding auth here follows the single-module pattern. The CLI handler in `cli.py` orchestrates calls, matching how `cmd_share` (line 2337) imports and calls `publish_flow`/`update_flow`.

**3. `publish_flow()` receives `auth_token` as a parameter, not loaded internally.**
`update_flow()` already loads its own token (`get_token(slug)` at line 211), but that's a per-flow token keyed by slug. The auth token is caller-provided context. Passing it as a parameter keeps `publish_flow` pure and testable — the test just passes a string, no file mocking needed for the publish path itself.

**4. `update_flow()` tries per-flow token first, falls back to auth token.**
Per spec: "PUT /api/flows/{slug} accepts either per-flow update_token OR auth session token." The per-flow token is more specific, so it takes priority. If missing (e.g., flow published from another machine), the auth session token works as fallback. This avoids breaking the existing `--update` path for users who already have per-flow tokens.

**5. Polling uses `time.sleep()` in a blocking loop.**
`cmd_login` is inherently interactive and blocking — the user is waiting. The existing codebase uses this exact pattern: `cli.py:510-512` (server start health probe: `for _ in range(50): time.sleep(0.1)`) and `cli.py:548-550` (server stop wait). Device Flow interval is typically 5s, timeout ~900s.

**6. `verify_auth()` call on `stepwise login` when already logged in.**
Could just check file existence, but the spec says "If valid, print already logged in." Calling verify catches expired/revoked tokens server-side, improving UX. If verify fails or network is down, we fall through to re-authenticate rather than erroring out.

## Implementation Steps

### Step 1: Add auth file management to `registry_client.py`

**File**: `src/stepwise/registry_client.py`
**Insert after**: Line 53 (end of existing token management section)
**Depends on**: Nothing (pure addition, no existing code modified)

Add new section `# ── Auth management ──` with three functions and one constant:

**`AUTH_FILE`** — `CONFIG_DIR / "auth.json"` (mirrors `TOKENS_FILE = CONFIG_DIR / "tokens.json"` at line 16)

**`load_auth() -> dict[str, str] | None`**
- Input: none (reads from `AUTH_FILE`)
- Output: dict with keys `auth_token`, `github_username`, `registry_url` — or `None` if file missing or JSON invalid
- Logic: `if AUTH_FILE.exists(): try: return json.loads(AUTH_FILE.read_text()) except (json.JSONDecodeError, KeyError): return None`
- Pattern source: `_load_tokens()` at lines 29-33, but with try/except around JSON parse (spec requires resilience to corruption)

**`save_auth(auth_token: str, github_username: str, registry_url: str) -> None`**
- Input: three strings
- Output: writes `{"auth_token": auth_token, "github_username": github_username, "registry_url": registry_url}` to `AUTH_FILE`
- Logic: identical to `_save_tokens()` at lines 36-40 — `CONFIG_DIR.mkdir(parents=True, exist_ok=True)`, write JSON, `chmod(stat.S_IRUSR | stat.S_IWUSR)`

**`clear_auth() -> None`**
- Input: none
- Output: deletes `AUTH_FILE` if it exists
- Logic: `if AUTH_FILE.exists(): AUTH_FILE.unlink()`

### Step 2: Add Device Flow API functions to `registry_client.py`

**File**: `src/stepwise/registry_client.py`
**Insert after**: Step 1's new section (before the existing `fetch_flow` at line 102)
**Depends on**: Step 1 (for `AUTH_FILE` constant import proximity, though not functionally required). More importantly, these functions use `_client()` (line 87) and `_ensure_json()` (line 91) which already exist.

Add three API functions to the `# ── API client ──` section:

**`initiate_device_flow(registry_url: str | None = None) -> dict[str, Any]`**
- Input: optional registry URL override (defaults to `get_registry_url()`)
- Output: dict with keys `device_code`, `user_code`, `verification_uri`, `interval`, `expires_in`
- HTTP: `POST {url}/api/auth/device` with empty JSON body
- Error: non-200 → `RegistryError(f"Failed to initiate login: {resp.status_code} {resp.text}", resp.status_code)`
- Pattern: matches `publish_flow()` lines 178-196 (POST + status check + `_ensure_json`)

**`poll_device_flow(device_code: str, registry_url: str | None = None) -> dict[str, Any]`**
- Input: device_code string, optional registry URL
- Output: raw server JSON response — either `{auth_token, github_username}` or `{error: "..."}`
- HTTP: `POST {url}/api/auth/poll` with `{"device_code": device_code}`
- Error: non-200 → `RegistryError`. Note: `authorization_pending` is a 200-level response with error field, not an HTTP error.
- Pattern: same POST pattern as `initiate_device_flow`

**`verify_auth(auth_token: str, registry_url: str | None = None) -> dict[str, Any]`**
- Input: auth token string, optional registry URL
- Output: user info dict on success
- HTTP: `GET {url}/api/auth/verify` with `headers={"Authorization": f"Bearer {auth_token}"}`
- Error: 401 → `RegistryError("Auth token is invalid or expired", 401)`. Other non-200 → generic `RegistryError`.
- Pattern: matches `update_flow()` Bearer header usage at lines 223-228

### Step 3: Modify `publish_flow()` signature and implementation

**File**: `src/stepwise/registry_client.py`, lines 168-202
**Depends on**: Steps 1-2 complete (so the module is coherent), but functionally independent — this is a signature change.

**Change**: Add `auth_token: str | None = None` parameter to `publish_flow()`.

Current line 185-186:
```python
with _client() as client:
    resp = client.post(f"{url}/api/flows", json=payload)
```

New:
```python
headers = {}
if auth_token:
    headers["Authorization"] = f"Bearer {auth_token}"
with _client() as client:
    resp = client.post(f"{url}/api/flows", json=payload, headers=headers)
```

Also add 401 handling after the existing 409 check (line 188):
```python
if resp.status_code == 401:
    raise RegistryError("Authentication required. Run `stepwise login`.", 401)
```

### Step 4: Modify `update_flow()` to accept auth token fallback

**File**: `src/stepwise/registry_client.py`, lines 205-238
**Depends on**: Step 3 (same module, avoids merge conflicts; also conceptually paired — both publish paths get auth support in sequence).

**Change**: Add `auth_token: str | None = None` parameter.

Current lines 211-216 (token resolution):
```python
token = get_token(slug)
if not token:
    raise RegistryError(
        f"No update token for '{slug}'. "
        f"You can only update flows you published from this machine."
    )
```

New:
```python
token = get_token(slug)
if not token:
    token = auth_token
if not token:
    raise RegistryError(
        f"No update token for '{slug}'. "
        f"Log in with `stepwise login` or publish from the original machine."
    )
```

Rest of the function unchanged — `token` is already used for the Bearer header at line 227.

### Step 5: Register `login`/`logout` in CLI parser and handler dispatch

**File**: `src/stepwise/cli.py`
**Depends on**: Nothing in `registry_client.py` — this is pure parser registration. Done before implementing the handlers so the parser is ready for testing.

**5a. Update module docstring** (`cli.py:1-29`): Add two lines:
```
    stepwise login                        Log in to the Stepwise registry
    stepwise logout                       Log out of the Stepwise registry
```
Insert after the `stepwise share` line (line 11).

**5b. Add subparsers** in `build_parser()` (`cli.py:3271-3485`): Insert after the `uninstall` block (after line 3483):
```python
# login
sub.add_parser("login", help="Log in to the Stepwise registry via GitHub")

# logout
sub.add_parser("logout", help="Log out of the Stepwise registry")
```

**5c. Add handler dispatch** in `main()` (`cli.py:3676-3704`): Add to the `handlers` dict:
```python
"login": cmd_login,
"logout": cmd_logout,
```

### Step 6: Implement `cmd_logout`

**File**: `src/stepwise/cli.py`
**Insert after**: `cmd_share` function (after line 2451), before `cmd_search` (line 2454)
**Depends on**: Step 1 (needs `load_auth`, `clear_auth` from `registry_client.py`), Step 5 (parser registration)

New function — ~15 lines:

```python
def cmd_logout(args: argparse.Namespace) -> int:
    """Log out of the Stepwise registry."""
    from stepwise.registry_client import clear_auth, load_auth
```

Logic:
1. `io = _io(args)` — get IO adapter (pattern from `cli.py:2368`)
2. `auth = load_auth()` — check if logged in
3. If `auth is None`: `io.log("info", "Not logged in.")` → return `EXIT_SUCCESS`
4. `clear_auth()` — delete auth file
5. `io.log("success", "Logged out.")` → return `EXIT_SUCCESS`

This is the simpler of the two commands. Implementing it first validates that the Step 1 auth file functions work end-to-end before tackling the more complex login flow.

### Step 7: Implement `cmd_login`

**File**: `src/stepwise/cli.py`
**Insert before**: `cmd_logout` (immediately preceding it in the file)
**Depends on**: Steps 1-2 (auth file management + Device Flow API functions), Step 5 (parser registration), Step 6 (validates auth file functions work)

New function — ~50 lines:

```python
def cmd_login(args: argparse.Namespace) -> int:
    """Log in to the Stepwise registry via GitHub."""
    import time
    from stepwise.registry_client import (
        RegistryError, initiate_device_flow, load_auth,
        poll_device_flow, save_auth, verify_auth,
    )
```

Logic flow (each with specific inputs/outputs):

1. **Check existing auth**: `auth = load_auth()`
   - If `auth` is not None:
     - Try `verify_auth(auth["auth_token"])` — if succeeds (no exception), `io.log("info", f"Already logged in as @{auth['github_username']}.")` → return `EXIT_SUCCESS`
     - If `RegistryError` with status 401: fall through to re-authenticate
     - If other `RegistryError` (network): fall through (don't block login on verify failure)

2. **Initiate Device Flow**: `resp = initiate_device_flow()`
   - Extracts: `device_code = resp["device_code"]`, `user_code = resp["user_code"]`, `verification_uri = resp["verification_uri"]`, `interval = resp.get("interval", 5)`, `expires_in = resp.get("expires_in", 900)`
   - Display: `io.log("info", f"Visit {verification_uri} and enter code: {user_code}")`

3. **Poll loop**:
   ```python
   deadline = time.time() + expires_in
   while time.time() < deadline:
       time.sleep(interval)
       result = poll_device_flow(device_code)
   ```
   - If `"auth_token" in result`: save + print + return (see below)
   - If `result.get("error") == "authorization_pending"`: continue
   - If `result.get("error") == "slow_down"`: `interval += 5`; continue
   - If `result.get("error") == "expired_token"`: `io.log("error", "Login timed out. Please try again.")` → return `EXIT_USAGE_ERROR`
   - If `result.get("error") == "access_denied"`: `io.log("error", "Login denied.")` → return `EXIT_USAGE_ERROR`
   - Any other error string: `io.log("error", f"Login failed: {result.get('error')}")` → return `EXIT_USAGE_ERROR`

4. **On success** (inside poll loop):
   - `save_auth(result["auth_token"], result["github_username"], get_registry_url())`
   - `io.log("success", f"Logged in as @{result['github_username']}. You can now publish flows with \`stepwise share\`.")`
   - Return `EXIT_SUCCESS`

5. **After loop** (deadline exceeded without success):
   - `io.log("error", "Login timed out. Please try again.")` → return `EXIT_USAGE_ERROR`

6. **Exception handling**: Wrap entire function body in:
   - `except RegistryError as e:` → `io.log("error", f"Login failed: {e}")` → return `EXIT_USAGE_ERROR`
   - `except KeyboardInterrupt:` → `io.log("info", "\nLogin cancelled.")` → return `EXIT_USAGE_ERROR`

### Step 8: Modify `cmd_share` for authentication

**File**: `src/stepwise/cli.py`, function `cmd_share` at line 2337
**Depends on**: Steps 1, 3, 4 (auth file management + modified `publish_flow`/`update_flow`). Also depends on Step 7 logically (so users can actually log in before sharing), though the code doesn't call `cmd_login`.

**8a. Add import** — modify line 2341 from:
```python
from stepwise.registry_client import publish_flow, update_flow, RegistryError
```
to:
```python
from stepwise.registry_client import load_auth, publish_flow, update_flow, RegistryError
```

**8b. Add auth check** — insert between line 2428 (end of bundle confirmation) and line 2429 (start of `try` block):
```python
auth = load_auth()
auth_token = auth["auth_token"] if auth else None

if not do_update and not auth_token:
    io.log("error", "You need to log in first. Run `stepwise login`.")
    return EXIT_USAGE_ERROR
```

**8c. Pass auth_token to `publish_flow()`** — modify line 2439 from:
```python
result = publish_flow(yaml_content, author=author, files=bundle_files)
```
to:
```python
result = publish_flow(yaml_content, author=author, files=bundle_files, auth_token=auth_token)
```

**8d. Pass auth_token to `update_flow()`** — modify line 2436 from:
```python
result = update_flow(slug, yaml_content)
```
to:
```python
result = update_flow(slug, yaml_content, auth_token=auth_token)
```

**8e. Enhance error handling** — modify the `except RegistryError` block at line 2447:
```python
except RegistryError as e:
    io.log("error", str(e))
    if e.status_code == 401:
        io.log("info", "Your session may have expired. Run `stepwise login` to re-authenticate.")
    return EXIT_USAGE_ERROR
```

### Step 9: Write tests — auth file management

**File**: `tests/test_auth.py` (new file)
**Depends on**: Step 1 (the functions being tested)

New file with class `TestAuthFileManagement`:

| Test | What it verifies | Key assertions |
|------|-----------------|----------------|
| `test_save_and_load_auth` | Round-trip save → load | `load_auth()` returns dict with all three keys matching saved values |
| `test_auth_file_permissions` | File mode is 0600 | `AUTH_FILE.stat().st_mode & 0o777 == 0o600` |
| `test_load_auth_missing` | Graceful when no file | `load_auth()` returns `None` |
| `test_load_auth_corrupt_json` | Resilience to corruption | Write invalid JSON to `AUTH_FILE`, `load_auth()` returns `None` |
| `test_clear_auth` | Deletes file | `save_auth(...)` then `clear_auth()`, `AUTH_FILE.exists()` is False |
| `test_clear_auth_when_missing` | No error on missing file | `clear_auth()` succeeds without raising |

Setup pattern (from `tests/test_registry.py:52-55`):
```python
monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", tmp_path / "auth.json")
monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
```

### Step 10: Write tests — Device Flow API functions

**File**: `tests/test_auth.py` (append)
**Depends on**: Step 2 (the functions being tested), Step 9 (file exists)

New class `TestDeviceFlowAPI`:

| Test | What it verifies | Mock setup | Key assertions |
|------|-----------------|------------|----------------|
| `test_initiate_device_flow` | POST to correct endpoint, parses response | Mock `_client()` returning 200 + JSON with `device_code`, `user_code`, `verification_uri`, `interval` | Return dict has all expected keys; `mock_client.post.call_args` URL ends with `/api/auth/device` |
| `test_initiate_device_flow_error` | Non-200 raises RegistryError | Mock returning 500 | `pytest.raises(RegistryError)` |
| `test_poll_device_flow_success` | Returns auth_token on success | Mock returning 200 + `{auth_token, github_username}` | Return dict contains `auth_token` |
| `test_poll_device_flow_pending` | Returns pending response without error | Mock returning 200 + `{error: "authorization_pending"}` | Return dict contains `error` key |
| `test_poll_sends_device_code` | Correct payload sent | Mock returning 200 | `mock_client.post.call_args.kwargs["json"]["device_code"] == "test-code"` |
| `test_verify_auth_success` | GET with Bearer header | Mock returning 200 + user info | `mock_client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"` |
| `test_verify_auth_401` | Raises RegistryError on 401 | Mock returning 401 | `pytest.raises(RegistryError, match="invalid or expired")` |

Mock pattern (from `tests/test_registry.py:89-106`): `MagicMock` with `__enter__`/`__exit__`, `monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)`.

### Step 11: Write tests — publish/update with auth

**File**: `tests/test_auth.py` (append)
**Depends on**: Steps 3-4 (modified `publish_flow`/`update_flow`)

New class `TestPublishWithAuth`:

| Test | What it verifies | Key assertions |
|------|-----------------|----------------|
| `test_publish_sends_auth_header` | Auth token included in request | `mock_client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-auth"` |
| `test_publish_no_header_when_no_auth` | No header when `auth_token=None` | `"headers" not in mock_client.post.call_args.kwargs` or headers is empty dict |
| `test_publish_401_raises` | 401 response raises with helpful message | `pytest.raises(RegistryError, match="Authentication required")` with `e.status_code == 401` |

New class `TestUpdateWithAuth`:

| Test | What it verifies | Key assertions |
|------|-----------------|----------------|
| `test_update_prefers_per_flow_token` | Per-flow token used when available | Mock `get_token` returning `"flow-tok"`, call with `auth_token="auth-tok"`. Header uses `"Bearer flow-tok"` |
| `test_update_falls_back_to_auth_token` | Auth token used when no per-flow token | Mock `get_token` returning `None`, call with `auth_token="auth-tok"`. Header uses `"Bearer auth-tok"` |
| `test_update_fails_when_no_tokens` | Raises when both missing | Mock `get_token` returning `None`, call with `auth_token=None`. `pytest.raises(RegistryError, match="Log in")` |

### Step 12: Write tests — CLI commands

**File**: `tests/test_auth.py` (append)
**Depends on**: Steps 5-8 (all CLI changes)

New class `TestCmdLogin`:

| Test | What it verifies | Setup | Key assertions |
|------|-----------------|-------|----------------|
| `test_login_already_authenticated` | Skips Device Flow if valid token | Mock `load_auth` → valid dict, mock `verify_auth` → success | Return `EXIT_SUCCESS`, `initiate_device_flow` never called |
| `test_login_re_auth_on_invalid_token` | Falls through if verify fails | Mock `load_auth` → dict, mock `verify_auth` → raises `RegistryError(status_code=401)`, mock device flow → success | `save_auth` called with new token |
| `test_login_full_device_flow` | Complete happy path | Mock `load_auth` → None, mock `initiate_device_flow` → device data, mock `poll_device_flow` → [pending, pending, success], mock `time.sleep` | `save_auth` called, return `EXIT_SUCCESS` |
| `test_login_expired` | Timeout error | Mock poll → `{error: "expired_token"}` | Return `EXIT_USAGE_ERROR` |
| `test_login_denied` | Access denied error | Mock poll → `{error: "access_denied"}` | Return `EXIT_USAGE_ERROR` |
| `test_login_network_error` | Registry unreachable | Mock `initiate_device_flow` → raises `RegistryError` | Return `EXIT_USAGE_ERROR` |

Approach for `cmd_login`/`cmd_logout` tests: call the function directly with a mock `argparse.Namespace`, monkeypatch the registry_client functions. Mock `time.sleep` to avoid actual delays: `monkeypatch.setattr("stepwise.cli.time.sleep", lambda _: None)` — but since `time` is imported inside the function body (`import time`), use `monkeypatch.setattr("time.sleep", lambda _: None)`.

New class `TestCmdLogout`:

| Test | What it verifies | Key assertions |
|------|-----------------|----------------|
| `test_logout_when_logged_in` | Removes auth file | Mock `load_auth` → dict, verify `clear_auth` called, return `EXIT_SUCCESS` |
| `test_logout_when_not_logged_in` | Graceful no-op | Mock `load_auth` → None, `clear_auth` not called, return `EXIT_SUCCESS` |

New class `TestCmdShareAuth`:

| Test | What it verifies | Setup | Key assertions |
|------|-----------------|-------|----------------|
| `test_share_requires_login` | Error when not authenticated | Mock `load_auth` → None, set up valid flow file | Return `EXIT_USAGE_ERROR`, output contains "stepwise login" |
| `test_share_sends_auth_token` | Token forwarded to publish | Mock `load_auth` → valid, mock `publish_flow` | `publish_flow` called with `auth_token="test-token"` |
| `test_share_update_passes_auth` | Token forwarded to update | Mock `load_auth` → valid, mock `update_flow`, `args.update=True` | `update_flow` called with `auth_token="test-token"` |
| `test_share_401_suggests_relogin` | Helpful hint on auth failure | Mock `publish_flow` → raises `RegistryError(status_code=401)` | Output contains "stepwise login" |

For `cmd_share` tests: requires more setup — need a valid flow file on disk, mock `resolve_flow`, mock `load_workflow_yaml`. Use `tmp_path` for a minimal `.flow.yaml` file.

## Testing Strategy

### Automated tests

```bash
# Run just the new auth tests
uv run pytest tests/test_auth.py -v

# Run registry tests to verify no regressions in token/publish/update
uv run pytest tests/test_registry.py -v

# Full test suite
uv run pytest tests/

# Web tests (no changes expected, but verify nothing broke)
cd web && npm run test
```

### Manual verification checklist

1. `stepwise login` — complete Device Flow against live registry, verify `~/.config/stepwise/auth.json` exists with correct content and `0600` permissions (`stat -c %a ~/.config/stepwise/auth.json`)
2. `stepwise login` (again) — verify "Already logged in as @{username}" without re-prompting
3. `stepwise share test-flow` — verify flow publishes with auth (check server logs for Bearer header)
4. `stepwise share test-flow --update` — verify update works with auth token
5. `stepwise logout` — verify `auth.json` removed, prints "Logged out."
6. `stepwise logout` (again) — verify "Not logged in."
7. `stepwise share test-flow` — verify "You need to log in first. Run `stepwise login`."
8. `stepwise --help` — verify `login` and `logout` appear in command list
9. `stepwise login --help` — verify help text

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|-----------|
| Registry auth endpoints not yet deployed or API contract differs | `stepwise login` fails at runtime with unexpected response format | Low (spec says server-side is done) | All tests mock the API. Before merging, do one live test against the registry. If API differs, only `registry_client.py` functions need updating — CLI logic is decoupled. |
| Existing `stepwise share` users get a hard break (must log in) | Users who previously published anonymously can't publish until they `stepwise login` | Medium (intentional behavior per spec) | The error message explicitly tells them to run `stepwise login`. Per-flow update tokens in `tokens.json` continue to work for `--update`, so existing flows can still be updated without login. |
| Network timeout during Device Flow polling | User appears stuck in terminal | Low | Three mitigations: (1) `time.time() < deadline` loop bound from server's `expires_in` (~900s), (2) `KeyboardInterrupt` handler prints "Login cancelled." and exits cleanly, (3) server-side `expired_token` response terminates the loop. |
| Auth file corruption (truncated write, invalid JSON) | `load_auth()` crashes with unhandled exception | Low | `load_auth()` wraps `json.loads` in try/except, returns `None` on any parse error. User can fix by running `stepwise login` again (overwrites file). |
| `time.sleep` mock in tests doesn't catch the import-inside-function pattern | Tests hang waiting for real sleep | Medium | Since `cmd_login` does `import time` then `time.sleep(interval)`, mock with `monkeypatch.setattr("time.sleep", lambda _: None)` which patches the module-level `time.sleep` that the local name resolves to. Verified: this is the standard monkeypatch pattern for builtins. |
| Adding `auth_token` param to `publish_flow` breaks existing callers | Other code that calls `publish_flow` breaks | Low | Parameter is optional with default `None`. Existing callers (`cli.py:2439`, `tests/test_registry.py:208`) pass positional/keyword args that don't conflict. `auth_token=None` means no header — backward compatible. |
