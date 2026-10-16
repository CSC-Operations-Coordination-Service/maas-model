"""
Baggage for the MAAS services: key/value pairs that travel with the trace.

    with baggage({"maas.origin.interface": "PRIP_S1"}):
        with amqp.publish_span(...) as headers:
            ...

Nothing here imports opentelemetry at module level: it is imported lazily, and
only once telemetry is enabled. Nothing here raises into the caller.
"""

import logging
from typing import Mapping, Optional

from maas_model.telemetry._state import STATE

LOGGER = logging.getLogger("Telemetry")


class _NoopBaggage:
    """the context manager handed out while telemetry is disabled"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


_NOOP_BAGGAGE = _NoopBaggage()


class _Baggage:
    """attach items as baggage on the current context, detach on exit"""

    def __init__(self, items: dict):
        self._items = items
        self._token = None

    def __enter__(self):
        try:
            from opentelemetry import baggage, context

            ctx = context.get_current()
            for key, value in self._items.items():
                ctx = baggage.set_baggage(key, value, context=ctx)
            self._token = context.attach(ctx)
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("baggage attach failed", exc_info=True)
        return self

    def __exit__(self, exc_type, exc, traceback):
        token, self._token = self._token, None
        if token is not None:
            try:
                from opentelemetry import context

                context.detach(token)
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("baggage detach failed", exc_info=True)
        return False


def baggage(mapping: Optional[Mapping] = None, /, **items):
    """
    context manager attaching items as baggage to the current context

    Keys may contain dots, so pass them in the positional mapping. Values are
    made strings. Disabled, it returns a reusable no-op object.
    """
    if not STATE.enabled:
        return _NOOP_BAGGAGE
    try:
        merged = {**(mapping or {}), **items}
        return _Baggage({str(k): str(v) for k, v in merged.items()})
    except Exception:  # pylint: disable=broad-except
        return _NOOP_BAGGAGE
