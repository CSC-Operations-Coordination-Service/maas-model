import ast
import pathlib

import pytest

from maas_model import telemetry
from maas_model.telemetry import _instruments, semconv

PACKAGE = pathlib.Path(telemetry.__file__).parent

# every counter and histogram of the spec's Metric spec table, and
# maas.latency.negative: (kind, unit, buckets)
EXPECTED = {
    "MESSAGES_PUBLISHED": ("counter", "{message}", None),
    "MESSAGES_RETURNED": ("counter", "{message}", None),
    "MESSAGES_CONSUMED": ("counter", "{message}", None),
    "MESSAGE_DOCUMENTS": ("histogram", "{document}", semconv.DOCUMENT_BUCKETS),
    "MESSAGE_PROCESSING_DURATION": ("histogram", "s", semconv.DURATION_BUCKETS),
    "ENGINE_RUN_DURATION": ("histogram", "s", semconv.DURATION_BUCKETS),
    "ENGINE_DOCUMENTS": ("counter", "{document}", None),
    "ENGINE_INPUT_DOCUMENTS": ("counter", "{document}", None),
    "ENGINE_INPUT_MISSING": ("counter", "{document}", None),
    "ENGINE_REPORTS_DOCUMENTS": ("counter", "{document}", None),
    "COLLECTOR_FILES": ("counter", "{file}", None),
    "COLLECTOR_FILE_DURATION": ("histogram", "s", semconv.DURATION_BUCKETS),
    "COLLECTOR_DOCUMENTS": ("counter", "{document}", None),
    "COLLECTOR_DOCUMENTS_ROUTED": ("counter", "{document}", None),
    "COLLECTOR_LOOP_DURATION": ("histogram", "s", semconv.DURATION_BUCKETS),
    "OPENSEARCH_REQUEST_DURATION": ("histogram", "s", semconv.OPENSEARCH_BUCKETS),
    "OPENSEARCH_TIME": ("counter", "s", None),
    "OPENSEARCH_REQUESTS": ("counter", "{request}", None),
    "LATENCY_NEGATIVE": ("counter", "", None),
}


def _exported(capture, name):
    data = capture.metric_reader.get_metrics_data()
    found = [
        metric
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == name
    ]
    assert len(found) == 1, f"{len(found)} metrics named {name!r}"
    return found[0]


def test_metrics_module_is_public():
    assert telemetry.metrics is not None
    assert "metrics" in telemetry.__all__


def test_every_metric_is_defined_once():
    proxies = {
        attr: value
        for attr, value in vars(telemetry.metrics).items()
        if isinstance(value, _instruments._Instrument)
    }
    assert set(proxies) == set(EXPECTED)
    for attr, proxy in proxies.items():
        assert proxy.name == getattr(semconv, attr)
        assert proxy.description


@pytest.mark.parametrize("attr", sorted(EXPECTED))
def test_unit_and_buckets(attr, telemetry_capture):
    kind, unit, buckets = EXPECTED[attr]
    proxy = getattr(telemetry.metrics, attr)
    if kind == "counter":
        assert isinstance(proxy, _instruments.Counter)
        proxy.add(1, {"node": "n"})
    else:
        assert isinstance(proxy, _instruments.Histogram)
        proxy.record(1, {"node": "n"})
    metric = _exported(telemetry_capture, proxy.name)
    assert metric.unit == unit
    if buckets is not None:
        assert tuple(metric.data.data_points[0].explicit_bounds) == tuple(buckets)


def _defined_elsewhere():
    """(file, line, name) of each _counter/_histogram call outside metrics.py"""
    names = {getattr(semconv, attr) for attr in EXPECTED}
    found = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in ("metrics.py", "_instruments.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                called = func.id
            else:
                called = getattr(func, "attr", "")
            if called not in ("_counter", "_histogram") or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name):
                name = getattr(semconv, first.attr, None)
            elif isinstance(first, ast.Constant):
                name = first.value
            else:
                name = None
            if name in names:
                found.append((path.name, node.lineno, name))
    return found


def test_no_other_module_defines_a_spec_metric():
    assert _defined_elsewhere() == []
