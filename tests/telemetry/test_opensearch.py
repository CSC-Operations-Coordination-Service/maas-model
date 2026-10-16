from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("opensearchpy")

from opensearchpy import OpenSearch  # noqa: E402
from opensearchpy.connection import Urllib3HttpConnection as Connection  # noqa: E402
from opensearchpy.exceptions import ConnectionError as OSConnectionError  # noqa: E402
from opensearchpy.exceptions import NotFoundError  # noqa: E402
from opensearchpy.transport import Transport  # noqa: E402

from maas_model import telemetry  # noqa: E402
from maas_model.telemetry import opensearch  # noqa: E402

BULK_BODY = '{"index":{"_index":"raw-data-aps-2026"}}\n{"a":1}\n'

PARSE_TABLE = [
    ("POST", "/_bulk", BULK_BODY, "bulk", "raw-data-aps"),
    ("POST", "/raw-data-aps-2026-10/_bulk", None, "bulk", "raw-data-aps"),
    ("POST", "/cds-product/_mget", None, "mget", "cds-product"),
    ("POST", "/cds-product,cds-publication/_search", None, "search", "_multi"),
    ("POST", "/_search/scroll", None, "scroll", "_none"),
    ("GET", "/cds-product/_count", None, "count", "cds-product"),
    ("POST", "/cds-*/_delete_by_query", None, "delete_by_query", "cds-*"),
    ("GET", "/cds-product/_doc/abc", None, "doc_get", "cds-product"),
    ("PUT", "/cds-product/_doc/abc", None, "doc_index", "cds-product"),
    ("DELETE", "/cds-product/_doc/abc", None, "doc_delete", "cds-product"),
    ("POST", "/cds-product/_update/abc", None, "doc_update", "cds-product"),
    ("GET", "/", None, "other", "_none"),
    ("POST", "/maas-journal-2026.10.16/_search", None, "search", "maas-journal"),
]


@pytest.mark.parametrize("method,url,body,operation,index", PARSE_TABLE)
def test_parse(method, url, body, operation, index):
    assert opensearch.parse_operation(method, url) == operation
    assert opensearch.parse_index(url, body) == index


def test_parse_bulk_bytes_and_list_bodies():
    assert opensearch.parse_index("/_bulk", BULK_BODY.encode()) == "raw-data-aps"
    assert opensearch.parse_index("/_bulk", [{"index": {"_index": "x-2026"}}]) == "x"


def test_parse_never_consumes_a_generator():
    gen = (line for line in [{"index": {"_index": "x"}}])
    assert opensearch.parse_index("/_bulk", gen) == "_none"
    assert len(list(gen)) == 1


def test_parse_never_raises():
    assert opensearch.parse_index(None, object()) == "_none"
    assert opensearch.parse_operation(None, None) == "other"


@pytest.fixture
def server(monkeypatch):
    calls = []

    def fake(self, method, url, params=None, body=None, **kwargs):
        calls.append((method, url))
        return 200, {}, "{}"

    monkeypatch.setattr(Connection, "perform_request", fake)
    yield calls
    opensearch.uninstrument_opensearch()


def test_perform_request_records(telemetry_capture, server):
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    with telemetry.span("engine.run"), telemetry.activity("engine", "COMPUTE_X"):
        client.transport.perform_request("POST", "/cds-product/_search", body={})
    cap = telemetry_capture
    # first: reading a point collects, and collecting drains the exemplars
    assert cap.point("maas.opensearch.request.duration", operation="search").exemplars
    assert (
        cap.point(
            "maas.opensearch.request.duration",
            operation="search",
            index="cds-product",
            status_class="2xx",
        ).count
        == 1
    )
    assert cap.point("maas.opensearch.time", node="COMPUTE_X").value >= 0
    assert cap.point("maas.opensearch.requests", node_type="engine").value == 1
    span = cap.span("opensearch search")
    assert span.parent is not None
    assert span.attributes["db.system"] == "opensearch"
    assert span.attributes["db.operation.name"] == "search"
    assert span.attributes["db.collection.name"] == "cds-product"
    assert span.attributes["http.response.status_code"] == 200


def test_passthrough_result_and_args(telemetry_capture, server):
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    assert opensearch.instrument_opensearch() is True
    assert client.transport.perform_request("GET", "/cds-product/_count") == {}
    assert server == [("GET", "/cds-product/_count")]


def test_threadpool_call_parents_to_activity(telemetry_capture, server):
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    with telemetry.span("engine.run"):
        with telemetry.activity("engine", "COMPUTE_X"):
            with ThreadPoolExecutor(2) as pool:
                pool.submit(
                    client.transport.perform_request, "POST", "/cds-a/_bulk", body=[]
                ).result()
    run = telemetry_capture.span("engine.run")
    child = telemetry_capture.span("opensearch bulk")
    assert child.parent.span_id == run.context.span_id
    assert telemetry_capture.point("maas.opensearch.time", node="COMPUTE_X")


def test_no_parent_no_span_but_metrics(telemetry_capture, server):
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    client.transport.perform_request("POST", "/cds-product/_search", body={})
    assert not [s for s in telemetry_capture.spans() if s.name.startswith("opensearch")]
    assert telemetry_capture.point(
        "maas.opensearch.time", node="none", node_type="none"
    )


def test_404_status_class_and_reraise(telemetry_capture, monkeypatch):
    def fake(self, *args, **kwargs):
        raise NotFoundError(404, "nope", {})

    monkeypatch.setattr(Connection, "perform_request", fake)
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    try:
        with pytest.raises(NotFoundError):
            client.transport.perform_request("GET", "/cds-product/_doc/x")
        assert (
            telemetry_capture.point(
                "maas.opensearch.request.duration", status_class="4xx"
            ).count
            == 1
        )
    finally:
        opensearch.uninstrument_opensearch()


def test_connection_error_status_class_error(telemetry_capture, monkeypatch):
    def fake(self, *args, **kwargs):
        raise OSConnectionError("N/A", "down", Exception("x"))

    monkeypatch.setattr(Connection, "perform_request", fake)
    client = OpenSearch(hosts=["http://127.0.0.1:1"], max_retries=0)
    try:
        with pytest.raises(OSConnectionError):
            client.transport.perform_request("GET", "/cds-product/_count")
        assert telemetry_capture.point(
            "maas.opensearch.request.duration", status_class="error"
        )
    finally:
        opensearch.uninstrument_opensearch()


def test_instrument_twice_counts_once(telemetry_capture, server):
    assert opensearch.instrument_opensearch() is True
    assert opensearch.instrument_opensearch() is True
    telemetry.configure("test", enabled=True)  # already enabled, no-op
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    client.transport.perform_request("POST", "/cds-product/_search", body={})
    assert (
        telemetry_capture.point(
            "maas.opensearch.request.duration", operation="search"
        ).count
        == 1
    )


def test_configure_instruments_opensearch(telemetry_capture):
    # the capture fixture configures with instrument_opensearch=True
    try:
        assert getattr(Transport.perform_request, "_maas_telemetry_wrapped", False)
    finally:
        opensearch.uninstrument_opensearch()
    assert not getattr(Transport.perform_request, "_maas_telemetry_wrapped", False)


def test_disabled_calls_straight_through(server):
    opensearch.instrument_opensearch()
    client = OpenSearch(hosts=["http://127.0.0.1:1"])
    assert client.transport.perform_request("GET", "/cds-product/_count") == {}
