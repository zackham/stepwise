"""Event model and emission."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class EventType(Enum):
    JOB_CREATED = "job.created"
    JOB_STARTED = "job.started"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELLED = "job.cancelled"
    STEP_READY = "step.ready"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    STEP_SKIPPED = "step.skipped"
    STEP_RETRY = "step.retry"
    STEP_TIMEOUT = "step.timeout"


@dataclass
class Event:
    id: str
    job_id: str
    event_type: EventType
    step_name: str | None
    data: dict[str, Any]
    timestamp: datetime

    @classmethod
    def create(
        cls,
        job_id: str,
        event_type: EventType,
        step_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        return cls(
            id=str(uuid.uuid4()),
            job_id=job_id,
            event_type=event_type,
            step_name=step_name,
            data=data or {},
            timestamp=datetime.now(timezone.utc),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "event_type": self.event_type.value,
            "step_name": self.step_name,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        return cls(
            id=d["id"],
            job_id=d["job_id"],
            event_type=EventType(d["event_type"]),
            step_name=d.get("step_name"),
            data=d.get("data", {}),
            timestamp=datetime.fromisoformat(d["timestamp"]),
        )


class EventBus:
    """In-process event pub/sub."""

    def __init__(self) -> None:
        self._handlers: dict[EventType | None, list[Callable]] = {}
        self._history: list[Event] = []

    def subscribe(self, event_type: EventType | None, handler: Callable) -> None:
        """Subscribe to an event type. None subscribes to all events."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: Event) -> None:
        self._history.append(event)
        targets = list(self._handlers.get(event.event_type, []))
        targets.extend(self._handlers.get(None, []))
        for handler in targets:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)

    @property
    def history(self) -> list[Event]:
        return list(self._history)

    def get_events(
        self,
        job_id: str | None = None,
        event_type: EventType | None = None,
    ) -> list[Event]:
        events = self._history
        if job_id:
            events = [e for e in events if e.job_id == job_id]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events

    def clear(self) -> None:
        self._history.clear()
        self._handlers.clear()
