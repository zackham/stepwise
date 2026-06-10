"""Tests for step-level result caching."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from stepwise.cache import (
    DEFAULT_TTL,
    UNCACHEABLE_TYPES,
    StepResultCache,
    compute_cache_key,
)
from stepwise.engine import AsyncEngine, Engine
from stepwise.models import (
    CacheConfig,
    ExecutorRef,
    ForEachSpec,
    HandoffEnvelope,
    InputBinding,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    WorkflowDefinition,
    _now,
    parse_duration,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_string

from tests.conftest import CallableExecutor, register_step_fn, run_job_sync


def _make_registry():
    from stepwise.executors import ExecutorRegistry
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    return reg


# ── CacheConfig YAML roundtrip ─────────────────────────────────────────


class TestCacheConfigYaml:
    def test_cache_true(self):
        yaml = """
name: test
author: test
steps:
  a:
    run: 'echo "{}"'
    outputs: [x]
    cache: true
"""
        wf = load_workflow_string(yaml)
        step = wf.steps["a"]
        assert step.cache is not None
        assert step.cache.enabled is True
        assert step.cache.ttl is None
        assert step.cache.key_extra is None

    def test_cache_false(self):
        yaml = """
name: test
author: test
steps:
  a:
    run: 'echo "{}"'
    outputs: [x]
    cache: false
"""
        wf = load_workflow_string(yaml)
        step = wf.steps["a"]
        assert step.cache is None

    def test_cache_dict_with_ttl(self):
        yaml = """
name: test
author: test
steps:
  a:
    run: 'echo "{}"'
    outputs: [x]
    cache:
      ttl: 30m
      key_extra: v2
"""
        wf = load_workflow_string(yaml)
        step = wf.steps["a"]
        assert step.cache is not None
        assert step.cache.ttl == 1800
        assert step.cache.key_extra == "v2"

    def test_cache_roundtrip_to_dict(self):
        cc = CacheConfig(enabled=True, ttl=3600, key_extra="v1")
        d = cc.to_dict()
        restored = CacheConfig.from_dict(d)
        assert restored.enabled is True
        assert restored.ttl == 3600
        assert restored.key_extra == "v1"

    def test_step_definition_cache_roundtrip(self):
        step = StepDefinition(
            name="test",
            outputs=["x"],
            executor=ExecutorRef("script", {"command": "echo"}),
            cache=CacheConfig(ttl=7200),
        )
        d = step.to_dict()
        assert "cache" in d
        restored = StepDefinition.from_dict(d)
        assert restored.cache is not None
        assert restored.cache.ttl == 7200


# ── parse_duration ─────────────────────────────────────────────────────


class TestParseDuration:
    def test_hours(self):
        assert parse_duration("24h") == 86400

    def test_minutes(self):
        assert parse_duration("30m") == 1800

    def test_days(self):
        assert parse_duration("7d") == 604800

    def test_seconds(self):
        assert parse_duration("60s") == 60

    def test_invalid(self):
        assert parse_duration("abc") is None
        assert parse_duration("") is None


# ── Cache key computation ──────────────────────────────────────────────


class TestCacheKey:
    def test_same_inputs_same_key(self):
        ref = ExecutorRef("script", {"command": "echo hello"})
        k1 = compute_cache_key({"x": 1}, ref, "1.0.0")
        k2 = compute_cache_key({"x": 1}, ref, "1.0.0")
        assert k1 == k2

    def test_different_inputs_different_key(self):
        ref = ExecutorRef("script", {"command": "echo hello"})
        k1 = compute_cache_key({"x": 1}, ref, "1.0.0")
        k2 = compute_cache_key({"x": 2}, ref, "1.0.0")
        assert k1 != k2

    def test_different_config_different_key(self):
        ref1 = ExecutorRef("script", {"command": "echo hello"})
        ref2 = ExecutorRef("script", {"command": "echo world"})
        k1 = compute_cache_key({"x": 1}, ref1, "1.0.0")
        k2 = compute_cache_key({"x": 1}, ref2, "1.0.0")
        assert k1 != k2

    def test_different_version_different_key(self):
        ref = ExecutorRef("script", {"command": "echo hello"})
        k1 = compute_cache_key({"x": 1}, ref, "1.0.0")
        k2 = compute_cache_key({"x": 1}, ref, "2.0.0")
        assert k1 != k2

    def test_key_extra_changes_key(self):
        ref = ExecutorRef("script", {"command": "echo hello"})
        k1 = compute_cache_key({"x": 1}, ref, "1.0.0", key_extra="v1")
        k2 = compute_cache_key({"x": 1}, ref, "1.0.0", key_extra="v2")
        assert k1 != k2

    def test_runtime_keys_stripped(self):
        ref1 = ExecutorRef("agent", {"prompt": "hello", "_registry": "obj1"})
        ref2 = ExecutorRef("agent", {"prompt": "hello", "_registry": "obj2"})
        k1 = compute_cache_key({}, ref1, "1.0.0")
        k2 = compute_cache_key({}, ref2, "1.0.0")
        assert k1 == k2

    def test_working_dir_changes_key(self):
        """Same command + different working_dir executes differently —
        must not share a cache entry (F24 regression)."""
        ref1 = ExecutorRef("script", {"command": "./build.sh", "working_dir": "/proj/a"})
        ref2 = ExecutorRef("script", {"command": "./build.sh", "working_dir": "/proj/b"})
        k1 = compute_cache_key({}, ref1, "1.0.0")
        k2 = compute_cache_key({}, ref2, "1.0.0")
        assert k1 != k2

    def test_flow_dir_changes_key(self):
        """Relative script paths resolve against flow_dir — different flow
        dirs must not share a cache entry (F24 regression)."""
        ref1 = ExecutorRef("script", {"command": "./build.sh", "flow_dir": "/flows/a"})
        ref2 = ExecutorRef("script", {"command": "./build.sh", "flow_dir": "/flows/b"})
        k1 = compute_cache_key({}, ref1, "1.0.0")
        k2 = compute_cache_key({}, ref2, "1.0.0")
        assert k1 != k2

    def test_flow_name_scopes_key(self):
        ref = ExecutorRef("script", {"command": "./build.sh"})
        k1 = compute_cache_key({}, ref, "1.0.0", flow_name="flow-a")
        k2 = compute_cache_key({}, ref, "1.0.0", flow_name="flow-b")
        assert k1 != k2

    def test_step_name_scopes_key(self):
        ref = ExecutorRef("script", {"command": "./build.sh"})
        k1 = compute_cache_key({}, ref, "1.0.0", step_name="step-a")
        k2 = compute_cache_key({}, ref, "1.0.0", step_name="step-b")
        assert k1 != k2

    def test_identity_omitted_keeps_backward_compatible_key(self):
        """Callers that don't pass identity still get deterministic keys."""
        ref = ExecutorRef("script", {"command": "echo hi"})
        assert compute_cache_key({}, ref, "1.0.0") == compute_cache_key({}, ref, "1.0.0")


# ── StepResultCache store ─────────────────────────────────────────────


class TestStepResultCache:
    def _envelope(self, data: dict) -> HandoffEnvelope:
        return HandoffEnvelope(
            artifact=data,
            sidecar=Sidecar(),
            workspace="/tmp",
            timestamp=_now(),
        )

    def test_put_and_get(self):
        cache = StepResultCache(":memory:")
        env = self._envelope({"result": 42})
        cache.put("key1", "step-a", "flow-x", env, ttl_seconds=3600)
        hit = cache.get("key1")
        assert hit is not None
        assert hit.artifact["result"] == 42

    def test_miss_returns_none(self):
        cache = StepResultCache(":memory:")
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self):
        cache = StepResultCache(":memory:")
        env = self._envelope({"result": 1})
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cache._conn.execute(
            """INSERT INTO cache_entries (key, step_name, flow_name, result, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("expired", "step", "flow", json.dumps(env.to_dict()), _now().isoformat(), past),
        )
        cache._conn.commit()
        assert cache.get("expired") is None

    def test_hit_count_incremented(self):
        cache = StepResultCache(":memory:")
        env = self._envelope({"x": 1})
        cache.put("hc", "step", "flow", env, ttl_seconds=3600)
        cache.get("hc")
        cache.get("hc")
        row = cache._conn.execute("SELECT hit_count FROM cache_entries WHERE key='hc'").fetchone()
        assert row["hit_count"] == 2

    def test_batch_get(self):
        cache = StepResultCache(":memory:")
        for i in range(5):
            cache.put(f"k{i}", "step", "flow", self._envelope({"i": i}), ttl_seconds=3600)
        hits = cache.batch_get(["k0", "k2", "k4", "missing"])
        assert len(hits) == 3
        assert hits["k0"].artifact["i"] == 0
        assert hits["k2"].artifact["i"] == 2
        assert "missing" not in hits

    def test_clear_all(self):
        cache = StepResultCache(":memory:")
        cache.put("a", "s1", "f1", self._envelope({}), ttl_seconds=3600)
        cache.put("b", "s2", "f2", self._envelope({}), ttl_seconds=3600)
        count = cache.clear()
        assert count == 2
        assert cache.get("a") is None

    def test_clear_by_flow(self):
        cache = StepResultCache(":memory:")
        cache.put("a", "s1", "flow-a", self._envelope({}), ttl_seconds=3600)
        cache.put("b", "s2", "flow-b", self._envelope({}), ttl_seconds=3600)
        count = cache.clear(flow_name="flow-a")
        assert count == 1
        assert cache.get("a") is None
        assert cache.get("b") is not None

    def test_clear_by_step(self):
        cache = StepResultCache(":memory:")
        cache.put("a", "analyze", "flow", self._envelope({}), ttl_seconds=3600)
        cache.put("b", "fetch", "flow", self._envelope({}), ttl_seconds=3600)
        count = cache.clear(step_name="analyze")
        assert count == 1
        assert cache.get("b") is not None

    def test_stats(self):
        cache = StepResultCache(":memory:")
        cache.put("a", "s1", "flow-x", self._envelope({}), ttl_seconds=3600)
        cache.put("b", "s2", "flow-x", self._envelope({}), ttl_seconds=3600)
        cache.get("a")
        s = cache.stats()
        assert s["total_entries"] == 2
        assert s["total_hits"] == 1
        assert "flow-x" in s["by_flow"]
        assert s["by_flow"]["flow-x"]["entries"] == 2


# ── Engine cache integration (using legacy Engine for simplicity) ─────


class TestEngineCacheIntegration:
    """Test cache check and write via the tick-based Engine."""

    def _make_engine(self, cache=None):
        store = SQLiteStore(":memory:")
        reg = _make_registry()
        if cache is None:
            cache = StepResultCache(":memory:")
        engine = Engine(store, reg, cache=cache)
        return engine, store, cache

    def _run_to_completion(self, engine, job_id, max_ticks=20):
        engine.start_job(job_id)
        for _ in range(max_ticks):
            engine.tick()
            job = engine.store.load_job(job_id)
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                return job
        return engine.store.load_job(job_id)

    def test_cache_hit_skips_executor(self):
        call_count = 0

        def double(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": inputs["n"] * 2}

        register_step_fn("double", double)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "double"}),
                inputs=[InputBinding("n", "$job", "n")],
                outputs=["result"],
                cache=CacheConfig(),
            ),
        })

        # First run
        job1 = engine.create_job(objective="t1", workflow=wf, inputs={"n": 5})
        result1 = self._run_to_completion(engine, job1.id)
        assert result1.status == JobStatus.COMPLETED
        assert call_count == 1

        runs1 = store.runs_for_job(job1.id)
        assert runs1[0].result.artifact["result"] == 10

        # Second run — cache hit
        job2 = engine.create_job(objective="t2", workflow=wf, inputs={"n": 5})
        result2 = self._run_to_completion(engine, job2.id)
        assert result2.status == JobStatus.COMPLETED
        assert call_count == 1  # NOT called again

        runs2 = store.runs_for_job(job2.id)
        assert runs2[0].result.artifact["result"] == 10
        assert runs2[0].executor_state.get("from_cache") is True

    def test_cache_miss_on_different_inputs(self):
        call_count = 0

        def inc(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": inputs["n"] + 1}

        register_step_fn("inc", inc)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "inc"}),
                inputs=[InputBinding("n", "$job", "n")],
                outputs=["result"],
                cache=CacheConfig(),
            ),
        })

        job1 = engine.create_job(objective="t1", workflow=wf, inputs={"n": 1})
        self._run_to_completion(engine, job1.id)
        assert call_count == 1

        job2 = engine.create_job(objective="t2", workflow=wf, inputs={"n": 2})
        self._run_to_completion(engine, job2.id)
        assert call_count == 2

    def test_cache_miss_on_different_config(self):
        call_count = 0

        def identity(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": "ok"}

        register_step_fn("ident_a", identity)
        register_step_fn("ident_b", identity)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf1 = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "ident_a"}),
                inputs=[InputBinding("x", "$job", "x")],
                outputs=["result"],
                cache=CacheConfig(),
            ),
        })
        wf2 = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "ident_b"}),
                inputs=[InputBinding("x", "$job", "x")],
                outputs=["result"],
                cache=CacheConfig(),
            ),
        })

        job1 = engine.create_job(objective="t1", workflow=wf1, inputs={"x": "hello"})
        self._run_to_completion(engine, job1.id)
        assert call_count == 1

        job2 = engine.create_job(objective="t2", workflow=wf2, inputs={"x": "hello"})
        self._run_to_completion(engine, job2.id)
        assert call_count == 2

    def test_ttl_expiration(self):
        call_count = 0

        def fn(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": "done"}

        register_step_fn("ttl_fn", fn)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "ttl_fn"}),
                inputs=[InputBinding("x", "$job", "x")],
                outputs=["result"],
                cache=CacheConfig(ttl=1),
            ),
        })

        job1 = engine.create_job(objective="t1", workflow=wf, inputs={"x": "a"})
        self._run_to_completion(engine, job1.id)
        assert call_count == 1

        # Expire cache entry
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cache._conn.execute("UPDATE cache_entries SET expires_at = ?", (past,))
        cache._conn.commit()

        job2 = engine.create_job(objective="t2", workflow=wf, inputs={"x": "a"})
        self._run_to_completion(engine, job2.id)
        assert call_count == 2

    def test_default_ttl_by_executor_type(self):
        assert DEFAULT_TTL["script"] == 3600
        assert DEFAULT_TTL["llm"] == 86400
        assert DEFAULT_TTL["agent"] == 86400
        assert DEFAULT_TTL["callable"] == 3600

    def test_explicit_ttl_overrides_default(self):
        cc = CacheConfig(ttl=1800)
        assert cc.ttl == 1800

    def test_key_extra_invalidates(self):
        ref = ExecutorRef("script", {"command": "echo"})
        k1 = compute_cache_key({"x": 1}, ref, "1.0.0", key_extra="v1")
        k2 = compute_cache_key({"x": 1}, ref, "1.0.0", key_extra="v2")
        assert k1 != k2

    def test_external_steps_never_cached(self):
        assert "external" in UNCACHEABLE_TYPES

    def test_rerun_bypasses_cache(self):
        call_count = 0

        def fn(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": f"run-{call_count}"}

        register_step_fn("rerun_fn", fn)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "rerun_fn"}),
                inputs=[InputBinding("x", "$job", "x")],
                outputs=["result"],
                cache=CacheConfig(),
            ),
        })

        # First run — populate cache
        job1 = engine.create_job(objective="t1", workflow=wf, inputs={"x": "a"})
        self._run_to_completion(engine, job1.id)
        assert call_count == 1

        # Second run with --rerun
        job2 = engine.create_job(
            objective="t2", workflow=wf, inputs={"x": "a"},
            config=JobConfig(metadata={"rerun_steps": ["step-a"]}),
        )
        self._run_to_completion(engine, job2.id)
        assert call_count == 2

    def test_rerun_still_writes_cache(self):
        call_count = 0

        def fn(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": f"v{call_count}"}

        register_step_fn("rewrite_fn", fn)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "rewrite_fn"}),
                inputs=[InputBinding("x", "$job", "x")],
                outputs=["result"],
                cache=CacheConfig(),
            ),
        })

        # First run with --rerun (still writes to cache)
        job1 = engine.create_job(
            objective="t1", workflow=wf, inputs={"x": "a"},
            config=JobConfig(metadata={"rerun_steps": ["step-a"]}),
        )
        self._run_to_completion(engine, job1.id)
        assert call_count == 1

        # Second run (no --rerun) — should hit cache
        job2 = engine.create_job(objective="t2", workflow=wf, inputs={"x": "a"})
        self._run_to_completion(engine, job2.id)
        assert call_count == 1  # cache hit

    def test_no_cache_config_means_no_caching(self):
        call_count = 0

        def fn(inputs):
            nonlocal call_count
            call_count += 1
            return {"result": "ok"}

        register_step_fn("nocache_fn", fn)

        cache = StepResultCache(":memory:")
        engine, store, _ = self._make_engine(cache)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef("callable", {"fn_name": "nocache_fn"}),
                inputs=[InputBinding("x", "$job", "x")],
                outputs=["result"],
            ),
        })

        job1 = engine.create_job(objective="t1", workflow=wf, inputs={"x": "a"})
        self._run_to_completion(engine, job1.id)

        job2 = engine.create_job(objective="t2", workflow=wf, inputs={"x": "a"})
        self._run_to_completion(engine, job2.id)
        assert call_count == 2


# ── For-each batch cache (uses AsyncEngine) ───────────────────────────


class TestForEachBatchCache:

    def test_for_each_batch_cache(self):
        """For-each with cached items should skip sub-job creation for hits."""
        call_count = 0

        def source_fn(inputs):
            return {"items": [1, 2, 3, 4, 5]}

        def process_fn(inputs):
            nonlocal call_count
            call_count += 1
            return {"doubled": inputs["item"] * 2}

        register_step_fn("src_fn", source_fn)
        register_step_fn("proc_fn", process_fn)

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process",
                executor=ExecutorRef("callable", {"fn_name": "proc_fn"}),
                inputs=[InputBinding("item", "$job", "item")],
                outputs=["doubled"],
                cache=CacheConfig(),
            ),
        })

        wf = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source",
                executor=ExecutorRef("callable", {"fn_name": "src_fn"}),
                outputs=["items"],
            ),
            "batch": StepDefinition(
                name="batch",
                executor=ExecutorRef("callable", {}),
                outputs=["results"],
                for_each=ForEachSpec(source_step="source", source_field="items"),
                sub_flow=sub_flow,
                inputs=[InputBinding("items", "source", "items")],
            ),
        })

        cache = StepResultCache(":memory:")

        # First run — all 5 items executed
        store1 = SQLiteStore(":memory:")
        engine1 = AsyncEngine(store1, _make_registry(), cache=cache)
        job1 = engine1.create_job(objective="t1", workflow=wf)
        result1 = run_job_sync(engine1, job1.id)
        assert result1.status == JobStatus.COMPLETED
        assert call_count == 5

        # Second run (fresh engine, same cache) — all 5 should be cached
        store2 = SQLiteStore(":memory:")
        engine2 = AsyncEngine(store2, _make_registry(), cache=cache)
        job2 = engine2.create_job(objective="t2", workflow=wf)
        result2 = run_job_sync(engine2, job2.id)
        assert result2.status == JobStatus.COMPLETED
        assert call_count == 5  # no new executions


# ── Cache DB hardening: fail-open on corruption (F25) ──────────────────


class TestCacheCorruptionRecovery:
    def _envelope(self) -> HandoffEnvelope:
        return HandoffEnvelope(artifact={"x": 1}, sidecar=Sidecar(), timestamp=_now())

    def test_corrupt_cache_db_is_recreated(self, tmp_path, caplog):
        """A corrupt cache DB must never block flow execution — the cache
        fails open: warn, recreate, continue."""
        import logging
        db_path = str(tmp_path / "cache" / "results.db")
        import os as _os
        _os.makedirs(_os.path.dirname(db_path), exist_ok=True)
        with open(db_path, "wb") as f:
            f.write(b"this is definitely not a sqlite database" * 100)

        with caplog.at_level(logging.WARNING, logger="stepwise.cache"):
            cache = StepResultCache(db_path)
        try:
            # Fully functional after recreation
            key = "k" * 64
            cache.put(key, "step", "flow", self._envelope())
            hit = cache.get(key)
            assert hit is not None
            assert hit.artifact == {"x": 1}
            assert cache.stats()["total_entries"] == 1
        finally:
            cache.close()
        assert any("recreating" in r.message.lower() for r in caplog.records)

    def test_unrecoverable_path_falls_back_to_memory(self, tmp_path):
        """If the cache DB cannot be recreated (path is a directory), the
        cache falls back to in-memory rather than crashing the run."""
        bad_path = str(tmp_path / "cachedir")
        import os as _os
        _os.makedirs(bad_path, exist_ok=True)

        cache = StepResultCache(bad_path)  # must not raise
        try:
            key = "k" * 64
            cache.put(key, "step", "flow", self._envelope())
            assert cache.get(key) is not None
            assert cache._db_path == ":memory:"
        finally:
            cache.close()

    def test_healthy_cache_db_untouched(self, tmp_path):
        """A healthy on-disk cache survives reopen with entries intact."""
        db_path = str(tmp_path / "results.db")
        cache = StepResultCache(db_path)
        key = "k" * 64
        cache.put(key, "step", "flow", self._envelope())
        cache.close()

        reopened = StepResultCache(db_path)
        try:
            assert reopened.get(key) is not None
        finally:
            reopened.close()

    def test_cache_db_gets_wal_hardening_pragmas(self, tmp_path):
        """The cache DB gets the same 0.46.1 WAL hardening as the main store."""
        db_path = str(tmp_path / "results.db")
        cache = StepResultCache(db_path)
        try:
            assert cache._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert cache._conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 200
            assert cache._conn.execute("PRAGMA journal_size_limit").fetchone()[0] == 16777216
            assert cache._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        finally:
            cache.close()
