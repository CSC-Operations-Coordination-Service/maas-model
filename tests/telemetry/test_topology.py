import pytest

from maas_model import telemetry
from maas_model.telemetry import topology
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
    topology.clear()
    topology._NODES.clear()
    yield
    topology.clear()
    topology._NODES.clear()


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
    topology.declare_route(**ROUTE)
    first.close()
    second = capture()
    try:
        assert len(second.points("maas.topology.route_info")) == 1
    finally:
        second.close()
