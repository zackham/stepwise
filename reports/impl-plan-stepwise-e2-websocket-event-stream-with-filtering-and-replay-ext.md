---
title: "Implementation Plan: E2 — WebSocket Event Stream with Filtering and Replay"
date: "2026-03-21T20:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# E2 — WebSocket Event Stream with Filtering and Replay

## Overview

Add a new WebSocket endpoint `GET /api/v1/events/stream` that serves as a general-purpose event bus with support for `session_id`, `job_id`, and `since_event_id` filtering, plus historical replay with a boundary frame. This coexists with the existing `/ws` endpoint (which remains the UI's tick-based invalidation channel).

## Requirements

### R1: New WebSocket endpoint
- **Route:** `GET /api/v1/events/stream`
- **Acceptance criteria:**
  - Client can connect via WebSocket upgrade at this path
  - Connection succeeds with 101 Switching Protocols
  - Client receives JSON messages (one per WebSocket frame)
  - Multiple clients can connect simultaneously
  - Clean disconnect on client close (no resource leaks)

### R2: Live event forwarding
- **Acceptance criteria:**
  - After connection (and after any replay phase), every engine event that matches the client's filters is forwarded as a JSON message
  - Message format matches E1 envelope: `{"event": "<type>", "job_id": "<id>", "timestamp": "<ISO>", "event_id": <rowid>, "metadata": {"sys": {...}, "app": {...}}, "data": {...}}` with optional `"step"` promoted from data
  - All ~25 event types emitted by `_emit()` are captured (step lifecycle, job lifecycle, exits, loops, for-each, etc.)
  - Events arrive in causal order per job (rowid ordering guarantees this since `_emit()` is serialized per job)

### R3: `job_id` filter
- **Query param:** `?job_id=X` (repeatable for OR semantics: `?job_id=A&job_id=B`)
- **Acceptance criteria:**
  - Client only receives events where `event.job_id` is in the specified set
  - Events for other jobs are silently dropped (not queued)
  - Works for both replay and live phases
  - Invalid/non-existent job_id is accepted (no events will match, no error)

### R4: `session_id` filter
- **Query param:** `?session_id=X`
- **Acceptance criteria:**
  - On connect, server resolves all jobs where `metadata.sys.session_id == X` using the existing `all_jobs(meta_filters=...)` pattern (`store.py:239-250`)
  - Client receives events only for those jobs
  - When a new job starts with matching `session_id`, client dynamically begins receiving its events too
  - Can be combined with `job_id` (union of both sets)

### R5: `since_event_id` replay
- **Query param:** `?since_event_id=N` (integer, the `event_id`/rowid from a previously received event)
- **Acceptance criteria:**
  - Server replays all stored events with `rowid > N`, filtered by any `job_id`/`session_id` params
  - Each replayed event is an individual WebSocket message in the same envelope format as live events
  - After all replay events are sent, server sends boundary frame: `{"type": "sys.replay.complete", "last_event_id": <highest_rowid_sent>}`
  - If no events match, boundary frame still sent with `"last_event_id": N` (the input value)
  - After boundary frame, server switches to live mode — no gap in event delivery
  - `since_event_id=0` replays the entire event log (for the filtered scope)

### R6: `since_job_start` replay
- **Query param:** `?since_job_start=true`
- **Acceptance criteria:**
  - Equivalent to `since_event_id=0` but scoped to the filtered jobs
  - Requires at least one of `job_id` or `session_id` — server closes connection with reason `"since_job_start requires job_id or session_id"` if neither is specified
  - Replays all events from the beginning for the filtered jobs, then boundary frame, then live
  - Cannot be combined with `since_event_id` — if both specified, `since_event_id` takes precedence

### R7: No-filter admin mode
- **Acceptance criteria:**
  - Connection with no query params receives ALL live events (every event from every job)
  - No replay occurs (live-only by default)
  - Useful for debugging and admin dashboards

### R8: Coexistence with existing `/ws`
- **Acceptance criteria:**
  - The existing `/ws` endpoint (`server.py:2230-2242`) continues to send `{"type": "tick", ...}` messages unchanged
  - The existing `_ws_clients` set and `_broadcast()` function (`server.py:131, 246-256`) are not modified
  - The web UI (`useStepwiseWebSocket.ts`) continues to work without changes
  - Both endpoints can have active connections simultaneously

## Assumptions

### A1: `event_id` is the SQLite `rowid` from `save_event()`
- **Verified at:** `store.py:529-544` — `save_event()` does `cursor = self._conn.execute(INSERT ...)` and returns `cursor.lastrowid`
- **Verified at:** `hooks.py:30-50` — `build_event_envelope()` accepts `event_id: int` parameter (the rowid) and includes it in the envelope as `"event_id"`
- **Verified at:** `engine.py:2481-2494` — `rowid = self.store.save_event(event)` then `envelope = build_event_envelope(..., rowid, ...)`
- **Verified at:** `store.py:83-90` — events table DDL: `id TEXT PRIMARY KEY` — SQLite assigns an implicit integer `rowid` separate from the TEXT `id` column. The `rowid` is monotonically increasing and never reused.
- **Implication:** `SELECT rowid, * FROM events WHERE rowid > N ORDER BY rowid` is the correct replay query.

### A2: `_emit()` is on the base `Engine` class, not `AsyncEngine`
- **Verified at:** `engine.py:100` — `class Engine:` starts at line 100
- **Verified at:** `engine.py:2472` — `def _emit(self, ...)` is inside `Engine`
- **Verified at:** `engine.py:2551` — `class AsyncEngine(Engine):` starts at line 2551
- **Verified at:** `engine.py:2577` — `self.on_broadcast` is declared in `AsyncEngine.__init__()`, not in base `Engine`
- **Implication:** The `on_event` callback must be declared in `AsyncEngine.__init__()` (next to `on_broadcast`) and invoked in `_emit()` with a guard `if hasattr(self, 'on_event') and self.on_event:` since `_emit()` lives on the base class. Alternatively, initialize `self.on_event = None` in base `Engine.__init__()` and check it directly. The latter is cleaner.

### A3: The current broadcast path does NOT carry event envelopes
- **Verified at:** `server.py:139-144` — `_schedule_broadcast()` wraps the engine event into `{"type": "tick", "changed_jobs": [job_id], "timestamp": ...}` — the full envelope is discarded
- **Verified at:** `engine.py:2993` — `self._broadcast({"type": "job_changed", "job_id": job_id})` — `_broadcast()` on AsyncEngine sends minimal state-change dicts
- **Verified at:** `engine.py:3057-3060` — `_broadcast()` just fires `self.on_broadcast(event)` with whatever dict it receives
- **Implication:** A new `on_event` callback is needed to carry full envelopes. Cannot reuse `on_broadcast`.

### A4: `ThreadSafeStore` methods are safe to call from async context
- **Verified at:** `server.py:76-95` — `ThreadSafeStore.__init__()` wraps the connection with `_LockedConnection(raw_conn, threading.Lock())`
- **Verified at:** `server.py:40-74` — `_LockedConnection` proxy acquires the threading lock on every `execute()`, `commit()`, etc.
- **Verified at:** `server.py:301-337` — `_observe_external_jobs()` (an async function) calls `engine.store.running_jobs()` and `engine.store.stale_jobs()` directly without `asyncio.to_thread()`
- **Implication:** The new `load_events_since()` store method can be called directly from the async WebSocket handler. No `to_thread()` wrapper needed.

### A5: `all_jobs(meta_filters=...)` already supports session_id lookup
- **Verified at:** `store.py:239-250` — `all_jobs()` accepts `meta_filters: dict[str, str]` and generates `json_extract(metadata, '$.key') = value` WHERE clauses
- **Implication:** Resolving `session_id` → job_ids on connect is a single call: `store.all_jobs(meta_filters={"sys.session_id": session_id})`. No new query method needed for this.

### A6: `_emit()` covers all engine events comprehensively
- **Verified:** ~50 call sites in `engine.py` covering all event types: `JOB_STARTED` (line 199), `JOB_COMPLETED` (line 812), `JOB_FAILED` (line 824), `JOB_PAUSED` (lines 210, 2033, 2057), `JOB_RESUMED` (line 220), `STEP_STARTED` (line 1227), `STEP_COMPLETED` (lines 731, 1272, 1386, 1596, 1661, 1800), `STEP_FAILED` (lines 1082, 1329, 1374, 2393, 2649), `STEP_SUSPENDED` (line 1404), `STEP_DELEGATED` (lines 1446, 1844), `STEP_SKIPPED` (line 1055), `EXIT_RESOLVED` (lines 1990, 2004, 2087, 2407), `LOOP_ITERATION` (lines 2040, 2422), `LOOP_MAX_REACHED` (line 2023), `WATCH_FULFILLED` (lines 437, 2193), `FOR_EACH_STARTED` (line 1673), `FOR_EACH_COMPLETED` (lines 1777, 1795), `CHAIN_CONTEXT_COMPILED` (line 1971), `EXTERNAL_RERUN` (line 289), `CONTEXT_INJECTED` (line 451)
- **Implication:** A single hook in `_emit()` captures the complete event stream. No events are emitted through a different path.

### A7: No existing versioned API
- **Verified at:** all routes in `server.py` use `/api/<resource>` pattern (e.g., `/api/jobs`, `/api/config`, `/api/health`). No `/api/v1/` prefix exists.
- **Implication:** The new endpoint at `/api/v1/events/stream` introduces the first versioned path. This is intentional per the spec — it signals this is a stable contract for external consumers, distinct from the internal UI API.

## Out of Scope

- **CLI commands that consume the stream** — E3 scope. The stream is server-side only for now.
- **Extension protocol documentation** — E4 scope. The envelope format is defined by E1 and this plan.
- **Authentication/authorization** — Local-first architecture, no auth on any endpoint currently. Same security model as existing `/ws`.
- **Rate limiting or backpressure** — Spec explicitly excludes this. We add a bounded queue (safety valve) but no formal backpressure protocol.
- **Binary/compressed event formats** — JSON text frames only.
- **Modifying the existing `/ws` endpoint** — The existing endpoint serves the web UI's React Query invalidation pattern. Changing it would break the frontend.
- **Frontend changes** — The web UI continues to use `/ws` for tick-based invalidation. No new React hooks or components.
- **Multi-value `session_id`** — Only a single `session_id` filter is supported per connection (the spec says `?session_id=X`, singular). Clients needing multiple sessions open multiple connections.

## Architecture

### Event dispatch flow (after E2)

```
engine._emit()  ──→  store.save_event()  ──→  build_event_envelope()
                                                  │
                                                  ├──→  fire_hook_for_event()       [existing, unchanged]
                                                  ├──→  fire_notify_webhook()        [existing, unchanged]
                                                  └──→  self.on_event(envelope)      [NEW: stream dispatch]
                                                              │
                                                              ▼
                                                  _schedule_event_stream()           [thread-safe bridge]
                                                              │
                                                              ▼
                                              async _dispatch_to_stream(envelope)    [filter + enqueue]
                                                              │
                                                              ▼
                                              client.queue.put_nowait(envelope)      [per-client queue]

engine._broadcast()  ──→  on_broadcast  ──→  _schedule_broadcast()  ──→  _ws_clients   [UNCHANGED]
```

### New callback: `on_event`

Added to base `Engine.__init__()` as `self.on_event: Callable[[dict], None] | None = None`. Called at the end of `_emit()` after `build_event_envelope()` (line 2494 in `engine.py`). Separate from `on_broadcast` because:

1. `on_broadcast` fires on `AsyncEngine._broadcast()` calls (lines 2993, 2999, 3032) which are job-state-change notifications — NOT every event
2. `on_event` fires for every persisted event (~50 call sites across all event types)
3. `on_broadcast` sends minimal `{"type": "job_changed", "job_id": ...}` dicts; `on_event` sends full E1 envelopes

### Thread safety pattern

`_emit()` runs in the engine's thread pool (via `asyncio.to_thread()` in `AsyncEngine`). The `on_event` callback uses `_event_loop.call_soon_threadsafe()` to schedule `_dispatch_to_stream()` on the event loop — identical to the proven pattern in `_schedule_broadcast()` (`server.py:139-145`).

### Stream client registry

```python
@dataclass
class _StreamClient:
    ws: WebSocket
    queue: asyncio.Queue[dict]       # bounded, maxsize=1000
    job_ids: set[str] | None         # None = no job_id filter (pass all)
    session_id: str | None           # None = no session_id filter
    session_job_ids: set[str]        # resolved on connect, expanded on job.started
```

Module-level `_stream_clients: set[_StreamClient] = set()`. Cleaned up in `lifespan()` shutdown (cancel any pending send tasks).

### Filter evaluation (in `_dispatch_to_stream`)

For each `(client, envelope)` pair:
1. Extract `job_id` from envelope
2. If `client.job_ids` is not None and `job_id` not in `client.job_ids` and `job_id` not in `client.session_job_ids` → skip
3. If `client.session_id` is not None and `client.job_ids` is None and `job_id` not in `client.session_job_ids` → skip
4. Otherwise → `client.queue.put_nowait(envelope)`

Dynamic session expansion: if `envelope["event"] == "job.started"` and `client.session_id` is not None, check `envelope["metadata"]["sys"].get("session_id") == client.session_id` — if so, add `job_id` to `client.session_job_ids`.

### Replay → live transition (gap-free)

1. Parse filters from query params
2. Accept WebSocket
3. Resolve `session_id` → initial `session_job_ids` via `store.all_jobs(meta_filters=...)`
4. Build combined `effective_job_ids = explicit_job_ids ∪ session_job_ids` (or `None` for no filter)
5. If replay requested (`since_event_id` or `since_job_start`):
   a. Query `store.load_events_since(since_rowid, effective_job_ids)`
   b. Send each event as a WebSocket message
   c. Track `last_sent_rowid`
6. **Register client in `_stream_clients`** (from this point, live events start queuing)
7. **Catch-up query:** `store.load_events_since(last_sent_rowid, effective_job_ids)` — picks up any events emitted between step 5a and step 6
8. Send catch-up events
9. Send boundary frame: `{"type": "sys.replay.complete", "last_event_id": <final_last_sent_rowid>}`
10. Enter live loop: dequeue from `client.queue`, send to WebSocket

The catch-up in step 7 closes the gap. Duplicates between steps 5 and 7 are possible if no new events occurred (the query returns nothing), but if events did arrive in the gap, they're caught. Clients should tolerate duplicate `event_id`s (idempotent processing).

## Implementation Steps

Steps are ordered by dependency. Each step's prerequisites are listed explicitly.

### Step 1: Add `on_event` callback to Engine base class (~20 min)

**Prerequisites:** None (first step, no dependencies)
**Why first:** All subsequent steps depend on this callback existing. The store method (step 2) is independent but the server wiring (step 3+) needs both.

**File:** `src/stepwise/engine.py`

Changes:
1. In `Engine.__init__()` (starts at line 100), add `self.on_event: Callable[[dict], None] | None = None` alongside existing instance variables
2. In `_emit()` (line 2472), after `envelope = build_event_envelope(...)` (line 2491-2494) and before the hook/webhook dispatch (line 2497), add:
   ```python
   if self.on_event:
       self.on_event(envelope)
   ```

**Verification:** `uv run pytest tests/test_async_engine.py -x` — existing tests pass (callback defaults to None, no behavior change).

### Step 2: Add `load_events_since()` to SQLiteStore (~30 min)

**Prerequisites:** None (independent of step 1)
**Why this order:** Can be developed in parallel with step 1. Steps 3+ need this method for replay.

**File:** `src/stepwise/store.py`

Add a new method after `load_events()` (line 557):

```python
def load_events_since(
    self,
    since_rowid: int = 0,
    job_ids: set[str] | None = None,
) -> list[tuple[int, dict]]:
```

- SQL: `SELECT e.rowid, e.id, e.job_id, e.timestamp, e.type, e.data, e.is_effector, j.metadata FROM events e LEFT JOIN jobs j ON e.job_id = j.id WHERE e.rowid > ? [AND e.job_id IN (...)] ORDER BY e.rowid`
- Returns `list[tuple[int, dict]]` where each tuple is `(rowid, envelope_dict)`
- Build envelope using `build_event_envelope()` from `hooks.py` — import already exists in the module DAG (`store` doesn't import from `hooks`, so we need to import it here). Actually, `store.py` should NOT import from `hooks.py` (module DAG: `models → store`). Instead, return raw row data and let the caller (server) build envelopes.
- Revised return: `list[tuple[int, Event, dict]]` — `(rowid, Event, job_metadata_dict)`. The server builds envelopes.

**Verification:** `uv run pytest tests/test_store.py -x` — existing store tests pass. New test in step 6a validates the method.

### Step 3: Add stream client registry and dispatch function (~30 min)

**Prerequisites:** Step 1 (needs `on_event` to exist for wiring)
**Why this order:** The dispatch function and client registry are the foundation that the WebSocket handler (step 4) and the lifespan wiring (step 3) both depend on.

**File:** `src/stepwise/server.py`

Add near line 131 (after `_ws_clients` declaration):

1. Define `_StreamClient` dataclass (as described in Architecture)
2. Define `_stream_clients: set[_StreamClient] = set()`
3. Define `async def _dispatch_to_stream(envelope: dict) -> None` — iterates `_stream_clients`, evaluates filters, calls `queue.put_nowait()`. Handles `QueueFull` by closing the client WebSocket with reason `"backpressure"`. Handles dynamic `session_job_ids` expansion on `job.started` events.
4. Define `def _schedule_event_stream(envelope: dict) -> None` — thread-safe bridge using `_event_loop.call_soon_threadsafe(_event_loop.create_task, _dispatch_to_stream(envelope))`. Follows `_schedule_broadcast()` pattern at `server.py:139-145`.
5. In `lifespan()` (line 441), add `_engine.on_event = _schedule_event_stream` right after `_engine.on_broadcast = _schedule_broadcast`

**Verification:** Server starts without errors. No observable behavior yet (no clients connected).

### Step 4: Implement WebSocket route handler — replay phase (~45 min)

**Prerequisites:** Step 2 (needs `load_events_since()`), Step 3 (needs `_stream_clients` registry)
**Why this order:** Replay is the most complex part of the handler. Implementing it first and testing it in isolation (step 6b) de-risks the live phase.

**File:** `src/stepwise/server.py`

Add after line 2242 (after existing `/ws` endpoint):

```python
@app.websocket("/api/v1/events/stream")
async def event_stream_endpoint(ws: WebSocket):
```

Handler logic for this step (replay only, live loop in step 5):
1. Parse query params: `ws.query_params.getlist("job_id")`, `.get("session_id")`, `.get("since_event_id")`, `.get("since_job_start")`
2. Validate: if `since_job_start` and no `job_id`/`session_id`, close with reason
3. Accept WebSocket
4. Resolve `session_id` → `session_job_ids` via `_get_engine().store.all_jobs(meta_filters={"sys.session_id": session_id})`
5. Compute `effective_job_ids` (union of explicit + session-resolved, or `None`)
6. Determine replay start: `since_event_id` as int, or `0` if `since_job_start`
7. If replaying: call `store.load_events_since(since_rowid, effective_job_ids)`, send each event envelope via `ws.send_json()`, track `last_sent_rowid`
8. (Live loop deferred to step 5)

### Step 5: Implement WebSocket route handler — live phase and gap closure (~45 min)

**Prerequisites:** Step 4 (replay handler exists), Step 3 (dispatch + registry exist)
**Why this order:** The live phase depends on the client registry being functional and the replay phase being implemented. This step completes the handler.

**File:** `src/stepwise/server.py`

Continue the handler from step 4:
1. Create `_StreamClient` with parsed filters and a bounded `asyncio.Queue(maxsize=1000)`
2. Add to `_stream_clients`
3. Run catch-up query: `store.load_events_since(last_sent_rowid, effective_job_ids)` — send any gap events
4. Send boundary frame: `{"type": "sys.replay.complete", "last_event_id": last_sent_rowid}`
5. Enter live send loop:
   ```python
   try:
       while True:
           envelope = await client.queue.get()
           await ws.send_json(envelope)
   except WebSocketDisconnect:
       pass
   finally:
       _stream_clients.discard(client)
   ```
6. Handle concurrent receive (for client-initiated close): use `asyncio.create_task` for a receive loop that catches `WebSocketDisconnect` and cancels the send loop.

**Verification:** Manual test with `websocat` (server must be running).

### Step 6a: Store unit tests (~30 min)

**Prerequisites:** Step 2 (store method exists)
**Why this order:** Validates the data layer independently before integration tests.

**File:** `tests/test_event_stream.py` (new file)

Tests for `load_events_since()`:
1. **Empty store** — returns empty list
2. **Basic query** — insert 5 events, query with `since_rowid=0`, get all 5 with correct rowids
3. **Rowid filtering** — insert 5 events, query with `since_rowid=3`, get only events with rowid > 3
4. **Job ID filtering** — insert events for 3 jobs, query with `job_ids={"job-a"}`, get only job-a events
5. **Combined filters** — `since_rowid` + `job_ids` together
6. **Metadata included** — verify returned metadata matches job's stored metadata

```bash
uv run pytest tests/test_event_stream.py::TestLoadEventsSince -v
```

### Step 6b: WebSocket integration tests — basic stream and filters (~1 hr)

**Prerequisites:** Steps 3-5 (full endpoint functional)
**Why this order:** Integration tests validate the complete pipeline after all pieces are assembled.

**File:** `tests/test_event_stream.py`

Test setup follows `test_editor_api.py` pattern (`server.py:78-100`): set `STEPWISE_DB=:memory:`, `STEPWISE_PROJECT_DIR` to tmp, use `TestClient(app)` context manager with `client.websocket_connect()`.

Tests:

1. **Basic stream — receive live events:**
   - Connect to `/api/v1/events/stream` (no filters)
   - Create a job via `POST /api/jobs` with a simple callable workflow
   - Start the job via `POST /api/jobs/{id}/start`
   - Read messages from WebSocket, verify at least `job.started` and `step.started` events arrive
   - Verify envelope format: `event`, `job_id`, `timestamp`, `event_id`, `metadata`, `data` keys present

2. **job_id filter:**
   - Create two jobs (job-A, job-B)
   - Connect with `?job_id=<job-A-id>`
   - Start both jobs
   - Verify only job-A events arrive (no job-B events)

3. **session_id filter:**
   - Create job-A with `metadata={"sys": {"session_id": "sess-1"}}` and job-B with `{"sys": {"session_id": "sess-2"}}`
   - Connect with `?session_id=sess-1`
   - Start both jobs
   - Verify only job-A events arrive

4. **No-filter admin mode:**
   - Connect without params
   - Create and start two jobs
   - Verify events from both jobs arrive

5. **Coexistence with /ws:**
   - Connect to both `/ws` and `/api/v1/events/stream`
   - Create and start a job
   - Verify `/ws` gets `{"type": "tick", ...}` messages
   - Verify `/api/v1/events/stream` gets full event envelopes
   - Both continue to work independently

```bash
uv run pytest tests/test_event_stream.py::TestEventStreamLive -v
```

### Step 6c: WebSocket integration tests — replay and reconnection (~45 min)

**Prerequisites:** Step 6b (basic stream tests pass, confirming endpoint works)
**Why this order:** Replay tests build on the confirmed-working endpoint.

**File:** `tests/test_event_stream.py`

Tests:

6. **Replay with since_event_id:**
   - Create and run a job to completion (generates several events)
   - Note the `event_id` of the 2nd event
   - Connect with `?since_event_id=<2nd_event_id>&job_id=<job_id>`
   - Verify: receive replayed events (with `event_id` > 2nd), then boundary frame `{"type": "sys.replay.complete", "last_event_id": N}`
   - Verify: `last_event_id` in boundary equals the highest event_id sent

7. **since_job_start replay:**
   - Create and run a job to completion
   - Connect with `?since_job_start=true&job_id=<job_id>`
   - Verify: receive ALL events for the job from the beginning, then boundary frame

8. **since_job_start without scope — error:**
   - Connect with `?since_job_start=true` (no job_id or session_id)
   - Verify: connection is closed with reason message

9. **Boundary frame with no matching events:**
   - Connect with `?since_event_id=999999&job_id=<job_id>` (event_id beyond any existing)
   - Verify: boundary frame sent immediately with `"last_event_id": 999999`

10. **Reconnection — no gaps:**
    - Create and start a long-running job (use external executor that suspends)
    - Connect, receive some events, note last `event_id`, disconnect
    - Let more events occur (fulfill the suspended step)
    - Reconnect with `?since_event_id=<last_seen>&job_id=<job_id>`
    - Verify: replayed events cover the gap, boundary frame, then live events continue

```bash
uv run pytest tests/test_event_stream.py::TestEventStreamReplay -v
```

### Step 7: Full regression (~15 min)

**Prerequisites:** All previous steps complete
**Why last:** Final validation that nothing is broken.

```bash
uv run pytest tests/ -v
cd web && npm run test
```

Confirm:
- All existing tests pass (especially `test_async_engine.py`, `test_editor_api.py`, `test_streaming.py`)
- No import errors or circular dependencies
- Existing `/ws` behavior unchanged

## Testing Strategy

### Commands

```bash
# Store unit tests only
uv run pytest tests/test_event_stream.py::TestLoadEventsSince -v

# Live stream integration tests
uv run pytest tests/test_event_stream.py::TestEventStreamLive -v

# Replay integration tests
uv run pytest tests/test_event_stream.py::TestEventStreamReplay -v

# All new tests
uv run pytest tests/test_event_stream.py -v

# Full regression
uv run pytest tests/ -v

# Web regression (no frontend changes, but verify nothing broke)
cd web && npm run test
```

### Manual verification

```bash
# Terminal 1: start server
uv run stepwise server start

# Terminal 2: connect to unfiltered stream
websocat 'ws://localhost:8340/api/v1/events/stream'

# Terminal 3: run a job
uv run stepwise run examples/hello.flow.yaml --var name=world

# Verify Terminal 2 shows JSON event envelopes with correct format
# Verify events include: job.started, step.started, step.completed, job.completed

# Terminal 4: test replay
# Note an event_id from Terminal 2 output
websocat 'ws://localhost:8340/api/v1/events/stream?since_event_id=0'
# Verify: all historical events replayed, then sys.replay.complete boundary, then live
```

## Risks & Mitigations

### R1: Replay/live gap — events emitted between replay query and client registration

- **Impact:** Client misses events, breaking the gap-free guarantee
- **Mitigation:** Register client for live events BEFORE sending the boundary frame (step 6 in replay flow). Then run a catch-up query for `rowid > last_replayed_rowid`. Any events emitted in the gap are caught by either the catch-up query or the live queue. Clients should be idempotent on `event_id` to handle the rare duplicate.
- **Verification:** Test case 10 (reconnection) validates no-gap behavior.

### R2: Memory pressure from slow consumers

- **Impact:** Unbounded queue growth if a client can't keep up
- **Mitigation:** Bounded `asyncio.Queue(maxsize=1000)`. On `QueueFull`, close the WebSocket with code 1008 (Policy Violation) and reason `"backpressure: client too slow"`. Client can reconnect with `since_event_id` to catch up.
- **Verification:** Not formally tested in E2 (backpressure is out of scope per spec). The bounded queue is a safety valve only.

### R3: Thread safety — `on_event` fires from engine thread pool

- **Impact:** Calling async dispatch from sync context would raise RuntimeError
- **Mitigation:** `_schedule_event_stream()` uses `_event_loop.call_soon_threadsafe()` — same proven pattern as `_schedule_broadcast()` (`server.py:139-145`) which has been in production since the initial server implementation.
- **Verification:** All integration tests exercise this path (engine runs in thread pool, dispatch runs on event loop).

### R4: `session_id` filter requires dynamic job discovery

- **Impact:** New jobs created after connection but matching the session would be missed
- **Mitigation:** In `_dispatch_to_stream()`, check `job.started` events for matching `session_id` in metadata and expand `client.session_job_ids`. This check runs only on `job.started` events (rare, cheap).
- **Verification:** Test case 3 (session_id filter) should include a sub-test where a new job is created after the client connects.

### R5: `since_job_start` without scope would replay entire event log

- **Impact:** Accidental full-log replay on a busy server
- **Mitigation:** Require `job_id` or `session_id` when `since_job_start=true`. Close connection with reason if neither specified.
- **Verification:** Test case 8 validates the error case.

### R6: Module DAG violation risk

- **Impact:** `store.py` importing from `hooks.py` would violate `models → store` boundary
- **Mitigation:** `load_events_since()` returns raw `(rowid, Event, metadata_dict)` tuples. The caller (server.py) imports `build_event_envelope` from `hooks.py` and constructs envelopes. This respects the DAG: `models → store`, `hooks` is a leaf module imported by `engine` and `server`.
- **Verification:** `uv run python -c "from stepwise.store import SQLiteStore"` — no import error.

### R7: Large replay sets blocking the event loop

- **Impact:** Sending thousands of replay events synchronously blocks other async tasks
- **Mitigation:** For E2, replay is synchronous (send in a loop). SQLite rowid scans are fast (<10ms for thousands of rows). If this becomes a bottleneck, add `await asyncio.sleep(0)` yield points every N events in a future iteration. The bounded queue for live events ensures live clients aren't starved during another client's replay.
