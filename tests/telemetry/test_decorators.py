import asyncio
import datetime
from types import SimpleNamespace

import pytest

from maas_model import telemetry
from maas_model.telemetry import _state


def test_timed_records_outcome(telemetry_capture):
    @telemetry.timed("maas.business.t.dur", attributes={"mission": "S1"})
    def f(fail=False):
        if fail:
            raise RuntimeError

    f()
    pytest.raises(RuntimeError, f, True)
    assert telemetry_capture.point("maas.business.t.dur", outcome="ok").count == 1
    assert telemetry_capture.point("maas.business.t.dur", outcome="error").count == 1
    assert telemetry_capture.point("maas.business.t.dur", mission="S1", outcome="ok")


def test_timed_attributes_from(telemetry_capture):
    @telemetry.timed("maas.business.t.af", attributes_from=lambda x, **k: {"kind": x})
    def f(x):
        return x

    f("a")
    assert telemetry_capture.point("maas.business.t.af", kind="a").count == 1


def test_timed_async_and_generator(telemetry_capture):
    @telemetry.timed("maas.business.t.async")
    async def f():
        return 5

    @telemetry.timed("maas.business.t.gen")
    def g():
        yield 1
        yield 2

    assert asyncio.run(f()) == 5
    assert list(g()) == [1, 2]
    assert telemetry_capture.point("maas.business.t.async", outcome="ok").count == 1
    assert telemetry_capture.point("maas.business.t.gen", outcome="ok").count == 1


def test_timed_generator_lazy_and_error(telemetry_capture):
    @telemetry.timed("maas.business.t.lazy")
    def g():
        yield 1
        raise ValueError

    it = g()
    assert telemetry_capture.points("maas.business.t.lazy") == []
    assert next(it) == 1
    with pytest.raises(ValueError):
        next(it)
    assert telemetry_capture.point("maas.business.t.lazy", outcome="error").count == 1


def test_timed_rejects_bad_name_and_label():
    with pytest.raises(ValueError):
        telemetry.timed("other.name")
    with pytest.raises(ValueError):
        telemetry.timed("maas.business.x", attributes={"file_id": "a"})


def test_counted_return_and_generator(telemetry_capture):
    @telemetry.counted("maas.business.c.ret", amount=len)
    def f():
        return [1, 2, 3]

    @telemetry.counted("maas.business.c.gen")
    def g():
        yield from range(4)

    f()
    assert list(g()) == [0, 1, 2, 3]
    assert telemetry_capture.point("maas.business.c.ret").value == 3
    assert telemetry_capture.point("maas.business.c.gen").value == 4


def test_counted_not_on_exception(telemetry_capture):
    @telemetry.counted("maas.business.c.err")
    def f():
        raise RuntimeError

    with pytest.raises(RuntimeError):
        f()
    assert telemetry_capture.points("maas.business.c.err") == []


def test_counted_async(telemetry_capture):
    @telemetry.counted("maas.business.c.async", attributes={"a": "b"})
    async def f():
        return 1

    asyncio.run(f())
    assert telemetry_capture.point("maas.business.c.async", a="b").value == 1


def test_record_latency_yield_documents(telemetry_capture):
    @telemetry.record_latency(
        "maas.business.pub.latency",
        start="publication_date",
        end="ingestionTime",
        on="yield",
        attributes={"mission": "mission"},
    )
    def docs():
        yield SimpleNamespace(
            publication_date="2026-10-16T10:00:00.000Z",
            ingestionTime="2026-10-16T10:05:00.000Z",
            mission="S1",
        )
        yield {
            "publication_date": "2026-10-16T10:00:00",
            "ingestionTime": "2026-10-16T12:00:00+02:00",
            "mission": "S2",
        }
        yield {
            "publication_date": None,
            "ingestionTime": "2026-10-16T10:00:00Z",
            "mission": "S1",
        }

    list(docs())
    assert telemetry_capture.point("maas.business.pub.latency", mission="S1").sum == 300
    assert telemetry_capture.point("maas.business.pub.latency", mission="S2").sum == 0


def test_record_latency_dotted_path_and_now(telemetry_capture):
    @telemetry.record_latency(
        "maas.business.now.latency", start="_source.ingestionTime", end="@now"
    )
    def actions():
        return [{"_source": {"ingestionTime": "2020-01-01T00:00:00Z"}}]

    actions()
    assert telemetry_capture.point("maas.business.now.latency").sum > 0


def test_record_latency_negative_counted(telemetry_capture):
    @telemetry.record_latency("maas.business.neg.latency", start="a", end="b")
    def f():
        return {"a": "2026-10-16T10:00:00Z", "b": "2026-10-16T09:00:00Z"}

    f()
    assert telemetry_capture.points("maas.business.neg.latency") == []
    assert (
        telemetry_capture.point("maas.latency.negative", metric="maas.business.neg.latency").value
        == 1
    )


def test_record_latency_unparseable_skipped(telemetry_capture):
    @telemetry.record_latency("maas.business.bad.latency", start="a", end="b")
    def f():
        return {"a": "not a date", "b": "2026-10-16T09:00:00Z"}

    f()
    assert telemetry_capture.points("maas.business.bad.latency") == []


def test_record_latency_callable_and_datetime(telemetry_capture):
    @telemetry.record_latency(
        "maas.business.cb.latency",
        start=lambda d: d["a"],
        end=lambda d: d["b"],
        attributes={"k": lambda d: "v"},
    )
    def f():
        return [{"a": datetime.datetime(2026, 1, 1), "b": "2026-01-01T00:00:10Z"}]

    f()
    assert telemetry_capture.point("maas.business.cb.latency", k="v").sum == 10


def test_record_latency_partial_generator(telemetry_capture):
    state = []

    @telemetry.record_latency(
        "maas.business.part.latency", start="a", end="b", on="yield"
    )
    def docs():
        try:
            while True:
                yield {"a": "2026-10-16T10:00:00Z", "b": "2026-10-16T10:00:01Z"}
        finally:
            state.append("closed")

    for _ in docs():
        break
    assert state == ["closed"]
    assert telemetry_capture.point("maas.business.part.latency").count == 1


def test_record_latency_arg_mode(telemetry_capture):
    @telemetry.record_latency(
        "maas.business.arg.latency", start="a", end="b", on="arg:documents"
    )
    def f(documents, other=None):
        return "done"

    doc = {"a": "2026-10-16T10:00:00Z", "b": "2026-10-16T10:00:01Z"}
    assert f(documents=[doc, doc]) == "done"
    assert telemetry_capture.point("maas.business.arg.latency").count == 2


def test_record_latency_rejects_bad_definitions():
    with pytest.raises(ValueError):
        telemetry.record_latency(
            "maas.business.x", start="a", end="b", attributes={"datatake_id": "datatake_id"}
        )
    with pytest.raises(ValueError):
        telemetry.record_latency("maas.business.x", start="a", end="b", on="later")
    with pytest.raises(ValueError):
        telemetry.record_latency("maas.business.x", start="a", end="b", on="arg:")
    with pytest.raises(ValueError):
        telemetry.record_latency("other", start="a", end="b")
    with pytest.raises(ValueError):

        @telemetry.record_latency("maas.business.x", start="a", end="b", on="yield")
        def f():
            return 1

    with pytest.raises(ValueError):

        @telemetry.record_latency(
            "maas.business.x", start="a", end="b", on="arg:missing"
        )
        def g(x):
            return 1


def test_disabled_is_transparent():
    _state.reset()

    @telemetry.timed("maas.business.d.t")
    def g():
        got = yield 1
        yield got

    @telemetry.counted("maas.business.d.c")
    def f():
        raise KeyError("x")

    @telemetry.record_latency("maas.business.d.l", start="a", end="b", on="yield")
    def h():
        try:
            yield 1
        except ZeroDivisionError:
            yield "caught"

    it = g()
    assert next(it) == 1
    assert it.send("sent") == "sent"
    with pytest.raises(KeyError):
        f()
    it = h()
    assert next(it) == 1
    assert it.throw(ZeroDivisionError) == "caught"
