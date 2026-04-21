"""Tests for stepwise.lifecycle — generic resource lifecycle manager."""

import pytest

from stepwise.lifecycle import (
    ManagedResource,
    ResourceLifecycleManager,
    ResourceManager,
)


# ── Test helpers ──────────────────────────────────────────────────────


class FakeResource:
    """Simple test resource."""

    def __init__(self, config_id: str):
        self.config_id = config_id
        self.alive = True

    def __repr__(self):
        return f"FakeResource({self.config_id!r})"


def _is_eq(a: dict, b: dict) -> bool:
    return a.get("group") == b.get("group")


def _factory(config: dict) -> FakeResource:
    return FakeResource(config.get("group", "default"))


def _teardown(resource: FakeResource) -> None:
    resource.alive = False


@pytest.fixture
def manager():
    return ResourceLifecycleManager(
        is_eq=_is_eq,
        factory=_factory,
        teardown=_teardown,
    )


# ── acquire() ─────────────────────────────────────────────────────────


class TestAcquire:
    def test_creates_new_resource(self, manager):
        managed, was_new = manager.acquire({"group": "alpha"})
        assert managed.resource.config_id == "alpha"
        assert managed.resource.alive
        assert was_new is True
        assert len(manager.active) == 1

    def test_reuses_when_eq_matches(self, manager):
        m1, new1 = manager.acquire({"group": "alpha"}, session_name="step-a")
        m2, new2 = manager.acquire({"group": "alpha"}, session_name="step-b")
        assert m1 is m2
        assert new1 is True
        assert new2 is False
        assert len(manager.active) == 1
        assert m1.session_names == {"step-a", "step-b"}

    def test_creates_new_when_eq_doesnt_match(self, manager):
        m1, new1 = manager.acquire({"group": "alpha"})
        m2, new2 = manager.acquire({"group": "beta"})
        assert m1 is not m2
        assert new1 is True
        assert new2 is True
        assert len(manager.active) == 2
        assert m1.resource.config_id == "alpha"
        assert m2.resource.config_id == "beta"

    def test_session_name_tracked(self, manager):
        m, _ = manager.acquire({"group": "alpha"}, session_name="step-x")
        assert "step-x" in m.session_names

    def test_no_session_name(self, manager):
        m, _ = manager.acquire({"group": "alpha"})
        assert m.session_names == set()


# ── release_if_unused() ──────────────────────────────────────────────


class TestReleaseIfUnused:
    def test_tears_down_unused(self, manager):
        m, _ = manager.acquire({"group": "alpha"})
        resource = m.resource

        # No remaining steps use "alpha"
        manager.release_if_unused(lambda config: False)
        assert not resource.alive
        assert len(manager.active) == 0

    def test_keeps_still_needed(self, manager):
        m, _ = manager.acquire({"group": "alpha"})
        resource = m.resource

        # Still has future steps
        manager.release_if_unused(lambda config: True)
        assert resource.alive
        assert len(manager.active) == 1

    def test_mixed_release(self, manager):
        m_alpha, _ = manager.acquire({"group": "alpha"})
        m_beta, _ = manager.acquire({"group": "beta"})

        # Only beta has future steps
        manager.release_if_unused(
            lambda config: config.get("group") == "beta"
        )

        assert not m_alpha.resource.alive
        assert m_beta.resource.alive
        assert len(manager.active) == 1
        assert manager.active[0].resource.config_id == "beta"


# ── release_all() ─────────────────────────────────────────────────────


class TestReleaseAll:
    def test_tears_down_everything(self, manager):
        m1, _ = manager.acquire({"group": "alpha"})
        m2, _ = manager.acquire({"group": "beta"})

        manager.release_all()

        assert not m1.resource.alive
        assert not m2.resource.alive
        assert len(manager.active) == 0

    def test_handles_teardown_error(self):
        """Teardown errors don't prevent cleaning up remaining resources."""
        call_count = [0]

        def _bad_teardown(r: FakeResource) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("boom")
            r.alive = False

        mgr = ResourceLifecycleManager(
            is_eq=_is_eq,
            factory=_factory,
            teardown=_bad_teardown,
        )
        mgr.acquire({"group": "alpha"})
        m2, _ = mgr.acquire({"group": "beta"})

        mgr.release_all()  # Should not raise

        assert len(mgr.active) == 0
        assert call_count[0] == 2
        assert not m2.resource.alive


# ── find() ────────────────────────────────────────────────────────────


class TestFind:
    def test_finds_matching(self, manager):
        m, _ = manager.acquire({"group": "alpha"})
        found = manager.find({"group": "alpha"})
        assert found is m

    def test_returns_none_for_no_match(self, manager):
        manager.acquire({"group": "alpha"})
        assert manager.find({"group": "beta"}) is None

    def test_find_does_not_create(self, manager):
        result = manager.find({"group": "alpha"})
        assert result is None
        assert len(manager.active) == 0


# ── Multiple sessions on one resource ─────────────────────────────────


class TestMultipleSessions:
    def test_accumulates_session_names(self, manager):
        m, _ = manager.acquire({"group": "alpha"}, session_name="s1")
        manager.acquire({"group": "alpha"}, session_name="s2")
        manager.acquire({"group": "alpha"}, session_name="s3")

        assert m.session_names == {"s1", "s2", "s3"}
        assert len(manager.active) == 1


# ── discard() — for cleaning up after post-acquire setup failures ────


class TestDiscard:
    def test_tears_down_and_removes(self, manager):
        m, _ = manager.acquire({"group": "alpha"})
        resource = m.resource
        manager.discard(m)
        assert not resource.alive
        assert len(manager.active) == 0

    def test_safe_when_already_removed(self, manager):
        m, _ = manager.acquire({"group": "alpha"})
        manager.discard(m)
        manager.discard(m)  # should not raise


# ── release_for_job() — reference-counted teardown ───────────────────


class TestReleaseForJob:
    def test_single_job_drains_resource(self, manager):
        """Resource acquired for one job is torn down when that job releases."""
        m, _ = manager.acquire({"group": "alpha"}, job_id="job-A")
        resource = m.resource
        assert m.job_refs == {"job-A"}
        assert m.job_scoped is True

        manager.release_for_job("job-A")

        assert not resource.alive
        assert len(manager.active) == 0

    def test_shared_across_jobs_survives_first_release(self, manager):
        """Two jobs sharing a resource; first release leaves it alive."""
        m1, _ = manager.acquire({"group": "alpha"}, job_id="job-A")
        m2, _ = manager.acquire({"group": "alpha"}, job_id="job-B")
        assert m1 is m2
        assert m1.job_refs == {"job-A", "job-B"}

        manager.release_for_job("job-A")

        assert m1.resource.alive
        assert m1.job_refs == {"job-B"}
        assert len(manager.active) == 1

        manager.release_for_job("job-B")

        assert not m1.resource.alive
        assert len(manager.active) == 0

    def test_unscoped_resource_not_torn_down(self, manager):
        """Resource acquired without job_id is not affected by release_for_job."""
        m, _ = manager.acquire({"group": "alpha"})  # no job_id
        assert m.job_scoped is False

        manager.release_for_job("job-A")

        assert m.resource.alive
        assert len(manager.active) == 1

    def test_release_for_unknown_job_is_noop(self, manager):
        """Releasing a job_id that doesn't reference anything is safe."""
        m, _ = manager.acquire({"group": "alpha"}, job_id="job-A")

        manager.release_for_job("job-X")  # never acquired

        assert m.resource.alive
        assert m.job_refs == {"job-A"}

    def test_mixed_scoped_and_unscoped(self, manager):
        """Scoped and unscoped resources in same pool — only scoped gets cleaned."""
        scoped, _ = manager.acquire({"group": "alpha"}, job_id="job-A")
        unscoped, _ = manager.acquire({"group": "beta"})  # no job_id

        manager.release_for_job("job-A")

        assert not scoped.resource.alive
        assert unscoped.resource.alive
        assert len(manager.active) == 1
        assert manager.active[0] is unscoped

    def test_teardown_error_does_not_break_cleanup(self):
        """Teardown errors during release_for_job don't corrupt active list."""
        def _bad_teardown(r: FakeResource) -> None:
            raise RuntimeError("boom")

        mgr = ResourceLifecycleManager(
            is_eq=_is_eq,
            factory=_factory,
            teardown=_bad_teardown,
        )
        mgr.acquire({"group": "alpha"}, job_id="job-A")

        mgr.release_for_job("job-A")  # should not raise

        assert len(mgr.active) == 0

    def test_acquire_then_add_job_ref(self, manager):
        """Subsequent acquire adds job_id to existing resource's refs."""
        m, _ = manager.acquire({"group": "alpha"}, job_id="job-A")
        m2, was_new = manager.acquire({"group": "alpha"}, job_id="job-B")

        assert m is m2
        assert was_new is False
        assert m.job_refs == {"job-A", "job-B"}


# ── ResourceManager protocol conformance ─────────────────────────────


class TestProtocolConformance:
    def test_lifecycle_manager_is_resource_manager(self, manager):
        """ResourceLifecycleManager satisfies the ResourceManager protocol."""
        assert isinstance(manager, ResourceManager)


# ── Thread safety — race-free acquire ─────────────────────────────────


class TestThreadSafety:
    def test_concurrent_same_config_no_double_spawn(self):
        """Multiple threads racing on the same config get the same resource."""
        import threading

        spawn_count = [0]

        def _counting_factory(config: dict) -> FakeResource:
            spawn_count[0] += 1
            # Brief sleep widens the race window so the test is meaningful
            import time as _t
            _t.sleep(0.01)
            return FakeResource(config.get("group", "default"))

        mgr = ResourceLifecycleManager(
            is_eq=_is_eq,
            factory=_counting_factory,
            teardown=_teardown,
        )

        results: list = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def _worker(i: int) -> None:
            barrier.wait()
            m, was_new = mgr.acquire({"group": "alpha"}, session_name=f"s{i}")
            with results_lock:
                results.append((m, was_new))

        threads = [
            threading.Thread(target=_worker, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one factory call, all threads share the same resource,
        # exactly one thread gets was_new=True.
        assert spawn_count[0] == 1
        assert len({id(m) for m, _ in results}) == 1
        assert sum(1 for _, was_new in results if was_new) == 1
        assert len(mgr.active) == 1
