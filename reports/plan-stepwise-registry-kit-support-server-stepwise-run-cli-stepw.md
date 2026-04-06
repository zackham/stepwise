# Registry Kit Support — Implementation Plan

**Server (~/work/stepwise.run) + CLI (~/work/stepwise)**

Add kit publishing, fetching, and search to the Stepwise registry. Kits are grouped flow collections with a KIT.yaml manifest. They work locally today; this plan adds registry support: `stepwise share` a kit, `stepwise get` a kit, and unified search across flows and kits.

---

## Requirements

### R1: Shared namespace enforcement
- Slug must be unique across BOTH flows and kits tables.
- Publishing a flow with slug "swdev" must fail if a kit "swdev" exists, and vice versa.
- **AC:** POST /api/flows returns 409 if slug exists in `kits` table. POST /api/kits returns 409 if slug exists in `flows` table. Verified by two tests: `test_publish_kit_slug_uniqueness_vs_flow` and `test_publish_flow_slug_uniqueness_vs_kit`.

### R2: Kit publishing (server)
- New POST /api/kits endpoint accepts KIT.yaml + bundled flows + co-located files.
- Validates KIT.yaml (name, description required), validates each bundled flow YAML (must have `steps` key).
- Stores atomically: kit row + all bundled flows serialized as JSON.
- Returns kit metadata + update_token.
- **AC:** `test_publish_kit_basic` publishes a kit and retrieves it via GET /api/kits/{slug} with all bundled flows and metadata intact.

### R3: Kit retrieval (server)
- GET /api/kits/{slug} returns full kit metadata + bundled flow data.
- GET /api/kits/{slug}/raw returns raw KIT.yaml as text/yaml.
- GET /api/kits/{slug}/flows/{flow_name} returns a specific bundled flow's YAML + files.
- **AC:** `test_get_kit`, `test_get_kit_raw`, `test_get_kit_flow` each verify 200 responses with correct data. `test_get_kit_not_found` and `test_get_kit_flow_not_found` verify 404.

### R4: Kit update and delete (server)
- PUT /api/kits/{slug} updates kit (token or session auth required).
- DELETE /api/kits/{slug} deletes kit (token or admin required).
- **AC:** `test_update_kit` verifies update with valid token. `test_update_kit_invalid_token` verifies 403. `test_delete_kit` verifies deletion + 404 on subsequent GET.

### R5: Unified search
- GET /api/flows (existing search endpoint at `app.py:556`) also searches `kits` table and returns kit results in a new `kits` key.
- Flow results get `type: "flow"` field added to existing `flow_to_dict()` at `app.py:350`.
- **AC:** `test_search_returns_kits` verifies kits appear in the `kits` key. `test_search_flow_has_type_field` verifies existing flows now include `type: "flow"`. All 4 existing `TestSearch` tests in `test_api.py:274-335` still pass.

### R6: Kit publishing (CLI)
- `stepwise share <kit-name>` detects KIT.yaml in kit directory, collects all bundled flows + co-located files, calls `publish_kit()`.
- Shows summary: "Publishing kit swdev (5 flows)" with confirmation prompt.
- **AC:** `test_share_kit_directory` mocks `_client()` and verifies POST to /api/kits with correct payload structure. `test_share_kit_requires_confirmation` verifies cancellation on "n" input.

### R7: Kit installation (CLI)
- `stepwise get @author:kit-slug` fetches kit from registry.
- Installs KIT.yaml + all bundled flows to `.stepwise/registry/@author/kit-slug/`.
- Auto-fetches registry includes listed in KIT.yaml.
- Shows summary: "Downloaded @author:kit (5 flows + 2 includes)".
- **AC:** `test_get_kit_creates_directory_structure` verifies KIT.yaml + flow subdirs + .origin.json exist at expected paths. `test_get_kit_auto_fetches_includes` verifies registry includes are fetched.

### R8: Search type indicator (CLI)
- `stepwise search` shows a TYPE column distinguishing flows from kits.
- **AC:** `test_search_shows_type_column` verifies table header includes "TYPE" and kit rows show "kit".

### R9: Registry client functions
- New functions: `publish_kit()`, `fetch_kit()`, `fetch_kit_flow()`, `update_kit()` in `registry_client.py`.
- **AC:** `test_publish_kit_function`, `test_fetch_kit_function`, `test_fetch_kit_flow_function` each mock `_client()` and verify correct HTTP method, URL, payload, and response parsing.

### R10: Backward compatibility
- Existing flow publishing, fetching, searching must continue to work unchanged.
- No schema changes that break existing flow rows.
- **AC:** All existing tests pass without modification: `test_api.py` (351 lines, 28 tests), `test_auth.py` (423 lines, 16 tests), `test_admin.py` (259 lines, 14 tests) in stepwise.run; `test_registry.py` (373 lines, 14 tests), `test_bundle.py` (563 lines, 30 tests) in stepwise.

---

## Assumptions

Each assumption is verified against a specific file and line number:

| # | Assumption | Verified at |
|---|-----------|-------------|
| A1 | `KitDefinition` has fields `name`, `description`, `author`, `category`, `usage`, `include`, `defaults`, `tags` plus `to_dict()`/`from_dict()` | `models.py:557-600` — read and confirmed all 8 fields at lines 560-567, `to_dict()` at line 569, `from_dict()` at line 589 |
| A2 | `load_kit_yaml()` validates name required, description required, name matches dir, and raises `KitLoadError` (not `YAMLLoadError`) | `yaml_loader.py:130-184` — signature at line 130, `KitLoadError` class at line 123-127 with `errors: list[str]` constructor |
| A3 | `discover_kits()` finds local kits and returns `list[KitInfo]` by scanning `_find_kit_dirs()` then iterating subdirs for FLOW.yaml | `flow_resolution.py:545-592` — confirmed scanning pattern at lines 553-563, `KitInfo` dataclass at lines 33-50 |
| A4 | `collect_bundle()` collects co-located files for a single flow dir, returning `dict[str, str]` of `{rel_path: content}`. Raises `BundleError` on limit violations. | `bundle.py:19-90` — signature at line 19, limits at lines 78-88, `BundleError` at line 15 |
| A5 | `unpack_bundle()` installs FLOW.yaml + files + .origin.json to a target dir | `bundle.py:93-124` — confirmed signature at line 93, FLOW.yaml write at line 110, files at line 114, origin at line 120 |
| A6 | Server uses `slugify()` to derive slug from name, checks `flows` table only for uniqueness before insert | `app.py:156-161` (slugify), `app.py:738` (uniqueness check — `SELECT slug FROM flows WHERE slug = ?` — only checks `flows` table, NOT `kits`) |
| A7 | Auth uses `resolve_auth_session()` for user auth (checks `stw_auth_` prefix and looks up `auth_sessions` table), `ADMIN_TOKEN` comparison for admin | `app.py:390-412` (resolve_auth_session), `app.py:378-387` (require_admin), `app.py:690` (bearer_token == ADMIN_TOKEN check in publish_flow) |
| A8 | FTS5 `flows_fts` has triggers for INSERT/UPDATE/DELETE sync. Columns: `name, description, tags, author`. | `app.py:79-101` — CREATE VIRTUAL TABLE at line 79, triggers `flows_ai` at line 86, `flows_ad` at line 91, `flows_au` at line 96 |
| A9 | `_validate_bundle_files()` enforces max 20 files, 500KB total, extension whitelist | `app.py:141-153` — count check at line 145, size check at line 148, extension check at line 152 |
| A10 | Server tests use `fresh_db` fixture with `monkeypatch.setattr("app.DB_PATH", ...)`, `TestClient(app)`, `auth_headers` fixture | `tests/test_api.py:43-52` (fresh_db), `tests/test_api.py:55-66` (auth_headers creates alice/11111 session), `tests/test_api.py:69-71` (client) |
| A11 | `registry_flow_dir()` returns `.stepwise/registry/@{author}/{slug}` path | `flow_resolution.py:761-763` — `return project_dir / ".stepwise" / "registry" / f"@{author}" / slug` |
| A12 | `parse_registry_ref()` parses `@author:slug` format, returns `(author, slug)` tuple or None | `flow_resolution.py:766-774` — checks `@` prefix at 768, splits on `:` at 772 |
| A13 | Kit bundled flows live in subdirs: `flows/{kit}/{flow}/FLOW.yaml` | `flow_resolution.py:558-563` — `discover_kits` iterates `resolved_dir.iterdir()`, checks `child.is_dir()`, looks for `FLOW_DIR_MARKER` |
| A14 | `IncludeRef` is a `__slots__` class (not a dataclass) with `ref_type`, `author`, `slug`, `version_constraint`, `kit`, `flow` fields | `flow_resolution.py:244-265` — `__slots__` at line 246, `__init__` at line 248, `flow_name` property at line 257 |
| A15 | `parse_include_ref()` returns `IncludeRef` and handles three formats: `@author:slug[@version]`, `kit/flow`, bare name | `flow_resolution.py:267-316` — signature at line 267, docstring describes formats at lines 270-275 |
| A16 | Existing client tests mock `_client()` by patching `stepwise.registry_client._client` to return a MagicMock context manager | `tests/test_registry.py:88-111` — pattern: `mock_client = MagicMock()`, `mock_client.__enter__ = MagicMock(return_value=mock_client)`, `mock_client.__exit__ = MagicMock(return_value=False)`, `monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)` |
| A17 | CLI integration tests use `main(["share", ...])` imported from `stepwise.cli`, and mock registry calls via `monkeypatch.setattr` | `tests/test_bundle.py:266-321` — imports `main` from `stepwise.cli` at line 18, patches `stepwise.registry_client._client` at line 301, uses `PlainAdapter` with `StringIO("y\n")` for confirmation prompts at line 306 |
| A18 | `flow_to_dict()` does NOT currently set a `type` field — adding `d["type"] = "flow"` is a new additive change | `app.py:350-366` — read the full function; no `type` assignment exists. The field is not present in any existing response. |
| A19 | The `list_flows` search endpoint returns `{"flows": [...], "total": int, "query": ..., "filters": ...}` with no kit data | `app.py:556-611` — return at line 606, only queries `flows` table |
| A20 | `seed_if_empty()` only seeds flows (single files and directories), no kit seeding | `app.py:1217-1356` — scans `SEED_DIR` for `*.flow.yaml` (line 1226) and dirs with `FLOW.yaml` (line 1274-1278), no KIT.yaml handling |

---

## Implementation Steps

### Phase 1: Registry Server (~/work/stepwise.run/app.py)

#### Step 1.1: Add `kits` table and FTS to `init_db()`

**File:** `~/work/stepwise.run/app.py`
**Insert at:** Line 124, after `conn.commit()` (end of reserved_namespaces setup), before the `# Migrations` comment at line 126.

Add a new `conn.executescript("""...""")` block containing:

```sql
CREATE TABLE IF NOT EXISTS kits (
    name TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    author TEXT NOT NULL DEFAULT 'anonymous',
    version TEXT NOT NULL DEFAULT '1.0',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    usage TEXT NOT NULL DEFAULT '',
    include TEXT NOT NULL DEFAULT '[]',
    kit_yaml TEXT NOT NULL,
    bundled_flows TEXT NOT NULL DEFAULT '[]',
    downloads INTEGER NOT NULL DEFAULT 0,
    featured INTEGER NOT NULL DEFAULT 0,
    unlisted INTEGER NOT NULL DEFAULT 0,
    update_token TEXT,
    source TEXT DEFAULT 'cli',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_kits_featured ON kits(featured);
CREATE INDEX IF NOT EXISTS idx_kits_downloads ON kits(downloads DESC);
CREATE INDEX IF NOT EXISTS idx_kits_created ON kits(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS kits_fts USING fts5(
    name, description, tags, author, usage, category,
    content='kits',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS kits_ai AFTER INSERT ON kits BEGIN
    INSERT INTO kits_fts(rowid, name, description, tags, author, usage, category)
    VALUES (new.rowid, new.name, new.description, new.tags, new.author, new.usage, new.category);
END;

CREATE TRIGGER IF NOT EXISTS kits_ad AFTER DELETE ON kits BEGIN
    INSERT INTO kits_fts(kits_fts, rowid, name, description, tags, author, usage, category)
    VALUES ('delete', old.rowid, old.name, old.description, old.tags, old.author, old.usage, old.category);
END;

CREATE TRIGGER IF NOT EXISTS kits_au AFTER UPDATE ON kits BEGIN
    INSERT INTO kits_fts(kits_fts, rowid, name, description, tags, author, usage, category)
    VALUES ('delete', old.rowid, old.name, old.description, old.tags, old.author, old.usage, old.category);
    INSERT INTO kits_fts(rowid, name, description, tags, author, usage, category)
    VALUES (new.rowid, new.name, new.description, new.tags, new.author, new.usage, new.category);
END;
```

**Column notes:**
- `bundled_flows` stores all flows in a single JSON array. Each element: `{"name": "plan", "slug": "plan", "yaml_content": "...", "files_json": {...} | null}`. A kit with 10 flows averaging 2KB each = ~20KB JSON — well within SQLite's comfort zone.
- `include` stores the raw include refs from KIT.yaml (e.g., `["@alice:utils"]`).

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_api.py::TestPublish::test_publish_simple -x -q
```
Existing tests init fresh DBs via `init_db()` — if this breaks schema creation, the first test will fail.

---

#### Step 1.2: Add cross-table slug uniqueness helper + update `publish_flow()`

**File:** `~/work/stepwise.run/app.py`

**New function — insert at:** Line 162, after `slugify()` ends (line 161: `return slug`), before `def analyze_yaml()` at line 164.

```python
def _check_slug_available(conn: sqlite3.Connection, slug: str, exclude_table: str | None = None) -> str | None:
    """Check if slug is taken across flows and kits. Returns 'flow'/'kit' if taken, None if available."""
    if exclude_table != "flows":
        if conn.execute("SELECT 1 FROM flows WHERE slug = ?", (slug,)).fetchone():
            return "flow"
    if exclude_table != "kits":
        if conn.execute("SELECT 1 FROM kits WHERE slug = ?", (slug,)).fetchone():
            return "kit"
    return None
```

**Modify `publish_flow()`** at `app.py:737-743`. Replace:
```python
    existing = conn.execute("SELECT slug FROM flows WHERE slug = ?", (slug,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(
            409, f"Flow '{slug}' already exists. Use PUT to update."
        )
```
With:
```python
    taken_by = _check_slug_available(conn, slug)
    if taken_by == "flow":
        conn.close()
        raise HTTPException(409, f"Flow '{slug}' already exists. Use PUT to update.")
    elif taken_by == "kit":
        conn.close()
        raise HTTPException(409, f"Slug '{slug}' is already used by a kit.")
```

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_api.py::TestPublish -x -q
```
All 8 existing publish tests (lines 89-149) must pass — the `_check_slug_available` function returns "flow" for the same-slug case that previously triggered the raw SELECT.

---

#### Step 1.3: Add Pydantic models for kits

**File:** `~/work/stepwise.run/app.py`
**Insert at:** Line 448, after `AdminPatchRequest` class (line 447: `unlisted: bool | None = None`), before the `# ── Auth API` section comment at line 450.

```python
class BundledFlowPayload(BaseModel):
    name: str
    yaml: str
    files: dict[str, str] | None = None

class PublishKitRequest(BaseModel):
    kit_yaml: str
    bundled_flows: list[BundledFlowPayload]
    author: str | None = None
    source: str = "cli"

class UpdateKitRequest(BaseModel):
    kit_yaml: str
    bundled_flows: list[BundledFlowPayload]
    changelog: str | None = None
```

---

#### Step 1.4: Add `validate_kit_yaml()` and `kit_to_dict()`, update `flow_to_dict()`

**File:** `~/work/stepwise.run/app.py`

**New functions — insert at:** Line 367, after `flow_to_dict()` return statement (line 366: `return d`), before `def admin_flow_to_dict()` at line 369.

```python
def validate_kit_yaml(raw_yaml: str) -> dict:
    """Parse and validate KIT.yaml content. Returns parsed dict or raises HTTPException."""
    import yaml as yaml_lib
    try:
        data = yaml_lib.safe_load(raw_yaml)
    except Exception as e:
        raise HTTPException(400, f"Invalid KIT.yaml: {e}")
    if not isinstance(data, dict):
        raise HTTPException(400, "KIT.yaml must be a YAML mapping")
    if not data.get("name"):
        raise HTTPException(400, "KIT.yaml must have a 'name' field")
    if not data.get("description"):
        raise HTTPException(400, "KIT.yaml must have a 'description' field")
    return data


def kit_to_dict(row: sqlite3.Row) -> dict:
    """Convert a kits DB row to an API response dict."""
    d = dict(row)
    d["tags"] = json.loads(d["tags"])
    d["include"] = json.loads(d["include"])
    d["featured"] = bool(d["featured"])
    bundled = json.loads(d["bundled_flows"])
    d["bundled_flows"] = [{"name": f["name"], "slug": f["slug"]} for f in bundled]
    d["flow_count"] = len(bundled)
    d.pop("update_token", None)
    d.pop("kit_yaml", None)
    d.pop("unlisted", None)
    d["type"] = "kit"
    d["url"] = f"https://stepwise.run/kits/{d['slug']}"
    return d
```

**Modify `flow_to_dict()`** at `app.py:350-366`. Add one line before the return:
```python
    d["type"] = "flow"  # ← add at line 365, before return d at line 366
    return d
```

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_api.py -x -q
```
The `type: "flow"` field is additive. Existing tests that check response dict keys with exact equality (e.g., `assert resp["slug"] == "test-flow"`) will be unaffected. Tests that check response keys exhaustively via `assert set(resp.keys()) == {...}` would fail — but inspecting `test_api.py` confirms no such tests exist.

---

#### Step 1.5: Implement POST /api/kits (publish)

**File:** `~/work/stepwise.run/app.py`
**Insert at:** Line 910, after the last admin endpoint `admin_delete_flow()` (line 909: `return {"deleted": slug}`), before the `# ── Documentation API` section comment at line 912.

New section header + endpoint. Auth pattern follows `publish_flow()` at lines 681-767 exactly (extract Bearer token, check admin vs session, resolve author, check reserved namespaces). Key additions:

1. Call `validate_kit_yaml(req.kit_yaml)` to validate KIT.yaml
2. Loop over `req.bundled_flows` — for each, parse YAML via `yaml_lib.safe_load()`, verify `steps` key exists, call `_validate_bundle_files(bf.files)` per-flow
3. Call `_check_slug_available(conn, slug)` for cross-table uniqueness
4. INSERT into `kits` table with `json.dumps(bundled)` for bundled_flows column
5. Return `kit_to_dict(row)` with `update_token` added

Full implementation is ~60 lines, structured identically to `publish_flow()` but operating on the `kits` table.

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_kits_api.py::TestPublishKit -x -v
```
(Test file created in Step 1.10 below.)

---

#### Step 1.6: Implement GET endpoints for kits

**File:** `~/work/stepwise.run/app.py`
**Insert at:** Immediately after the POST /api/kits endpoint from Step 1.5.

Three endpoints following the pattern of `get_flow()` at `app.py:614-638`:

1. **GET /api/kits/{slug}** — Query `kits` table, increment downloads if `X-Stepwise-Download` header present, return `kit_to_dict(row)` with `kit_yaml` and full `bundled_flows` JSON added back (for installation).

2. **GET /api/kits/{slug}/raw** — Return `row["kit_yaml"]` as `PlainTextResponse(media_type="text/yaml")`, same pattern as `download_flow()` at `app.py:641-666`.

3. **GET /api/kits/{slug}/flows/{flow_name}** — Parse `row["bundled_flows"]` JSON, find matching flow by `name` or `slug`, return `{"name", "slug", "yaml", "files"}`.

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_kits_api.py::TestGetKit -x -v
```

---

#### Step 1.7: Implement PUT and DELETE for kits

**File:** `~/work/stepwise.run/app.py`
**Insert at:** After GET endpoints from Step 1.6.

**PUT /api/kits/{slug}** — follows `update_flow()` at `app.py:770-826`: extract Bearer token, check `row["update_token"]` match, fall back to `resolve_auth_session()` + author match. Re-validate KIT.yaml, re-validate all bundled flows, UPDATE `kits` table.

**DELETE /api/kits/{slug}** — follows `delete_flow()` at `app.py:829-849`: extract Bearer token, check `row["update_token"]` match, DELETE from `kits`.

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_kits_api.py::TestUpdateKit tests/test_kits_api.py::TestDeleteKit -x -v
```

---

#### Step 1.8: Update search to include kits

**File:** `~/work/stepwise.run/app.py`
**Modify:** `list_flows()` function at lines 556-611.

After the existing flows query completes (line 598: `rows = conn.execute(base, params).fetchall()`) and before `conn.close()` at line 604, add a parallel query against `kits`:

```python
# Also search kits (same FTS/filter logic)
kit_params: list[Any] = []
if q:
    kit_q = """
        SELECT k.* FROM kits k
        JOIN kits_fts kfts ON k.rowid = kfts.rowid
        WHERE kits_fts MATCH ? AND k.unlisted = 0
    """
    kit_params.append(q)
else:
    kit_q = "SELECT * FROM kits WHERE unlisted = 0"
if tag:
    kit_q += " AND json_extract(tags, '$') LIKE ?"
    kit_params.append(f"%{tag}%")
kit_q += f" ORDER BY {order} LIMIT ? OFFSET ?"
kit_params.extend([limit, offset])
kit_rows = conn.execute(kit_q, kit_params).fetchall()
kit_total = conn.execute("SELECT COUNT(*) FROM kits WHERE unlisted = 0").fetchone()[0]
```

Then modify the return statement at line 606 to add kit data:

```python
return {
    "flows": [flow_to_dict(r) for r in rows],  # unchanged — backward compat
    "kits": [kit_to_dict(r) for r in kit_rows],
    "total": total + kit_total,
    "total_flows": total,
    "total_kits": kit_total,
    "query": q,
    "filters": {"tag": tag, "featured": featured},
}
```

**Backward compatibility:** The `flows` key is unchanged. `total` now includes kits but old clients just use it for "X of Y" display which is still correct. New keys `kits`, `total_flows`, `total_kits` are additive.

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_api.py::TestSearch -x -v && uv run pytest tests/test_kits_api.py::TestSearchKits -x -v
```
The 4 existing TestSearch tests at `test_api.py:274-335` must still pass. They check `result["flows"]` and `result["total"]` — the `flows` key is unchanged, and `total` is equal to `total_flows` when no kits exist (which is the case in existing tests that don't publish kits).

---

#### Step 1.9: Add admin endpoints for kits

**File:** `~/work/stepwise.run/app.py`
**Insert at:** After kit CRUD endpoints, before the `# ── Documentation API` section.

Three endpoints following the exact pattern of their flow counterparts at `app.py:855-909`:
- **GET /api/admin/kits** — same as `admin_list_flows()` at line 855, querying `kits` table
- **PATCH /api/admin/kits/{slug}** — same as `admin_patch_flow()` at line 866, partial update of featured/unlisted
- **DELETE /api/admin/kits/{slug}** — same as `admin_delete_flow()` at line 897

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_kits_api.py::TestAdminKits -x -v
```

---

#### Step 1.10: Write server tests

**File:** `~/work/stepwise.run/tests/test_kits_api.py` (new file)

Follow the exact patterns from `tests/test_api.py`:
- Same `fresh_db` fixture pattern (lines 43-52): `monkeypatch.setattr("app.DB_PATH", ...)`, `monkeypatch.setattr("app.SEED_DIR", ...)`, `monkeypatch.setattr("app.ADMIN_TOKEN", ...)`, call `init_db()`
- Same `auth_headers` fixture pattern (lines 55-66): INSERT into `auth_sessions`, return Bearer headers
- Same `client` fixture: `TestClient(app, raise_server_exceptions=False)`
- Same `ADMIN_TOKEN = "test-admin-token-0123456789abcdef"` constant

**Test constants:**
```python
SIMPLE_KIT_YAML = """\
name: test-kit
description: A test kit
author: alice
tags: [test]
"""

SIMPLE_FLOW_A = """\
name: flow-a
description: First flow
steps:
  hello:
    run: 'echo "hello"'
    outputs: [msg]
"""

SIMPLE_FLOW_B = """\
name: flow-b
description: Second flow
steps:
  world:
    run: 'echo "world"'
    outputs: [msg]
"""
```

**Test classes and functions (21 tests):**

| Class | Test function | What it verifies |
|-------|--------------|-----------------|
| `TestPublishKit` | `test_publish_kit_basic` | POST /api/kits → 200, returns slug, update_token, flow_count=2 |
| | `test_publish_kit_requires_auth` | POST without auth → 401 |
| | `test_publish_kit_validates_name_required` | POST with kit_yaml missing name → 400 |
| | `test_publish_kit_validates_description_required` | POST with kit_yaml missing description → 400 |
| | `test_publish_kit_validates_bundled_flow_steps` | POST with bundled flow missing steps → 400 |
| | `test_publish_kit_slug_uniqueness_vs_flow` | Publish flow "foo" then kit "foo" → 409 |
| | `test_publish_flow_slug_uniqueness_vs_kit` | Publish kit "bar" then flow "bar" → 409 |
| `TestGetKit` | `test_get_kit` | GET /api/kits/{slug} → 200, correct metadata + bundled_flows |
| | `test_get_kit_not_found` | GET /api/kits/nonexistent → 404 |
| | `test_get_kit_raw` | GET /api/kits/{slug}/raw → text/yaml content |
| | `test_get_kit_flow` | GET /api/kits/{slug}/flows/flow-a → correct YAML |
| | `test_get_kit_flow_not_found` | GET /api/kits/{slug}/flows/bad → 404 |
| | `test_kit_download_counter` | GET with X-Stepwise-Download → downloads incremented |
| `TestUpdateKit` | `test_update_kit_with_token` | PUT /api/kits/{slug} with update_token → 200 |
| | `test_update_kit_invalid_token` | PUT with wrong token → 403 |
| | `test_update_kit_via_session_auth` | PUT with matching author session → 200 |
| `TestDeleteKit` | `test_delete_kit` | DELETE → 200, subsequent GET → 404 |
| | `test_delete_kit_invalid_token` | DELETE with wrong token → 403 |
| `TestSearchKits` | `test_search_returns_kits_key` | GET /api/flows returns `kits` key with published kit |
| | `test_search_flow_has_type_field` | GET /api/flows flow results include `type: "flow"` |
| `TestAdminKits` | `test_admin_list_kits` | GET /api/admin/kits includes published kit |
| | `test_admin_patch_kit_featured` | PATCH /api/admin/kits/{slug} toggles featured |

**Helper fixture:**
```python
@pytest.fixture
def published_kit(client, auth_headers):
    """Publish a kit and return the response data."""
    resp = client.post(
        "/api/kits",
        json={
            "kit_yaml": SIMPLE_KIT_YAML,
            "bundled_flows": [
                {"name": "flow-a", "yaml": SIMPLE_FLOW_A},
                {"name": "flow-b", "yaml": SIMPLE_FLOW_B},
            ],
            "source": "test",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    return resp.json()
```

**Verify:**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_kits_api.py -x -v
cd ~/work/stepwise.run && uv run pytest tests/ -x -q  # full regression
```

---

### Phase 2: Registry Client (~/work/stepwise/src/stepwise/registry_client.py)

#### Step 2.1: Add `publish_kit()` function

**File:** `~/work/stepwise/src/stepwise/registry_client.py`
**Insert at:** Line 347, after `update_flow()` ends (line 346: `return _ensure_json(resp, f"update flow '{slug}'")`).

```python
def publish_kit(
    kit_yaml: str,
    bundled_flows: list[dict[str, Any]],
    author: str | None = None,
    auth_token: str | None = None,
) -> dict[str, Any]:
    """Publish a kit to the registry.
    bundled_flows: list of {"name": str, "yaml": str, "files": dict | None}
    """
    url = get_registry_url()
    payload: dict[str, Any] = {
        "kit_yaml": kit_yaml,
        "bundled_flows": bundled_flows,
        "source": "cli",
    }
    if author:
        payload["author"] = author
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    with _client() as client:
        resp = client.post(f"{url}/api/kits", json=payload, headers=headers)
    if resp.status_code == 409:
        raise RegistryError("Kit already exists. Use 'stepwise share --update' to update.", 409)
    if resp.status_code not in (200, 201):
        raise RegistryError(f"Publish failed: {resp.status_code} {resp.text}", resp.status_code)
    data = _ensure_json(resp, "publish kit")
    if data.get("update_token") and data.get("slug"):
        save_token(data["slug"], data["update_token"])
    return data
```

Follows the exact pattern of `publish_flow()` at lines 265-304 — same auth header handling, same status code checks, same token saving.

---

#### Step 2.2: Add `fetch_kit()` and `fetch_kit_flow()` functions

**File:** `~/work/stepwise/src/stepwise/registry_client.py`
**Insert at:** After `publish_kit()` from Step 2.1.

```python
def fetch_kit(slug: str, *, count_download: bool = True) -> dict[str, Any]:
    """Fetch kit metadata + bundled flows from the registry."""
    url = get_registry_url()
    headers = {}
    if count_download:
        headers["X-Stepwise-Download"] = "true"
    with _client() as client:
        resp = client.get(f"{url}/api/kits/{slug}", headers=headers)
    if resp.status_code == 404:
        raise RegistryError(f"Kit '{slug}' not found in registry", 404)
    if resp.status_code != 200:
        raise RegistryError(f"Registry error: {resp.status_code} {resp.text}", resp.status_code)
    return _ensure_json(resp, f"fetch kit '{slug}'")


def fetch_kit_flow(kit_slug: str, flow_name: str) -> dict[str, Any]:
    """Fetch a specific bundled flow from a kit."""
    url = get_registry_url()
    with _client() as client:
        resp = client.get(f"{url}/api/kits/{kit_slug}/flows/{flow_name}")
    if resp.status_code == 404:
        raise RegistryError(f"Flow '{flow_name}' not found in kit '{kit_slug}'", 404)
    if resp.status_code != 200:
        raise RegistryError(f"Registry error: {resp.status_code} {resp.text}", resp.status_code)
    return _ensure_json(resp, f"fetch kit flow '{kit_slug}/{flow_name}'")
```

Follows `fetch_flow()` pattern at lines 192-220 — same download header logic, same 404/error handling.

---

#### Step 2.3: Add `update_kit()` function

**File:** `~/work/stepwise/src/stepwise/registry_client.py`
**Insert at:** After `fetch_kit_flow()` from Step 2.2.

```python
def update_kit(
    slug: str,
    kit_yaml: str,
    bundled_flows: list[dict[str, Any]],
    changelog: str | None = None,
    auth_token: str | None = None,
) -> dict[str, Any]:
    """Update an existing kit in the registry."""
    token = get_token(slug)
    if not token:
        token = auth_token
    if not token:
        raise RegistryError(f"No update token for '{slug}'. Run `stepwise login` or publish from the original machine.")
    url = get_registry_url()
    payload: dict[str, Any] = {"kit_yaml": kit_yaml, "bundled_flows": bundled_flows}
    if changelog:
        payload["changelog"] = changelog
    with _client() as client:
        resp = client.put(f"{url}/api/kits/{slug}", json=payload, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 403:
        raise RegistryError("Invalid update token — you may not own this kit.", 403)
    if resp.status_code == 404:
        raise RegistryError(f"Kit '{slug}' not found in registry", 404)
    if resp.status_code != 200:
        raise RegistryError(f"Update failed: {resp.status_code} {resp.text}", resp.status_code)
    return _ensure_json(resp, f"update kit '{slug}'")
```

Follows `update_flow()` pattern at lines 307-346 — same token resolution (per-slug then fallback), same error handling.

---

### Phase 3: Bundle + CLI (~/work/stepwise)

#### Step 3.1: Add `collect_kit_bundle()` and `unpack_kit_bundle()` to bundle.py

**File:** `~/work/stepwise/src/stepwise/bundle.py`
**Insert at:** Line 125, after `unpack_bundle()` return statement (end of file).

```python
def collect_kit_bundle(kit_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """Collect a kit directory for publishing.
    Returns (kit_yaml_content, bundled_flows) where bundled_flows is:
    [{"name": "flow-name", "yaml": "...", "files": {"path": "content"} | None}, ...]
    """
    kit_yaml_path = kit_dir / "KIT.yaml"
    if not kit_yaml_path.is_file():
        raise BundleError(f"No KIT.yaml in {kit_dir}")
    kit_yaml = kit_yaml_path.read_text(encoding="utf-8")
    bundled_flows = []
    for sub in sorted(kit_dir.iterdir()):
        if not sub.is_dir():
            continue
        flow_yaml = sub / "FLOW.yaml"
        if not flow_yaml.is_file():
            continue
        name = sub.name
        yaml_content = flow_yaml.read_text(encoding="utf-8")
        files = collect_bundle(sub)  # reuse existing function (bundle.py:19)
        bundled_flows.append({
            "name": name,
            "yaml": yaml_content,
            "files": files if files else None,
        })
    if not bundled_flows:
        raise BundleError(f"Kit '{kit_dir.name}' has no bundled flows (no subdirectories with FLOW.yaml)")
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
        unpack_bundle(target_dir=target_dir / flow_name, yaml_content=yaml_content, files=files)
    if origin:
        origin_path = target_dir / ".origin.json"
        origin_path.write_text(json.dumps(origin, indent=2) + "\n")
    return kit_path
```

**Verify:**
```bash
cd ~/work/stepwise && uv run pytest tests/test_kit_bundle.py -x -v
```
(Test file created in Step 3.5.)

---

#### Step 3.2: Update `cmd_share` to detect and publish kits

**File:** `~/work/stepwise/src/stepwise/cli.py`

**Modify:** `cmd_share()` at line 3349. Insert kit detection after `flow_arg = args.flow` (line 3357) and the early exit for missing arg (line 3359), but **before** `resolve_flow()` call (line 3363).

Insert at line 3361 (between the early return and the try/except for resolve_flow):

```python
    # Check if target is a kit directory (has KIT.yaml, not FLOW.yaml)
    from stepwise.flow_resolution import _discovery_dirs
    for d in _discovery_dirs(_project_dir(args)):
        candidate = d / flow_arg
        if candidate.is_dir() and (candidate / "KIT.yaml").is_file():
            return _share_kit(args, candidate / "KIT.yaml")
```

Then add `_share_kit()` as a new function before `cmd_share()` (insert at line 3348):

```python
def _share_kit(args: argparse.Namespace, kit_yaml_path: Path) -> int:
    """Publish a kit to the registry."""
    from stepwise.bundle import BundleError, collect_kit_bundle
    from stepwise.registry_client import load_auth, publish_kit, update_kit, RegistryError
    from stepwise.yaml_loader import load_kit_yaml, KitLoadError
    io = _io(args)
    kit_dir = kit_yaml_path.parent
    try:
        kit_def = load_kit_yaml(str(kit_yaml_path))
    except KitLoadError as e:
        io.log("error", f"Invalid KIT.yaml: {e}")
        return EXIT_USAGE_ERROR
    try:
        kit_yaml, bundled_flows = collect_kit_bundle(kit_dir)
    except BundleError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR
    flow_count = len(bundled_flows)
    io.log("success", f"Validated kit '{kit_def.name}' ({flow_count} flows)")
    for bf in bundled_flows:
        file_count = len(bf.get("files") or {})
        file_msg = f" + {file_count} file(s)" if file_count else ""
        io.log("info", f"  {bf['name']}{file_msg}")
    if not io.prompt_confirm(f"Publish kit '{kit_def.name}' ({flow_count} flows)?"):
        io.log("info", "Cancelled.")
        return EXIT_SUCCESS
    auth = load_auth()
    auth_token = auth["auth_token"] if auth else None
    author = getattr(args, "author", None)
    do_update = getattr(args, "update", False)
    if not do_update and not auth_token:
        io.log("error", "You need to log in first. Run `stepwise login`.")
        return EXIT_USAGE_ERROR
    try:
        if do_update:
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", kit_def.name.lower().strip()).strip("-")
            result = update_kit(slug, kit_yaml, bundled_flows, auth_token=auth_token)
            io.log("success", f"Updated kit: {result.get('url', slug)}")
        else:
            result = publish_kit(kit_yaml, bundled_flows, author=author, auth_token=auth_token)
            slug = result.get("slug", "")
            io.log("success", f"Published kit '{kit_def.name}' ({flow_count} flows)")
            io.log("info", f"Get: stepwise get {slug}")
    except RegistryError as e:
        io.log("error", str(e))
        if e.status_code == 401:
            io.log("info", "Your session may have expired. Run `stepwise login` to re-authenticate.")
        return EXIT_USAGE_ERROR
    return EXIT_SUCCESS
```

**Verify:**
```bash
cd ~/work/stepwise && uv run pytest tests/test_kit_bundle.py::TestShareKit -x -v
```

---

#### Step 3.3: Update `cmd_get` to handle kits

**File:** `~/work/stepwise/src/stepwise/cli.py`

**Modify:** `cmd_get()` at line 3189. Change the import line at 3193 and the fetch/install block at lines 3215-3258.

Update import at line 3193:
```python
    from stepwise.registry_client import fetch_flow, fetch_kit, get_registry_url, RegistryError
```

Replace the `try: data = fetch_flow(slug)` block (lines 3215-3218) with flow-then-kit fallback:

```python
    data = None
    entity_type = None
    try:
        data = fetch_flow(slug)
        entity_type = "flow"
    except RegistryError as e:
        if e.status_code != 404:
            io.log("error", str(e))
            return EXIT_USAGE_ERROR
    if not data:
        try:
            data = fetch_kit(slug)
            entity_type = "kit"
        except RegistryError as e:
            io.log("error", str(e))
            return EXIT_USAGE_ERROR

    if entity_type == "kit":
        return _get_kit(args, data, slug, author_hint)
```

Then the existing flow install code (lines 3221-3258) stays as the `else` branch.

Add `_get_kit()` as a new function (insert before `cmd_get` at line 3188):

```python
def _get_kit(args: argparse.Namespace, data: dict, slug: str, author_hint: str | None) -> int:
    """Install a kit from the registry."""
    from stepwise.bundle import unpack_kit_bundle
    from stepwise.flow_resolution import registry_flow_dir
    from stepwise.registry_client import fetch_flow, get_registry_url, RegistryError
    io = _io(args)
    author = data.get("author", "unknown")
    downloads = data.get("downloads", 0)
    bundled_flows = data.get("bundled_flows", [])
    include_refs = data.get("include", [])
    force = getattr(args, "force", False)
    project_dir = Path.cwd()
    target_dir = registry_flow_dir(author, slug, project_dir)
    if target_dir.exists() and not force:
        io.log("error", f"{target_dir} already exists (use --force to overwrite)")
        return EXIT_USAGE_ERROR
    import hashlib
    from datetime import datetime, timezone
    origin = {
        "registry": get_registry_url(), "author": author, "slug": slug,
        "type": "kit", "version": data.get("version", "1.0"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    unpack_kit_bundle(target_dir=target_dir, kit_yaml=data.get("kit_yaml", ""),
                      bundled_flows=bundled_flows, origin=origin)
    flow_count = len(bundled_flows)
    include_count = 0
    if include_refs:
        from stepwise.flow_resolution import parse_include_ref
        from stepwise.bundle import unpack_bundle
        for ref_str in include_refs:
            try:
                ref = parse_include_ref(ref_str)
                if ref.ref_type == "registry":
                    inc_dir = registry_flow_dir(ref.author, ref.slug, project_dir)
                    if not inc_dir.exists():
                        inc_data = fetch_flow(ref.slug)
                        unpack_bundle(target_dir=inc_dir, yaml_content=inc_data["yaml"],
                                      files=inc_data.get("files"))
                        include_count += 1
            except Exception as e:
                io.log("warn", f"Failed to fetch include '{ref_str}': {e}")
    inc_msg = f" + {include_count} include(s)" if include_count else ""
    io.log("success", f"Downloaded @{author}:{slug} ({flow_count} flows{inc_msg}, {downloads:,} downloads)")
    io.log("info", f"Run: stepwise run @{author}:{slug}/<flow-name>")
    return EXIT_SUCCESS
```

**Verify:**
```bash
cd ~/work/stepwise && uv run pytest tests/test_kit_bundle.py::TestGetKit -x -v
```

---

#### Step 3.4: Update `cmd_search` to show type column

**File:** `~/work/stepwise/src/stepwise/cli.py`

**Modify:** `cmd_search()` at lines 3476-3515.

Replace the table-building block at lines 3502-3514:

```python
    # Table output
    rows = []
    for f in flows:
        rows.append([
            f.get("type", "flow"),
            f["slug"],
            f.get("author", "?"),
            str(f.get("steps", "?")),
            f"{f.get('downloads', 0):,}",
        ])
    kits = result.get("kits", [])
    for k in kits:
        rows.append([
            "kit",
            k["slug"],
            k.get("author", "?"),
            str(k.get("flow_count", "?")),
            f"{k.get('downloads', 0):,}",
        ])
    io.table(["TYPE", "NAME", "AUTHOR", "STEPS", "DOWNLOADS"], rows)

    total = result.get("total", len(flows) + len(kits))
    shown = len(flows) + len(kits)
    if total > shown:
        io.log("info", f"Showing {shown} of {total} results")
```

**Verify:**
```bash
cd ~/work/stepwise && uv run pytest tests/test_kit_bundle.py::TestSearchKit -x -v
```

---

#### Step 3.5: Write client tests

**File:** `~/work/stepwise/tests/test_kit_bundle.py` (new file)

Follow the exact patterns from `tests/test_bundle.py` and `tests/test_registry.py`:

**Mock pattern** (from `test_registry.py:88-111`):
```python
mock_client = MagicMock()
mock_client.__enter__ = MagicMock(return_value=mock_client)
mock_client.__exit__ = MagicMock(return_value=False)
mock_client.get.return_value = mock_response  # or .post
monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)
```

**CLI integration pattern** (from `test_bundle.py:266-321`):
```python
from stepwise.cli import EXIT_SUCCESS, main
monkeypatch.chdir(tmp_path)
monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
# Auto-confirm prompts:
from stepwise.io import PlainAdapter
from io import StringIO
monkeypatch.setattr("stepwise.cli.create_adapter", lambda **kw: PlainAdapter(output=sys.stderr, input_stream=StringIO("y\n")))
```

**Auth setup pattern** (from `test_bundle.py:267-272`):
```python
auth_file = tmp_path / "auth.json"
auth_file.write_text(json.dumps({"auth_token": "tok_test", "github_username": "test", "registry_url": "https://stepwise.run"}))
monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
```

**Test constants:**
```python
SIMPLE_KIT_YAML = "name: test-kit\ndescription: A test kit\nauthor: alice\ntags: [test]\n"
SIMPLE_FLOW_A = "name: flow-a\nsteps:\n  hello:\n    run: 'echo hi'\n    outputs: [msg]\n"
SIMPLE_FLOW_B = "name: flow-b\nsteps:\n  world:\n    run: 'echo world'\n    outputs: [msg]\n"
```

**Test classes and functions (15 tests):**

| Class | Test function | What it verifies |
|-------|--------------|-----------------|
| `TestCollectKitBundle` | `test_collects_kit_yaml_and_flows` | Create kit dir with KIT.yaml + 2 flow subdirs → returns (yaml, [{name, yaml, files}]) |
| | `test_no_flows_raises` | Kit dir with only KIT.yaml → BundleError |
| | `test_collects_colocated_files` | Flow subdir has helper.py → included in files dict |
| | `test_skips_non_flow_subdirs` | Subdirs without FLOW.yaml are ignored |
| `TestUnpackKitBundle` | `test_creates_structure` | Unpack → KIT.yaml + flow-a/FLOW.yaml + flow-b/FLOW.yaml |
| | `test_writes_origin_json` | Unpack with origin → .origin.json created |
| | `test_unpacks_colocated_files` | Flow with files → files written to subdirs |
| `TestPublishKitClient` | `test_publish_kit_success` | Mock POST /api/kits → 200, token saved |
| | `test_publish_kit_conflict` | Mock 409 → RegistryError |
| `TestFetchKitClient` | `test_fetch_kit_success` | Mock GET /api/kits/{slug} → 200, data parsed |
| | `test_fetch_kit_not_found` | Mock 404 → RegistryError |
| `TestShareKit` | `test_share_kit_directory` | `main(["share", "test-kit"])` with mock → POST /api/kits called, EXIT_SUCCESS |
| | `test_share_detects_kit_vs_flow` | `main(["share", "my-flow"])` with FLOW.yaml → uses publish_flow, not publish_kit |
| `TestGetKit` | `test_get_kit_creates_structure` | `main(["get", "test-kit"])` with mock returning kit data → KIT.yaml + flow subdirs created |
| | `test_get_kit_fallback_from_flow_404` | Mock fetch_flow 404, then fetch_kit 200 → installs kit |

**Verify:**
```bash
cd ~/work/stepwise && uv run pytest tests/test_kit_bundle.py -x -v
cd ~/work/stepwise && uv run pytest tests/ -x -q  # full regression
```

---

### Phase 4: flow_resolution.py helper

#### Step 4.1: Add `registry_kit_dir()` helper

**File:** `~/work/stepwise/src/stepwise/flow_resolution.py`
**Insert at:** Line 764, after `registry_flow_dir()` ends (line 763: `return project_dir / ".stepwise" / "registry" / f"@{author}" / slug`).

```python
def registry_kit_dir(author: str, slug: str, project_dir: Path) -> Path:
    """Return the directory path for a registry kit install."""
    return project_dir / ".stepwise" / "registry" / f"@{author}" / slug
```

Note: Same path as `registry_flow_dir()` — kits and flows share the registry directory layout. The presence of `KIT.yaml` vs `FLOW.yaml` distinguishes them. The separate function name makes call sites self-documenting.

---

## Testing Strategy

### Commands to run

**Server (after each Phase 1 step):**
```bash
cd ~/work/stepwise.run && uv run pytest tests/test_kits_api.py -x -v     # new kit tests
cd ~/work/stepwise.run && uv run pytest tests/test_api.py -x -v          # regression (28 tests)
cd ~/work/stepwise.run && uv run pytest tests/test_auth.py -x -v         # regression (16 tests)
cd ~/work/stepwise.run && uv run pytest tests/test_admin.py -x -v        # regression (14 tests)
cd ~/work/stepwise.run && uv run pytest tests/ -x -q                     # full suite
```

**Client (after each Phase 2-4 step):**
```bash
cd ~/work/stepwise && uv run pytest tests/test_kit_bundle.py -x -v       # new kit tests
cd ~/work/stepwise && uv run pytest tests/test_bundle.py -x -v           # regression (30 tests)
cd ~/work/stepwise && uv run pytest tests/test_registry.py -x -v         # regression (14 tests)
cd ~/work/stepwise && uv run pytest tests/ -x -q                         # full suite
```

### Regression risk points

| Change | Tests that must still pass | Why |
|--------|--------------------------|-----|
| `d["type"] = "flow"` in `flow_to_dict()` (app.py:365) | All `test_api.py` tests | Additive field — no test checks for absence of `type` key |
| `_check_slug_available()` replaces raw SELECT in `publish_flow()` (app.py:738) | `test_api.py::TestPublish::test_duplicate_slug_409` | Returns "flow" for same-table conflict, same 409 behavior |
| `list_flows()` return adds `kits`, `total_kits`, `total_flows` keys (app.py:606) | `test_api.py::TestSearch` (4 tests) | `flows` key unchanged; `total` equals `total_flows` when no kits exist |
| `cmd_search` table header changes from 4 to 5 columns | `test_bundle.py` tests don't test search output format | No existing test validates the exact table header |
| `cmd_get` now has flow-then-kit fallback with two HTTP calls | `test_bundle.py::TestGetWithBundle` (7 tests) | These mock `fetch_flow` directly — `fetch_kit` is never called since `fetch_flow` succeeds |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Large kit bundles** — 10 flows × 20 files = 200 files in JSON | SQLite handles large TEXT, but JSON parse time could spike | Add kit-level limits in Step 1.5: max 50 bundled flows, max 5MB total. Validate server-side before INSERT |
| **`cmd_get` double-fetch latency** — tries /api/flows first, then /api/kits on 404 | One extra HTTP round-trip on kit fetches | Acceptable — search results include `type` field, so callers who search first can skip the fallback. Future: `GET /api/registry/{slug}` unified endpoint |
| **Slug collision race condition** — two concurrent publishes with same slug | Second INSERT fails with UNIQUE constraint | Catch `sqlite3.IntegrityError` in Step 1.5 and return 409 (same as the existing pre-check) |
| **Breaking `total` in search** — now includes kits | Old CLI reads `total` for "X of Y" display | Old CLI shows a slightly higher total but the `flows` list is unchanged. New `total_flows` key provides the exact count |
| **FTS table creation on existing DB** — adding `kits_fts` on deployed registry | `CREATE VIRTUAL TABLE IF NOT EXISTS` is safe | No migration needed for existing data since kits table starts empty. `init_db()` is idempotent |

---

## Execution Order

```
Step 1.1  (kits table + FTS)
  ↓
Step 1.2  (cross-table slug check + update publish_flow)
  ↓
Step 1.3  (Pydantic models)
  ↓
Step 1.4  (kit_to_dict + validate_kit_yaml + flow type field)
  ↓
Step 1.5  (POST /api/kits)
  ↓
Step 1.6  (GET /api/kits/*)
  ↓
Step 1.7  (PUT + DELETE /api/kits)
  ↓                                    ←── commit to stepwise.run: "feat: kit CRUD endpoints"
Step 1.8  (unified search)
  ↓
Step 1.9  (admin endpoints)
  ↓                                    ←── commit to stepwise.run: "feat: kit search + admin"
Step 1.10 (server tests)
  ↓                                    ←── commit to stepwise.run: "test: kit endpoint tests"
Step 2.1  (publish_kit client)
  ↓
Step 2.2  (fetch_kit client)
  ↓
Step 2.3  (update_kit client)
  ↓                                    ←── commit to stepwise: "feat: registry kit client functions"
Step 3.1  (collect_kit_bundle + unpack_kit_bundle)
  ↓                                    ←── commit to stepwise: "feat: kit bundle collect/unpack"
Step 3.2  (cmd_share kit detection)
  ↓
Step 3.3  (cmd_get kit fallback)
  ↓
Step 3.4  (cmd_search type column)
  ↓
Step 4.1  (registry_kit_dir helper)
  ↓                                    ←── commit to stepwise: "feat: kit share/get/search CLI"
Step 3.5  (client tests)
  ↓                                    ←── commit to stepwise: "test: kit bundle + registry tests"
```
