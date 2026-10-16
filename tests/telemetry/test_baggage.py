import subprocess
import sys
from types import SimpleNamespace

from maas_model import telemetry
from maas_model.telemetry import _state


def Msg(headers):
    return SimpleNamespace(
        headers=headers,
        delivery_info={"exchange": "collect-exchange", "routing_key": "rk"},
    )


def test_baggage_propagates_through_amqp(telemetry_capture):
    with telemetry.span("collect.file"), telemetry.baggage(
        {"maas.origin.interface": "PRIP_S1"}
    ):
        with telemetry.amqp.publish_span("collect-exchange", "rk") as headers:
            assert "baggage" in headers
    with telemetry.amqp.consume_span(Msg(headers), queue="q") as cs:
        cs.outcome("ack")
    span = telemetry_capture.span("process q")
    assert span.attributes["maas.origin.interface"] == "PRIP_S1"


def test_baggage_detaches_on_exit_and_coerces(telemetry_capture):
    from opentelemetry import baggage

    with telemetry.baggage({"a.b": 1}, c="x"):
        assert baggage.get_baggage("a.b") == "1"
        assert baggage.get_baggage("c") == "x"
    assert baggage.get_baggage("a.b") is None


def test_no_baggage_no_attribute(telemetry_capture):
    with telemetry.amqp.publish_span("e", "rk") as headers:
        pass
    with telemetry.amqp.consume_span(Msg(headers), queue="q") as cs:
        cs.outcome("ack")
    assert "maas.origin.interface" not in telemetry_capture.span("process q").attributes


def test_baggage_disabled_is_noop():
    _state.reset()
    first = telemetry.baggage(a="b")
    with first:
        pass
    assert telemetry.baggage({"x.y": "z"}) is first
    with first:
        with first:
            pass


def test_baggage_disabled_imports_no_opentelemetry():
    code = (
        "import sys\n"
        "from maas_model import telemetry\n"
        "with telemetry.baggage(a='b'):\n    pass\n"
        "assert 'opentelemetry' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_baggage_reentrant(telemetry_capture):
    from opentelemetry import baggage

    bag = telemetry.baggage(a="1")
    with bag:
        with bag:
            assert baggage.get_baggage("a") == "1"
        assert baggage.get_baggage("a") == "1"
    assert baggage.get_baggage("a") is None


def test_baggage_detached_when_body_raises(telemetry_capture):
    from opentelemetry import baggage

    try:
        with telemetry.baggage(a="1"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert baggage.get_baggage("a") is None


def test_nested_baggage_restores_outer(telemetry_capture):
    from opentelemetry import baggage

    with telemetry.baggage(a="outer", b="x"):
        with telemetry.baggage(a="inner"):
            assert baggage.get_baggage("a") == "inner"
            assert baggage.get_baggage("b") == "x"
        assert baggage.get_baggage("a") == "outer"


def test_consume_ignores_unrelated_baggage(telemetry_capture):
    with telemetry.baggage(other="v"):
        with telemetry.amqp.publish_span("e", "rk") as headers:
            pass
    with telemetry.amqp.consume_span(Msg(headers), queue="q") as cs:
        cs.outcome("ack")
    attrs = telemetry_capture.span("process q").attributes
    assert "maas.origin.interface" not in attrs
    assert "other" not in attrs


def test_republish_inside_consume_carries_origin(telemetry_capture):
    with telemetry.baggage({"maas.origin.interface": "PRIP_S1"}):
        with telemetry.amqp.publish_span("e", "rk") as first:
            pass
    with telemetry.amqp.consume_span(Msg(first), queue="q") as cs:
        with telemetry.amqp.publish_span("e2", "rk2") as second:
            assert "maas.origin.interface=PRIP_S1" in second["baggage"]
        cs.outcome("ack")
