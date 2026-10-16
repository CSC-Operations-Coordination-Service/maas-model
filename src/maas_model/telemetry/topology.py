"""
Declared MAAS topology, exported as info gauges.

Services declare their routes (exchange, routing key, queue) and nodes, and
the gauges maas.topology.route_info, maas.node_info and maas.build_info carry
them with a value of 1. The registry works when telemetry is disabled and
imports nothing from opentelemetry at module level.
"""

import dataclasses
import logging
import platform
import threading
from typing import Dict, FrozenSet, Optional, Tuple

from maas_model.telemetry import _state, semconv

LOGGER = logging.getLogger("Telemetry")

DIRECTIONS = frozenset({"in", "out"})
NODE_TYPES = frozenset({"collector", "engine"})
SOURCES = frozenset({"config", "observed"})

SUMMARY_MAX_LENGTH = 200


@dataclasses.dataclass(frozen=True)
class Route:
    """one declared route, every field is a metric label"""

    direction: str
    node_type: str
    node: str
    exchange: str
    routing_key: str
    queue: str = ""
    document_class: str = ""
    source: str = "config"


_LOCK = threading.Lock()
_ROUTES = set()
# (node_type, node) -> (impl, summary)
_NODES: Dict[Tuple[str, str], Tuple[str, str]] = {}


def declare_route(
    *,
    direction: str,
    node_type: str,
    node: str,
    exchange: str,
    routing_key: str,
    queue: str = "",
    document_class: str = "",
    source: str = "config",
) -> None:
    """declare a route, a repeated declaration is a no-op"""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be in {sorted(DIRECTIONS)}: {direction!r}")
    if node_type not in NODE_TYPES:
        raise ValueError(f"node_type must be in {sorted(NODE_TYPES)}: {node_type!r}")
    if source not in SOURCES:
        raise ValueError(f"source must be in {sorted(SOURCES)}: {source!r}")
    route = Route(
        direction,
        node_type,
        node,
        exchange,
        routing_key,
        queue,
        document_class,
        source,
    )
    with _LOCK:
        _ROUTES.add(route)


def declare_node(
    *, node_type: str, node: str, impl: str = "", summary: str = ""
) -> None:
    """declare a node, the summary is truncated to 200 characters"""
    if node_type not in NODE_TYPES:
        raise ValueError(f"node_type must be in {sorted(NODE_TYPES)}: {node_type!r}")
    with _LOCK:
        _NODES[(node_type, node)] = (impl, (summary or "")[:SUMMARY_MAX_LENGTH])


def clear(source: Optional[str] = None) -> None:
    """forget the routes, of one source or of all; nodes are kept"""
    with _LOCK:
        if source is None:
            _ROUTES.clear()
        else:
            for route in [r for r in _ROUTES if r.source == source]:
                _ROUTES.discard(route)


def _reset_for_tests() -> None:
    """forget every declared route and node"""
    with _LOCK:
        _ROUTES.clear()
        _NODES.clear()


def routes() -> FrozenSet[Route]:
    """the declared routes"""
    with _LOCK:
        return frozenset(_ROUTES)


def node_summary(obj) -> str:
    """the first non empty line of the docstring of obj, or an empty string"""
    try:
        doc = getattr(obj, "__doc__", None) or ""
        for line in doc.splitlines():
            if line.strip():
                return line.strip()
    except Exception:  # pylint: disable=broad-except
        pass
    return ""


def _base() -> dict:
    return dict(_state.STATE.base_attributes)


def _register(meter, project: str, project_version: str) -> None:
    """create the three observable gauges on a meter"""
    from opentelemetry.metrics import Observation

    def route_callback(options):  # pylint: disable=unused-argument
        try:
            snapshot = routes()
            base = _base()
            return [
                Observation(1, {**dataclasses.asdict(route), **base})
                for route in snapshot
            ]
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("route_info callback failed", exc_info=True)
            return []

    def node_callback(options):  # pylint: disable=unused-argument
        try:
            with _LOCK:
                snapshot = dict(_NODES)
            base = _base()
            return [
                Observation(
                    1,
                    {
                        "node_type": node_type,
                        "node": node,
                        "impl": impl,
                        "summary": summary,
                        **base,
                    },
                )
                for (node_type, node), (impl, summary) in snapshot.items()
            ]
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("node_info callback failed", exc_info=True)
            return []

    def build_callback(options):  # pylint: disable=unused-argument
        try:
            from maas_model import __version__

            return [
                Observation(
                    1,
                    {
                        "version": __version__,
                        "project": project,
                        "project_version": project_version,
                        "python": platform.python_version(),
                        **_base(),
                    },
                )
            ]
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("build_info callback failed", exc_info=True)
            return []

    meter.create_observable_gauge(
        semconv.TOPOLOGY_ROUTE_INFO, callbacks=[route_callback], unit=""
    )
    meter.create_observable_gauge(
        semconv.NODE_INFO, callbacks=[node_callback], unit=""
    )
    meter.create_observable_gauge(
        semconv.BUILD_INFO, callbacks=[build_callback], unit=""
    )
