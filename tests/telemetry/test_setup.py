import os
import socket
import time

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
