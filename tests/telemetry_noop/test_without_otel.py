"""
The API must work, and import nothing, without opentelemetry installed.

Skipped where opentelemetry is installed: tox -e no-otel runs it without.
"""

import importlib.util
import sys

import pytest


def test_api_without_opentelemetry():
    if importlib.util.find_spec("opentelemetry") is not None:
        pytest.skip("opentelemetry installed; covered by tox -e no-otel")

    from maas_model import telemetry

    assert telemetry.configure("engine", enabled=True) is False
    assert telemetry.enabled() is False

    with telemetry.span("x") as span:
        span.set_attribute("k", 1)
    telemetry.counter("maas.business.x").add(1)

    @telemetry.record_latency("maas.business.l", start="a", end="@now", on="return")
    def func():
        return {"a": "2026-10-16T00:00:00Z"}

    assert func() == {"a": "2026-10-16T00:00:00Z"}

    with telemetry.amqp.publish_span("e", "rk") as headers:
        assert headers == {}
    with telemetry.baggage(a="b"):
        pass
    with telemetry.baggage({"maas.origin.interface": "x"}):
        pass
    telemetry.topology.declare_route(
        direction="out",
        node_type="collector",
        node="n",
        exchange="e",
        routing_key="rk",
    )

    assert "opentelemetry" not in sys.modules
