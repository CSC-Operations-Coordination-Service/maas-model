"""
The node a service is working on, visible from worker threads.

A contextvar does not cross into pool threads, so activity() also pushes onto a
lock guarded process wide stack that current_activity() falls back to.
"""

import contextvars
import dataclasses
import threading
from typing import Any, List, Optional

from maas_model.telemetry._state import STATE
from maas_model.telemetry.tracing import current_span_context


@dataclasses.dataclass(frozen=True)
class Activity:
    """what is being worked on, and the span it is part of"""

    node_type: str
    node: str
    span_context: Optional[Any] = None


_current: "contextvars.ContextVar[Optional[Activity]]" = contextvars.ContextVar(
    "maas_telemetry_activity", default=None
)

_stack: List[Activity] = []

_lock = threading.Lock()


class _DisabledActivity:
    """reusable, does nothing and yields None"""

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False


_DISABLED = _DisabledActivity()


class _ActivityScope:
    def __init__(self, entry: Activity):
        self.entry = entry
        self.previous = None

    def __enter__(self):
        self.previous = _current.get()
        _current.set(self.entry)
        with _lock:
            _stack.append(self.entry)
        return self.entry

    def __exit__(self, exc_type, exc, traceback):
        with _lock:
            # remove this very entry, whatever its position
            for index in range(len(_stack) - 1, -1, -1):
                if _stack[index] is self.entry:
                    del _stack[index]
                    break
            previous_alive = self.previous is not None and any(
                item is self.previous for item in _stack
            )
        # only touch the contextvar if it still is this scope's entry, and
        # never bring back an activity that has already exited
        if _current.get() is self.entry:
            _current.set(self.previous if previous_alive else None)
        return False


def activity(node_type: str, node: str):
    """context manager yielding an Activity, or None when disabled"""
    if not STATE.enabled:
        return _DISABLED
    return _ActivityScope(Activity(node_type, node, current_span_context()))


def current_activity() -> Optional[Activity]:
    """the contextvar activity, else the top of the process stack, else None"""
    if not STATE.enabled:
        return None
    found = _current.get()
    if found is not None:
        return found
    with _lock:
        return _stack[-1] if _stack else None
