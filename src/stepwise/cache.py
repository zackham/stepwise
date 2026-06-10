"""Step result cache: content-addressable, project-scoped SQLite store."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from stepwise.models import ExecutorRef, HandoffEnvelope, Sidecar
from stepwise.store import (
    DatabaseIntegrityError,
    apply_pragmas,
    check_database_integrity,
)

logger = logging.getLogger(__name__)

# Keys injected by engine at runtime — excluded from cache key computation.
# NOTE: `working_dir` and `flow_dir` are deliberately NOT in this set. They
# change execution semantics (ScriptExecutor resolves relative commands
# against flow_dir and runs with cwd=working_dir), so two steps with the same
# command text but different directories must not share a cache entry.
RUNTIME_CONFIG_KEYS = frozenset({
    "_registry",
    "_config",
    "_depth_remaining",
    "_project_dir",
    "_prev_session_name",
    "_session_lock_manager",
    "_injected_contexts",
})

# Bump when the key derivation scheme changes. v2: working_dir/flow_dir are
# no longer stripped from the config and flow/step identity participates in
# the key — entries written under v1 could be shared across flows/dirs that
# resolve the same command text differently, so they must not be served.
CACHE_KEY_VERSION = 2

# Default TTL per executor type (seconds)
DEFAULT_TTL: dict[str, int] = {
    "script": 3600,      # 1 hour
    "llm": 86400,        # 24 hours
    "agent": 86400,      # 24 hours
    "callable": 3600,    # 1 hour
    "mock_llm": 3600,    # 1 hour
}

# Executor types that must never be cached
UNCACHEABLE_TYPES = frozenset({"external", "poll", "for_each", "sub_flow"})


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_cache_key(
    inputs: dict,
    exec_ref: ExecutorRef,
    engine_version: str,
    key_extra: str | None = None,
    *,
    flow_name: str | None = None,
    step_name: str | None = None,
) -> str:
    """Compute a deterministic SHA-256 cache key from step inputs and config.

    ``flow_name``/``step_name`` scope the key to a specific flow/step when
    provided. This weakens cross-step dedup but eliminates cross-flow
    contamination: identical ``run:`` command text in two different flows
    resolves (and executes) differently, so they must never share an entry.
    Callers that can supply identity should.
    """
    # Strip runtime-injected keys from executor config. working_dir and
    # flow_dir are kept (when present) — see RUNTIME_CONFIG_KEYS note.
    clean_config = {
        k: v for k, v in exec_ref.config.items()
        if k not in RUNTIME_CONFIG_KEYS
    }

    key_parts = {
        "key_version": CACHE_KEY_VERSION,
        "inputs": inputs,
        "executor_type": exec_ref.type,
        "executor_config": clean_config,
        "engine_version": engine_version,
        "key_extra": key_extra or "",
    }
    if flow_name is not None:
        key_parts["flow_name"] = flow_name
    if step_name is not None:
        key_parts["step_name"] = step_name
    canonical = json.dumps(key_parts, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class StepResultCache:
    """Project-scoped SQLite cache for step results."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        try:
            self._conn = self._open(db_path)
        except (DatabaseIntegrityError, sqlite3.Error) as exc:
            # The cache is a pure optimization — fail OPEN, unlike the main
            # store (which fails closed on corruption). A corrupt cache DB
            # must never block flow execution: recreate it from scratch.
            logger.warning(
                "Step result cache at %s is unusable (%s); recreating it",
                db_path, exc,
            )
            self._remove_db_files(db_path)
            try:
                self._conn = self._open(db_path)
            except (DatabaseIntegrityError, sqlite3.Error) as exc2:
                logger.warning(
                    "Recreating step result cache at %s failed (%s); "
                    "falling back to an in-memory cache for this process",
                    db_path, exc2,
                )
                self._db_path = ":memory:"
                self._conn = self._open(":memory:")

    @staticmethod
    def _open(db_path: str) -> sqlite3.Connection:
        """Open + harden the cache DB, raising on any corruption.

        Applies the same integrity check and PRAGMA hardening as the main
        store (see store.apply_pragmas — WAL autocheckpoint and journal size
        limits were added after the May 2026 btrfs WAL corruption incident,
        CHANGELOG 0.46.1/0.46.2).
        """
        check_database_integrity(db_path)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            apply_pragmas(conn)
            StepResultCache._create_tables(conn)
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    @staticmethod
    def _remove_db_files(db_path: str) -> None:
        """Delete the cache DB and its WAL/SHM sidecars (best-effort)."""
        if db_path == ":memory:":
            return
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "Failed to remove cache file %s: %s", db_path + suffix, exc
                )

    @staticmethod
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                key TEXT PRIMARY KEY,
                step_name TEXT NOT NULL,
                flow_name TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                hit_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_cache_step_flow
                ON cache_entries(step_name, flow_name);
        """)

    def get(self, key: str) -> HandoffEnvelope | None:
        """Look up a cache entry by key. Returns None on miss or expiry."""
        try:
            row = self._conn.execute(
                "SELECT result, expires_at FROM cache_entries WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None

            # Check expiry
            if row["expires_at"]:
                expires = datetime.fromisoformat(row["expires_at"])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expires:
                    # Expired — delete and return miss
                    self._conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                    self._conn.commit()
                    return None

            # Increment hit count
            self._conn.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1 WHERE key = ?",
                (key,),
            )
            self._conn.commit()

            return HandoffEnvelope.from_dict(json.loads(row["result"]))
        except Exception:
            logger.debug("Cache get failed for key %s", key[:16], exc_info=True)
            return None

    def put(
        self,
        key: str,
        step_name: str,
        flow_name: str,
        envelope: HandoffEnvelope,
        ttl_seconds: int | None = None,
    ) -> None:
        """Insert or update a cache entry."""
        try:
            now = datetime.now(timezone.utc)
            expires_at = None
            if ttl_seconds is not None:
                from datetime import timedelta
                expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

            self._conn.execute(
                """INSERT OR REPLACE INTO cache_entries
                   (key, step_name, flow_name, result, created_at, expires_at, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (key, step_name, flow_name,
                 _dumps(envelope.to_dict()), now.isoformat(), expires_at),
            )
            self._conn.commit()
        except Exception:
            logger.debug("Cache put failed for key %s", key[:16], exc_info=True)

    def batch_get(self, keys: list[str]) -> dict[str, HandoffEnvelope]:
        """Look up multiple cache entries. Returns dict of key → envelope for hits."""
        if not keys:
            return {}

        results: dict[str, HandoffEnvelope] = {}
        now = datetime.now(timezone.utc)
        expired_keys: list[str] = []

        # Query in batches to avoid SQLite variable limits
        for i in range(0, len(keys), 500):
            batch = keys[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = self._conn.execute(
                f"SELECT key, result, expires_at FROM cache_entries WHERE key IN ({placeholders})",
                batch,
            ).fetchall()

            for row in rows:
                if row["expires_at"]:
                    expires = datetime.fromisoformat(row["expires_at"])
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if now > expires:
                        expired_keys.append(row["key"])
                        continue

                try:
                    results[row["key"]] = HandoffEnvelope.from_dict(
                        json.loads(row["result"])
                    )
                except Exception:
                    logger.debug("Cache deserialize failed for key %s", row["key"][:16])

        # Clean up expired entries
        if expired_keys:
            placeholders = ",".join("?" * len(expired_keys))
            self._conn.execute(
                f"DELETE FROM cache_entries WHERE key IN ({placeholders})",
                expired_keys,
            )
            self._conn.commit()

        # Increment hit counts
        if results:
            hit_keys = list(results.keys())
            placeholders = ",".join("?" * len(hit_keys))
            self._conn.execute(
                f"UPDATE cache_entries SET hit_count = hit_count + 1 WHERE key IN ({placeholders})",
                hit_keys,
            )
            self._conn.commit()

        return results

    def clear(
        self,
        flow_name: str | None = None,
        step_name: str | None = None,
    ) -> int:
        """Delete cache entries. Returns count of deleted rows."""
        conditions: list[str] = []
        params: list[str] = []

        if flow_name is not None:
            conditions.append("flow_name = ?")
            params.append(flow_name)
        if step_name is not None:
            conditions.append("step_name = ?")
            params.append(step_name)

        if conditions:
            where = " WHERE " + " AND ".join(conditions)
        else:
            where = ""

        cursor = self._conn.execute(f"DELETE FROM cache_entries{where}", params)
        self._conn.commit()
        return cursor.rowcount

    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        total_hits = self._conn.execute(
            "SELECT COALESCE(SUM(hit_count), 0) FROM cache_entries"
        ).fetchone()[0]

        # Size on disk
        size_bytes = 0
        if self._db_path != ":memory:" and os.path.exists(self._db_path):
            size_bytes = os.path.getsize(self._db_path)

        # Per-flow breakdown
        by_flow = {}
        for row in self._conn.execute(
            "SELECT flow_name, COUNT(*) as cnt, SUM(hit_count) as hits "
            "FROM cache_entries GROUP BY flow_name"
        ).fetchall():
            by_flow[row["flow_name"] or "(unnamed)"] = {
                "entries": row["cnt"],
                "hits": row["hits"],
            }

        # Per-step breakdown
        by_step = {}
        for row in self._conn.execute(
            "SELECT step_name, COUNT(*) as cnt, SUM(hit_count) as hits "
            "FROM cache_entries GROUP BY step_name"
        ).fetchall():
            by_step[row["step_name"]] = {
                "entries": row["cnt"],
                "hits": row["hits"],
            }

        return {
            "total_entries": total,
            "total_hits": total_hits,
            "size_bytes": size_bytes,
            "by_flow": by_flow,
            "by_step": by_step,
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
