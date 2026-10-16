"""
Spans for the MAAS services.

Nothing here imports opentelemetry at module level: it is imported lazily, and
only once telemetry is enabled.
"""

import functools
import inspect
from typing import Any, Iterable, Mapping, Optional

from maas_model.telemetry._noop import NOOP_SPAN
from maas_model.telemetry._state import STATE

_KINDS = ("internal", "producer", "consumer", "client", "server")

_KIND_SET = frozenset(_KINDS)

_otel_trace = None


def _trace():
    """the opentelemetry.trace module, imported on first use"""
    global _otel_trace
    if _otel_trace is None:
        from opentelemetry import trace

        _otel_trace = trace
    return _otel_trace


def span(
    name: str,
    *,
    kind: str = "internal",
    attributes: Optional[Mapping] = None,
    links: Iterable[Any] = (),
    parent: Any = None,
):
    """
    context manager yielding a span, or NOOP_SPAN when telemetry is disabled

    kind is one of internal, producer, consumer, client, server. parent is an
    opentelemetry Context, links are SpanContext objects. An exception is
    recorded on the span, which is set to ERROR, and re-raised.
    """
    if kind not in _KIND_SET:
        raise ValueError(f"span kind must be one of {_KINDS}, not {kind!r}")
    if not STATE.enabled or STATE.tracer is None:
        return NOOP_SPAN
    try:
        trace = _trace()
        return STATE.tracer.start_as_current_span(
            name,
            context=parent,
            kind=getattr(trace.SpanKind, kind.upper()),
            attributes=dict(attributes) if attributes else None,
            links=[trace.Link(ctx) for ctx in links if ctx is not None] or None,
        )
    except Exception:  # never raise into the caller
        return NOOP_SPAN


def current_span_context():
    """the SpanContext of the current span, None if disabled or invalid"""
    if not STATE.enabled:
        return None
    try:
        context = _trace().get_current_span().get_span_context()
    except Exception:
        return None
    return context if context is not None and context.is_valid else None


def set_span_attributes(**attrs) -> None:
    """set attributes on the current span, if any"""
    if not STATE.enabled:
        return
    try:
        _trace().get_current_span().set_attributes(attrs)
    except Exception:
        pass


def traced(
    name: Optional[str] = None,
    *,
    kind: str = "internal",
    attributes: Optional[Mapping] = None,
):
    """
    decorator wrapping a sync, async or generator function in a span

    The span is named after the function qualname by default. For a generator
    it covers the first next() up to exhaustion or close. Usable bare, as
    @traced, or called, as @traced(...).
    """
    if callable(name):  # bare @traced: name is the decorated function
        return traced()(name)
    if kind not in _KINDS:
        raise ValueError(f"span kind must be one of {_KINDS}, not {kind!r}")

    def decorate(func):
        span_name = name or func.__qualname__

        def open_span():
            return span(span_name, kind=kind, attributes=attributes)

        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def agen_wrapper(*args, **kwargs):
                with open_span():
                    async for item in func(*args, **kwargs):
                        yield item

            return agen_wrapper

        if inspect.isgeneratorfunction(func):

            @functools.wraps(func)
            def gen_wrapper(*args, **kwargs):
                with open_span():
                    return (yield from func(*args, **kwargs))

            return gen_wrapper

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with open_span():
                    return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with open_span():
                return func(*args, **kwargs)

        return wrapper

    return decorate
