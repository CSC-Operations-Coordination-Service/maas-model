import logging
import sys

import pytest

from maas_model import telemetry
from maas_model.telemetry import _setup, _state
from maas_model.telemetry._instruments import _counter, _histogram
from maas_model.telemetry.testing import capture


def test_instrument_created_before_configure_records_after():
    c = telemetry.counter("maas.business.test.things")
    c.add(1, {"mission": "S1"})  # disabled, the proxy caches "disabled"
    cap = capture()
    try:
        c.add(2, {"mission": "S1"})
        p = cap.point("maas.business.test.things", mission="S1")
        assert p.value == 2
        assert p.attributes["component"] == "test"
        assert "deployment" in p.attributes
    finally:
        cap.close()


def test_public_name_must_be_business():
    with pytest.raises(ValueError):
        telemetry.counter("maas.engine.x")
    with pytest.raises(ValueError):
        telemetry.histogram("maas.engine.x")


def test_disabled_is_noop():
    telemetry.counter("maas.business.noop").add(1)
    telemetry.histogram("maas.business.noop.h").record(1.0)


def test_none_value_becomes_unknown(telemetry_capture):
    telemetry.counter("maas.business.none").add(1, {"mission": None})
    assert telemetry_capture.point("maas.business.none", mission="unknown")


def test_non_str_value_coerced(telemetry_capture):
    telemetry.counter("maas.business.coerce").add(1, {"n": 3})
    assert telemetry_capture.point("maas.business.coerce", n="3")


def test_id_labels_dropped(telemetry_capture):
    telemetry.counter("maas.business.ids").add(
        1, {"datatake_id": "X", "mission": "S1"}
    )
    p = telemetry_capture.point("maas.business.ids")
    assert p.attributes["mission"] == "S1"
    assert "datatake_id" not in p.attributes


def test_base_attributes_win(telemetry_capture):
    telemetry.counter("maas.business.base").add(1, {"component": "evil"})
    p = telemetry_capture.point("maas.business.base")
    assert p.attributes["component"] == "test"


def test_histogram_uses_boundaries(telemetry_capture):
    telemetry.histogram("maas.business.t", boundaries=(1, 10)).record(5)
    data = telemetry_capture.metric_reader.get_metrics_data()
    metric = [
        m
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        if m.name == "maas.business.t"
    ][0]
    assert tuple(metric.data.data_points[0].explicit_bounds) == (1, 10)


def test_internal_factories_skip_prefix_check(telemetry_capture):
    _counter("maas.engine.x").add(1)
    _histogram("maas.engine.y").record(0.5)
    assert telemetry_capture.point("maas.engine.x").value == 1
    assert telemetry_capture.point("maas.engine.y").count == 1


def test_cardinality_guard(caplog, telemetry_capture):
    cap = telemetry_capture
    c = telemetry.counter("maas.business.card")
    with caplog.at_level(logging.WARNING):
        for i in range(505):
            c.add(1, {"k": str(i)})
    assert len(cap.points("maas.business.card")) == 501
    assert cap.point("maas.business.card", k="other").value == 5
    warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "maas.business.card" in r.message
    ]
    assert len(warnings) == 1


def test_record_never_raises(telemetry_capture):
    telemetry.counter("maas.business.bad").add("not-a-number")
    telemetry.histogram("maas.business.badh").record("nope")


def test_exporter_import_error_returns_false(monkeypatch, caplog):
    _state.reset()
    monkeypatch.setitem(
        sys.modules,
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
        None,
    )
    with caplog.at_level(logging.WARNING):
        assert _setup.configure("engine", enabled=True) is False
    assert not telemetry.enabled()
    assert any(r.levelname == "WARNING" for r in caplog.records)
