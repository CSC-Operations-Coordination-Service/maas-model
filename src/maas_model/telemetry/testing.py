"""
Capture the telemetry of a test in memory.

Test only: unlike the rest of maas_model.telemetry it imports opentelemetry
and pytest at import time.

    def test_something(telemetry_capture):
        ...
        assert telemetry_capture.point("maas.messages.published").value == 1
"""

import dataclasses
from typing import Any, Dict, List, Optional

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from maas_model.telemetry import _setup, _state, topology
from maas_model.telemetry.activity import (  # the package rebinds activity
    _reset_for_tests as _reset_activities,
)


@dataclasses.dataclass
class Point:
    """one data point of a metric"""

    attributes: Dict[str, Any]

    # sum or gauge value, None for a histogram
    value: Optional[float] = None

    # histogram count and sum, None for a sum or gauge
    count: Optional[int] = None

    sum: Optional[float] = None

    exemplars: list = dataclasses.field(default_factory=list)


class Capture:
    """the spans and metric points recorded since capture() was called"""

    def __init__(self, span_exporter, metric_reader):
        self.span_exporter = span_exporter
        self.metric_reader = metric_reader

    def spans(self) -> List[ReadableSpan]:
        """the finished spans"""
        return list(self.span_exporter.get_finished_spans())

    def span(self, name: str) -> ReadableSpan:
        """the one finished span with this name"""
        found = [span for span in self.spans() if span.name == name]
        assert len(found) == 1, f"{len(found)} spans named {name!r}, expected 1"
        return found[0]

    def points(self, metric_name: str) -> List[Point]:
        """every data point of a metric, one per attribute set"""
        data = self.metric_reader.get_metrics_data()
        points = []
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name != metric_name:
                        continue
                    for point in metric.data.data_points:
                        points.append(self._point(point))
        return points

    def point(self, metric_name: str, **attrs) -> Point:
        """the one data point of a metric whose attributes include attrs"""
        found = [
            point
            for point in self.points(metric_name)
            if all(point.attributes.get(k) == v for k, v in attrs.items())
        ]
        assert (
            len(found) == 1
        ), f"{len(found)} points of {metric_name!r} match {attrs}, expected 1"
        return found[0]

    def close(self) -> None:
        """
        disable telemetry, shut the providers down, and forget the declared
        topology and the activities
        """
        providers = [_state.STATE.tracer_provider, _state.STATE.meter_provider]
        _state.reset()
        _forget_process_state()
        for provider in providers:
            if provider is not None:
                provider.shutdown()

    @staticmethod
    def _point(point) -> Point:
        exemplars = list(getattr(point, "exemplars", None) or [])
        if hasattr(point, "bucket_counts"):
            return Point(
                dict(point.attributes or {}),
                count=point.count,
                sum=point.sum,
                exemplars=exemplars,
            )
        return Point(
            dict(point.attributes or {}), value=point.value, exemplars=exemplars
        )


def _forget_process_state() -> None:
    """the topology registry and the activities outlive a test, clear them"""
    topology._reset_for_tests()  # pylint: disable=protected-access
    _reset_activities()


def capture(component: str = "test", **configure_kwargs) -> Capture:
    """
    reset telemetry and enable it on in-memory exporters

    The declared topology and the activities are forgotten, here and by
    Capture.close(), so that no test sees what another one left behind.
    """
    _state.reset()
    _forget_process_state()
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    _setup.configure(
        component,
        enabled=True,
        span_exporter=span_exporter,
        metric_reader=metric_reader,
        **configure_kwargs,
    )
    return Capture(span_exporter, metric_reader)


@pytest.fixture
def telemetry_capture():
    """telemetry enabled on in-memory exporters for the length of a test"""
    cap = capture()
    yield cap
    cap.close()
