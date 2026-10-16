import logging
import os
import socket
import threading
import time

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from maas_model import telemetry
from maas_model.telemetry import _noop, _state, semconv
from maas_model.telemetry.testing import capture


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MAAS_TELEMETRY", raising=False)
    assert telemetry.configure("engine") is False
    assert telemetry.enabled() is False


def test_enabled_from_env(monkeypatch):
    monkeypatch.setenv("MAAS_TELEMETRY", "TRUE")
    assert (
        telemetry.configure(
            "engine",
            span_exporter=InMemorySpanExporter(),
            metric_reader=InMemoryMetricReader(),
        )
        is True
    )


def test_explicit_disable_wins_over_env(monkeypatch):
    monkeypatch.setenv("MAAS_TELEMETRY", "1")
    assert telemetry.configure("engine", enabled=False) is False


def test_resource_attributes():
    capture(
        component="collector",
        deployment="dep",
        instance_id="i-1",
        collector_type="odata",
        project_version="9.9",
    )
    res = _state.STATE.tracer_provider.resource.attributes
    assert res["service.namespace"] == "maas"
    assert res["service.name"] == "maas-collector"
    assert res["service.instance.id"] == "i-1"
    assert res["maas.deployment"] == "dep"
    assert res["maas.collector.type"] == "odata"
    assert res["service.version"] == "9.9"
    assert res["maas.component"] == "collector"
    assert _state.STATE.base_attributes == {
        "component": "collector",
        "deployment": "dep",
    }


def test_instance_id_defaults_to_host_pid(monkeypatch):
    monkeypatch.delenv("MAAS_INSTANCE_ID", raising=False)
    capture()
    res = _state.STATE.tracer_provider.resource.attributes
    assert res["service.instance.id"] == f"{socket.gethostname()}-{os.getpid()}"


def test_instance_id_and_deployment_from_env(monkeypatch):
    monkeypatch.setenv("MAAS_INSTANCE_ID", "env-i")
    monkeypatch.setenv("MAAS_DEPLOYMENT", "env-dep")
    capture()
    res = _state.STATE.tracer_provider.resource.attributes
    assert res["service.instance.id"] == "env-i"
    assert res["maas.deployment"] == "env-dep"


def test_service_name_from_env(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "custom")
    capture()
    assert _state.STATE.tracer_provider.resource.attributes["service.name"] == "custom"


def test_collector_type_only_when_given():
    capture()
    assert "maas.collector.type" not in (
        _state.STATE.tracer_provider.resource.attributes
    )


def test_configure_is_idempotent():
    capture()
    provider = _state.STATE.tracer_provider
    assert telemetry.configure("other", enabled=True) is True
    assert _state.STATE.tracer_provider is provider


def test_project_is_stored():
    capture(project="maas-engine", project_version="1.2")
    assert _state.STATE.project == "maas-engine"
    assert _state.STATE.project_version == "1.2"


def test_reset_bumps_generation():
    capture()
    generation = _state.STATE.generation
    _state.reset()
    assert _state.STATE.generation == generation + 1
    assert _state.STATE.enabled is False
    assert _state.STATE.tracer_provider is None


def test_shutdown_resets_state():
    capture()
    telemetry.shutdown()
    assert telemetry.enabled() is False
    assert _state.STATE.meter is None


def test_shutdown_bounded_with_unreachable_endpoint(monkeypatch):
    # a closed port: the OTLP exporters keep retrying
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    telemetry.configure("engine", enabled=True)
    with _state.STATE.tracer.start_as_current_span("x"):
        pass
    t0 = time.monotonic()
    telemetry.shutdown(timeout_s=1)
    assert time.monotonic() - t0 < 2.5
    assert telemetry.enabled() is False


def test_noop_span():
    span = _noop.NOOP_SPAN
    span.set_attribute("a", 1)
    span.set_attributes({"a": 1})
    span.add_event("e", {"a": 1})
    span.record_exception(ValueError("x"))
    span.set_status("ok")
    assert span.is_recording() is False
    assert span.get_span_context() is None
    with span as entered:
        assert entered is span


def test_semconv_values():
    assert semconv.MESSAGES_PUBLISHED == "maas.messages.published"
    assert semconv.BUSINESS_PREFIX == "maas.business."
    assert semconv.DOCUMENT_BUCKETS == (1, 5, 10, 50, 100, 500, 1000, 5000)
    assert semconv.ATTR_MESSAGE_ID == "maas.message_id"
    for name in ("id", "message_id", "doc_ids", "file", "file_name"):
        assert semconv.ID_LABEL_DENYLIST.match(name)
    assert not semconv.ID_LABEL_DENYLIST.match("exchange")


def test_capture_points_and_spans():
    cap = capture()
    meter = _state.STATE.meter
    counter = meter.create_counter("c")
    counter.add(2, {"k": "a"})
    counter.add(3, {"k": "b"})
    hist = meter.create_histogram("h")
    hist.record(4, {"k": "a"})
    with _state.STATE.tracer.start_as_current_span("s"):
        pass
    assert len(cap.points("c")) == 2
    assert cap.point("c", k="b").value == 3
    h = cap.point("h")
    assert (h.count, h.sum, h.value) == (1, 4, None)
    assert cap.span("s").name == "s"
    assert len(cap.spans()) == 1
    cap.close()
    assert telemetry.enabled() is False


def test_telemetry_capture_fixture(telemetry_capture):
    assert telemetry.enabled() is True
    assert telemetry_capture.spans() == []


def _spy_reader(monkeypatch):
    """record the export interval configure() gives the periodic reader"""
    from opentelemetry.sdk.metrics import export

    real = export.PeriodicExportingMetricReader
    seen = {}

    def spy(exporter, export_interval_millis=None, **kwargs):
        seen["interval"] = export_interval_millis
        return real(exporter, export_interval_millis=export_interval_millis, **kwargs)

    monkeypatch.setattr(export, "PeriodicExportingMetricReader", spy)
    return seen


@pytest.mark.parametrize(
    "value, expected",
    [("0", 30000), ("-5", 30000), ("nope", 30000), ("15000", 15000)],
)
def test_export_interval_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", value)
    monkeypatch.setenv("MAAS_TELEMETRY", "1")
    seen = _spy_reader(monkeypatch)
    try:
        assert telemetry.configure("engine", span_exporter=InMemorySpanExporter())
        assert seen["interval"] == expected
    finally:
        telemetry.shutdown(timeout_s=1)


def _otel_threads():
    return [t for t in threading.enumerate() if t.name.startswith("Otel")]


def test_setup_failure_returns_false_and_leaks_nothing(monkeypatch, caplog):
    from opentelemetry.exporter.otlp.proto.http import metric_exporter

    def broken(*args, **kwargs):
        raise RuntimeError("exporter construction failed")

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(metric_exporter, "OTLPMetricExporter", broken)
    before = _otel_threads()
    with caplog.at_level(logging.WARNING, logger="Telemetry"):
        # the BatchSpanProcessor thread is started before the metric exporter fails
        assert telemetry.configure("engine", enabled=True) is False
    assert telemetry.enabled() is False
    assert _state.STATE.tracer_provider is None
    assert _otel_threads() == before
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_setup_failure_shuts_the_built_providers_down(monkeypatch):
    from opentelemetry.sdk import trace

    calls = []
    real_shutdown = trace.TracerProvider.shutdown

    def spy(self):
        calls.append(self)
        real_shutdown(self)

    def broken(*args, **kwargs):
        raise RuntimeError("meter provider construction failed")

    monkeypatch.setattr(trace.TracerProvider, "shutdown", spy)
    monkeypatch.setattr("opentelemetry.sdk.metrics.MeterProvider", broken)
    assert (
        telemetry.configure(
            "engine",
            enabled=True,
            span_exporter=InMemorySpanExporter(),
            metric_reader=InMemoryMetricReader(),
        )
        is False
    )
    assert len(calls) == 1
    assert telemetry.enabled() is False


def test_configure_enables_before_bumping_the_generation(monkeypatch):
    """an instrument resolved as the generation changes must not cache None"""
    from maas_model.telemetry.testing import Capture

    ctr = telemetry.counter("maas.business.order")
    _state.reset()
    armed = []

    def setattr_then_resolve(self, name, value):
        object.__setattr__(self, name, value)
        if armed and name == "generation":
            # another thread recording right after the generation changed
            ctr.add(1)

    monkeypatch.setattr(_state.State, "__setattr__", setattr_then_resolve)
    span_exporter, metric_reader = InMemorySpanExporter(), InMemoryMetricReader()
    armed.append(True)
    telemetry.configure(
        "test", enabled=True, span_exporter=span_exporter, metric_reader=metric_reader
    )
    armed.clear()
    cap = Capture(span_exporter, metric_reader)
    try:
        ctr.add(2)
        assert cap.point("maas.business.order").value == 3
    finally:
        cap.close()


def test_shutdown_cycles_leave_no_thread_behind(monkeypatch):
    """the OTLP exporters retry an unreachable endpoint: shutdown stops them"""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    before = set(threading.enumerate())
    baseline = threading.active_count()
    for _ in range(2):
        assert telemetry.configure("engine", enabled=True)
        with telemetry.span("x"):
            pass
        telemetry.counter("maas.business.cycle").add(1)
        t0 = time.monotonic()
        telemetry.shutdown(timeout_s=1)
        assert time.monotonic() - t0 < 2.5
    deadline = time.monotonic() + 3
    for thread in set(threading.enumerate()) - before:
        thread.join(max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in set(threading.enumerate()) - before]
    assert threading.active_count() <= baseline + 1, alive
