from types import SimpleNamespace

import pytest

from maas_model import telemetry
from maas_model.telemetry import _state


def Msg(headers, rk="new.raw.data.x", ex="collect-exchange", redelivered=False):
    return SimpleNamespace(
        headers=headers,
        delivery_info={"exchange": ex, "routing_key": rk, "redelivered": redelivered},
    )


def test_publish_then_consume_same_trace(telemetry_capture):
    with telemetry.amqp.publish_span(
        "collect-exchange", "new.raw.data.x", message_id="m1", document_count=3
    ) as headers:
        assert "traceparent" in headers
    with telemetry.amqp.consume_span(
        Msg(headers),
        queue="q1",
        body={"message_id": "m1", "document_ids": ["a", "b", "c"]},
    ) as cs:
        cs.outcome("ack")
    pub = telemetry_capture.span("publish collect-exchange")
    con = telemetry_capture.span("process q1")
    assert con.context.trace_id == pub.context.trace_id
    assert con.parent.span_id == pub.context.span_id
    assert con.attributes["maas.message_id"] == "m1"
    assert con.attributes["maas.message.outcome"] == "ack"
    assert (
        telemetry_capture.point(
            "maas.messages.consumed", queue="q1", outcome="ack", redelivered="false"
        ).value
        == 1
    )
    assert telemetry_capture.point("maas.message.documents", direction="in").sum == 3
    assert telemetry_capture.point("maas.message.documents", direction="out").sum == 3
    assert (
        telemetry_capture.point("maas.messages.published", outcome="ok").value == 1
    )
    assert telemetry_capture.point("maas.message.processing.duration", queue="q1")


@pytest.mark.parametrize("headers", [None, {}])
def test_consume_without_headers_is_root(telemetry_capture, headers):
    with telemetry.amqp.consume_span(Msg(headers), queue="q1") as cs:
        cs.outcome("ack")
    assert telemetry_capture.span("process q1").parent is None


def test_consume_none_delivery_info_and_headers(telemetry_capture):
    msg = SimpleNamespace(headers=None, delivery_info=None)
    with telemetry.amqp.consume_span(msg, queue="q1", body={"document_ids": 5}):
        pass
    with telemetry.amqp.consume_span(
        msg, queue="q1", body={"pipeline": "x", "ancestor_ids": 3}
    ):
        pass
    assert telemetry_capture.point("maas.messages.consumed", outcome="unknown").value == 2


def test_consume_non_dict_body(telemetry_capture):
    with telemetry.amqp.consume_span(Msg({}), queue="q1", body="raw string") as cs:
        cs.outcome("reject", "bad")
    assert (
        telemetry_capture.point(
            "maas.messages.consumed", outcome="reject", reason="bad"
        ).value
        == 1
    )


def test_consume_body_attributes(telemetry_capture):
    body = {
        "message_id": "m",
        "document_ids": ["a"],
        "document_class": "Foo",
        "pipeline": ["p1", "p2"],
        "ancestor_ids": ["x", "y"],
    }
    with telemetry.amqp.consume_span(Msg({}, redelivered=True), queue="q1", body=body):
        pass
    attrs = telemetry_capture.span("process q1").attributes
    assert attrs["maas.document.count"] == 1
    assert attrs["maas.document.class"] == "Foo"
    assert attrs["maas.pipeline"] == "p1,p2"
    assert attrs["maas.message.ancestor_ids"] == "x,y"
    assert attrs["messaging.destination.subscription.name"] == "q1"
    telemetry_capture.point("maas.messages.consumed", redelivered="true")


def test_consume_exception_outcome_error(telemetry_capture):
    with pytest.raises(RuntimeError):
        with telemetry.amqp.consume_span(Msg({}), queue="q1"):
            raise RuntimeError("boom")
    telemetry_capture.point(
        "maas.messages.consumed", outcome="error", reason="exception"
    )


def test_consume_exception_keeps_explicit_outcome(telemetry_capture):
    with pytest.raises(RuntimeError):
        with telemetry.amqp.consume_span(Msg({}), queue="q1") as cs:
            cs.outcome("requeue", "retry")
            raise RuntimeError("boom")
    telemetry_capture.point("maas.messages.consumed", outcome="requeue", reason="retry")


def test_publish_error_outcome(telemetry_capture):
    with pytest.raises(ValueError):
        with telemetry.amqp.publish_span("ex", "rk"):
            raise ValueError("x")
    assert telemetry_capture.point("maas.messages.published", outcome="error").value == 1


def test_returned_counter(telemetry_capture):
    telemetry.amqp.returned("ex", "rk", "NO_ROUTE")
    assert (
        telemetry_capture.point("maas.messages.returned", exchange="ex", reason="NO_ROUTE").value
        == 1
    )


def test_extract_robust(telemetry_capture):
    assert telemetry.amqp.extract(None) is None
    assert telemetry.amqp.extract({}) is None


def test_inject_enabled(telemetry_capture):
    with telemetry.span("s"):
        assert "traceparent" in telemetry.amqp.inject({"a": 1})
        assert telemetry.amqp.inject()["traceparent"]


def test_documents_histogram_unit(telemetry_capture):
    with telemetry.amqp.publish_span("ex", "rk", document_count=2):
        pass
    data = telemetry_capture.metric_reader.get_metrics_data()
    units = {
        m.name: m.unit
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert units["maas.message.documents"] == "{document}"
    assert units["maas.messages.published"] == "{message}"


def test_disabled_paths():
    _state.reset()
    assert telemetry.amqp.inject({"a": 1}) == {"a": 1}
    assert telemetry.amqp.inject(None) == {}
    assert telemetry.amqp.extract({"traceparent": "x"}) is None
    with telemetry.amqp.publish_span("ex", "rk", document_count=1) as headers:
        assert headers == {}
    with telemetry.amqp.consume_span(Msg(None), queue="q", body={}) as cs:
        cs.outcome("ack")
    telemetry.amqp.returned("ex", "rk", "r")


def test_publish_span_merges_headers(telemetry_capture):
    given = {"maas.replay": "true"}
    with telemetry.amqp.publish_span("e", "rk", headers=given) as headers:
        assert headers["maas.replay"] == "true"
        assert "traceparent" in headers
    assert given == {"maas.replay": "true"}


def test_publish_span_trace_keys_win(telemetry_capture):
    given = {"traceparent": "stale"}
    with telemetry.amqp.publish_span("e", "rk", headers=given) as headers:
        assert headers["traceparent"] != "stale"
    assert given == {"traceparent": "stale"}


def test_publish_span_drops_stale_trace_keys(telemetry_capture):
    given = {"traceparent": "x", "tracestate": "y", "baggage": "z=1", "k": "v"}
    with telemetry.amqp.publish_span("e", "rk", headers=given) as headers:
        assert headers["traceparent"] != "x"
        assert "tracestate" not in headers
        assert "baggage" not in headers
        assert headers["k"] == "v"


def test_publish_span_disabled_returns_copy_of_headers():
    _state.reset()
    given = {"x": "1"}
    with telemetry.amqp.publish_span("e", "rk", headers=given) as headers:
        assert headers == {"x": "1"}
        assert headers is not given


def test_publish_span_new_trace_is_a_root_with_links(telemetry_capture):
    with telemetry.span("outer") as outer:
        link = telemetry.current_span_context()
        with telemetry.amqp.publish_span(
            "e", "rk", links=[link], new_trace=True
        ) as headers:
            pass
    pub = telemetry_capture.span("publish e")
    assert pub.parent is None
    assert len(pub.links) == 1
    assert pub.context.trace_id != telemetry_capture.span("outer").context.trace_id
    assert format(pub.context.trace_id, "032x") in headers["traceparent"]


def test_publish_span_default_is_child(telemetry_capture):
    with telemetry.span("outer"):
        with telemetry.amqp.publish_span("e", "rk"):
            pass
    pub = telemetry_capture.span("publish e")
    assert pub.parent.span_id == telemetry_capture.span("outer").context.span_id


def test_publish_span_new_trace_disabled_is_harmless():
    _state.reset()
    with telemetry.amqp.publish_span("e", "rk", new_trace=True) as headers:
        assert headers == {}
