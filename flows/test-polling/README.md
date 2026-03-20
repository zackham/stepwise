# test-polling

Validates polling executor reliability across four scenarios: basic file-based polling, server restart survival, long-running polls (>5 min), and simulated backoff.

All tests are self-contained — no external dependencies. Marker files are created in the job workspace and cleaned up automatically.

## Purpose

| Scenario | Steps | Default duration | What it validates |
|---|---|---|---|
| Basic polling | setup-basic → poll-basic | ~5s | File-based poll check fulfillment at 2s intervals |
| Restart survival | setup-restart → poll-restart | ~30s | Poll behavior across server restart (see known issue) |
| Long-running | setup-longrun → poll-longrun | ~6 min | Sustained polling at 30s intervals over extended duration |
| Backoff simulation | setup-backoff → poll-backoff | ~15s | Check command tracks attempt count, succeeds after N failures |

All four scenarios run in parallel. A final `summarize` step waits for all four and reports pass/fail.

## Quick run (~30s)

```bash
stepwise run flows/test-polling/FLOW.yaml \
  --var basic_delay_seconds=3 \
  --var longrun_delay_seconds=10 \
  --var backoff_fail_count=2
```

## Full run (~6 min)

```bash
stepwise run --watch flows/test-polling/FLOW.yaml
```

Default `longrun_delay_seconds=360`. The web UI shows poll steps entering SUSPENDED (yellow) then transitioning to COMPLETED (green) as markers appear.

## Server restart test procedure

1. Start the server: `stepwise server start`
2. Run the flow with long delays:
   ```bash
   stepwise run flows/test-polling/FLOW.yaml \
     --var basic_delay_seconds=120 \
     --var longrun_delay_seconds=120 \
     --var backoff_fail_count=20
   ```
3. Wait for poll steps to enter SUSPENDED state (visible in web UI or `stepwise list`)
4. Restart the server: `stepwise server restart`
5. Observe whether poll steps resume checking after restart

**Expected result:** Poll timers do NOT resume after restart (known gap — see below).

## Known issue: poll watch restart gap

After a server restart, poll watch timers are **not re-scheduled**. The `_schedule_poll_watch` method (`engine.py:2447-2502`) is only called when a step first suspends with a poll watch. On server restart:

- `_cleanup_zombie_jobs` (`server.py:325-358`) preserves suspended runs but doesn't re-arm poll timers
- `_poll_external_changes` in AsyncEngine never re-schedules poll watches
- The legacy `Engine.tick()` loop (`engine.py:736-738`) checks polls directly, but the server uses `AsyncEngine` which requires scheduled tasks

**Impact:** Poll steps that were active before restart will stall until the engine is fixed to re-schedule poll watches on startup.

This is a known gap documented here for visibility. This test flow serves as a regression test once the fix lands.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `basic_delay_seconds` | `5` | Seconds before basic marker file appears |
| `longrun_delay_seconds` | `360` | Seconds before long-run marker file appears |
| `backoff_fail_count` | `4` | Number of poll checks that return empty before backoff succeeds |
