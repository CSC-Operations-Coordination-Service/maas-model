import pytest

from maas_model import telemetry
from maas_model.telemetry import semconv, topology
from maas_model.telemetry.testing import capture

ROUTE = dict(
    direction="in",
    node_type="engine",
    node="CONSOLIDATE_APS",
    exchange="collect-exchange",
    routing_key="new.raw.data.aps.#",
    queue="q-aps",
)


@pytest.fixture(autouse=True)
def _clean_registry():
    topology._reset_for_tests()
    yield
    topology._reset_for_tests()


def test_routes_exported_as_info(telemetry_capture):
    telemetry.topology.declare_route(**ROUTE)
    p = telemetry_capture.point(
        "maas.topology.route_info", node="CONSOLIDATE_APS", direction="in"
    )
    assert p.value == 1
    assert p.attributes["routing_key"] == "new.raw.data.aps.#"
    assert p.attributes["source"] == "config"
    assert p.attributes["component"] == "test"
    assert "deployment" in p.attributes


def test_duplicate_declare_is_single_series(telemetry_capture):
    topology.declare_route(**ROUTE)
    topology.declare_route(**ROUTE)
    assert len(topology.routes()) == 1
    assert len(telemetry_capture.points("maas.topology.route_info")) == 1


def test_clear_by_source(telemetry_capture):
    topology.declare_route(**ROUTE)
    topology.declare_route(**{**ROUTE, "direction": "out"}, source="observed")
    topology.clear(source="config")
    assert [r.source for r in topology.routes()] == ["observed"]
    points = telemetry_capture.points("maas.topology.route_info")
    assert [p.attributes["source"] for p in points] == ["observed"]
    topology.clear()
    assert topology.routes() == frozenset()


def test_works_when_disabled():
    assert not telemetry.enabled()
    topology.declare_route(**ROUTE)
    assert len(topology.routes()) == 1


@pytest.mark.parametrize(
    "override",
    [{"direction": "sideways"}, {"node_type": "foo"}, {"source": "guess"}],
)
def test_invalid_values_raise(override):
    with pytest.raises(ValueError):
        topology.declare_route(**{**ROUTE, **override})


def test_node_info_and_summary(telemetry_capture):
    class Consolidate:
        """Consolidate APS.

        More.
        """

    summary = topology.node_summary(Consolidate)
    assert summary == "Consolidate APS."
    topology.declare_node(
        node_type="engine", node="CONSOLIDATE_APS", impl="pkg.Consolidate",
        summary=summary,
    )
    p = telemetry_capture.point("maas.node_info", node="CONSOLIDATE_APS")
    assert p.value == 1
    assert p.attributes["summary"] == "Consolidate APS."
    assert p.attributes["impl"] == "pkg.Consolidate"
    assert p.attributes["component"] == "test"


def test_summary_truncated_and_empty_docstring():
    topology.declare_node(node_type="collector", node="n", summary="x" * 500)
    assert len(topology._NODES[("collector", "n")][1]) == 200

    class Bare:
        pass

    assert topology.node_summary(Bare) == ""


def test_build_info():
    cap = capture(project="maas-engine", project_version="3.2.0")
    try:
        import platform

        from maas_model import __version__

        p = cap.point("maas.build_info")
        assert p.value == 1
        assert p.attributes["project"] == "maas-engine"
        assert p.attributes["project_version"] == "3.2.0"
        assert p.attributes["version"] == __version__
        assert p.attributes["python"] == platform.python_version()
        assert p.attributes["component"] == "test"
    finally:
        cap.close()


def test_reregistered_after_reset():
    first = capture()
    first.close()
    second = capture()
    try:
        topology.declare_route(**ROUTE)
        assert len(second.points("maas.topology.route_info")) == 1
    finally:
        second.close()


def test_reset_for_tests_forgets_routes_and_nodes():
    topology.declare_route(**ROUTE)
    topology.declare_node(node_type="engine", node="CONSOLIDATE_APS")
    topology._reset_for_tests()
    assert topology.routes() == frozenset()
    assert topology._NODES == {}


def test_capture_starts_and_ends_with_an_empty_registry():
    # declared by an earlier test, or at import time
    topology.declare_route(**ROUTE)
    topology.declare_node(node_type="engine", node="CONSOLIDATE_APS")
    cap = capture()
    try:
        assert cap.points("maas.topology.route_info") == []
        assert cap.points("maas.node_info") == []
        topology.declare_route(**ROUTE)
        topology.declare_node(node_type="engine", node="CONSOLIDATE_APS")
    finally:
        cap.close()
    assert topology.routes() == frozenset()
    assert topology._NODES == {}


def test_string_fields_coerced(telemetry_capture):
    topology.declare_route(**{**ROUTE, "exchange": None, "routing_key": 123})
    topology.declare_node(node_type="engine", node=7, impl=None, summary=None)
    (route,) = topology.routes()
    assert (route.exchange, route.routing_key) == ("", "123")
    p = telemetry_capture.point("maas.topology.route_info", node="CONSOLIDATE_APS")
    assert p.attributes["exchange"] == ""
    assert p.attributes["routing_key"] == "123"
    n = telemetry_capture.point("maas.node_info", node="7")
    assert (n.attributes["impl"], n.attributes["summary"]) == ("", "")


def _observed(i):
    return dict(ROUTE, routing_key=f"rk.{i}", source="observed")


def test_clear_observed_rearms_cap(monkeypatch):
    monkeypatch.setattr(semconv, "MAX_OBSERVED_ROUTES", 2)
    for i in range(4):
        topology.declare_route(**_observed(i))
    assert len(topology.routes()) == 2
    topology.clear("observed")
    for i in range(10, 14):
        topology.declare_route(**_observed(i))
    assert len(topology.routes()) == 2
    topology.clear()
    topology.declare_route(**_observed(20))
    topology.declare_route(**_observed(21))
    assert len(topology.routes()) == 2


def test_clear_config_keeps_observed_count(monkeypatch):
    monkeypatch.setattr(semconv, "MAX_OBSERVED_ROUTES", 2)
    topology.declare_route(**dict(ROUTE, routing_key="cfg"))
    for i in range(3):
        topology.declare_route(**_observed(i))
    topology.clear("config")
    topology.declare_route(**_observed(9))
    observed = [r for r in topology.routes() if r.source == "observed"]
    assert len(observed) == 2


def test_observed_routes_capped(caplog, monkeypatch):
    monkeypatch.setattr(semconv, "MAX_OBSERVED_ROUTES", 3)
    with caplog.at_level("WARNING", logger="Telemetry"):
        for i in range(5):
            topology.declare_route(**_observed(i))
        topology.declare_route(**_observed(0))  # known: no-op, no warning
        for i in range(4):
            topology.declare_route(**dict(ROUTE, routing_key=f"cfg.{i}"))
    observed = [r for r in topology.routes() if r.source == "observed"]
    config = [r for r in topology.routes() if r.source == "config"]
    assert len(observed) == 3
    assert len(config) == 4
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
