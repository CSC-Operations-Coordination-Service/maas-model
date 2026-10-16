import asyncio

import pytest
from opentelemetry.trace import SpanKind, StatusCode

from maas_model import telemetry


def test_span_nesting_and_kind(telemetry_capture):
    with telemetry.span("parent"):
        with telemetry.span("child", kind="producer", attributes={"a": 1}):
            pass
    c, p = telemetry_capture.span("child"), telemetry_capture.span("parent")
    assert c.parent.span_id == p.context.span_id
    assert c.kind == SpanKind.PRODUCER and c.attributes["a"] == 1


def test_span_records_exception_and_reraises(telemetry_capture):
    with pytest.raises(KeyError):
        with telemetry.span("boom"):
            raise KeyError("x")
    s = telemetry_capture.span("boom")
    assert s.status.status_code == StatusCode.ERROR
    assert s.events[0].name == "exception"


def test_links(telemetry_capture):
    with telemetry.span("first"):
        ctx = telemetry.current_span_context()
    with telemetry.span("second", links=[ctx]):
        pass
    assert telemetry_capture.span("second").links[0].context.trace_id == ctx.trace_id


def test_parent_context(telemetry_capture):
    from opentelemetry import trace

    with telemetry.span("root") as root:
        parent = trace.set_span_in_context(root)
    with telemetry.span("late", parent=parent):
        pass
    late = telemetry_capture.span("late")
    assert late.parent.span_id == telemetry_capture.span("root").context.span_id


def test_set_span_attributes(telemetry_capture):
    with telemetry.span("s"):
        telemetry.set_span_attributes(k="v")
    assert telemetry_capture.span("s").attributes["k"] == "v"


def test_traced_sync_async_generator(telemetry_capture):
    @telemetry.traced()
    def sync_fn():
        return 1

    @telemetry.traced()
    async def async_fn():
        return 2

    @telemetry.traced()
    def gen_fn():
        yield 1
        yield 2

    assert sync_fn() == 1
    assert asyncio.run(async_fn()) == 2
    g = gen_fn()
    names = [s.name for s in telemetry_capture.spans()]
    assert not any("gen_fn" in n for n in names)
    assert list(g) == [1, 2]
    names = [s.name for s in telemetry_capture.spans()]
    assert any(n.endswith("sync_fn") for n in names)
    assert any(n.endswith("async_fn") for n in names)
    assert any(n.endswith("gen_fn") for n in names)
    assert telemetry_capture.span(gen_fn.__qualname__)


def test_disabled_span_is_noop():
    with telemetry.span("x") as s:
        s.set_attribute("k", 1)
    assert telemetry.current_span_context() is None
    telemetry.set_span_attributes(a=1)


def test_bad_kind_raises():
    with pytest.raises(ValueError):
        telemetry.span("x", kind="nope")


def test_bare_traced_decorator(telemetry_capture):
    @telemetry.traced
    def bare():
        return 1

    assert bare() == 1
    assert telemetry_capture.span(bare.__qualname__)
