"""
Timing of the OpenSearch requests, per operation, index and node.

Wraps opensearchpy.transport.Transport.perform_request. Nothing here imports
opentelemetry at module level: it is imported lazily, and only once telemetry
is enabled. Nothing here raises into the caller because of telemetry.
"""

import functools
import json
import logging
import re
import time
from typing import Any

from maas_model.telemetry import semconv
from maas_model.telemetry._instruments import _counter, _histogram
from maas_model.telemetry._noop import NOOP_SPAN
from maas_model.telemetry._state import STATE
from maas_model.telemetry.activity import current_activity
from maas_model.telemetry.tracing import current_span_context, span

LOGGER = logging.getLogger("Telemetry")

_MARK = "_maas_telemetry_wrapped"

_DURATION = _histogram(
    semconv.OPENSEARCH_REQUEST_DURATION,
    unit="s",
    description="OpenSearch request duration",
    boundaries=semconv.OPENSEARCH_BUCKETS,
)
_TIME = _counter(
    semconv.OPENSEARCH_TIME,
    unit="s",
    description="time spent in OpenSearch requests",
)
_REQUESTS = _counter(
    semconv.OPENSEARCH_REQUESTS,
    unit="{request}",
    description="OpenSearch requests",
)

_DATE_SUFFIX = re.compile(r"-\d{4}([.-]\d{2}){0,2}$")

_BULK_INDEX = re.compile(r'"_index"\s*:\s*"([^"]+)"')

_BODY_WINDOW = 2048

_DOC_OPERATIONS = {
    "GET": "doc_get",
    "HEAD": "doc_get",
    "PUT": "doc_index",
    "POST": "doc_index",
    "DELETE": "doc_delete",
}


def _segments(url: Any) -> list:
    path = str(url or "").split("?", 1)[0]
    return [part for part in path.split("/") if part]


def parse_operation(method: str, url: str) -> str:
    """the operation of a request: bulk, search, doc_get, ... or other"""
    try:
        segments = _segments(url)
        api = [part for part in segments if part.startswith("_")]
        if not api:
            return "other"
        name = api[0]
        if name == "_search" and segments[-1] == "scroll":
            return "scroll"
        if name == "_doc":
            return _DOC_OPERATIONS.get(str(method).upper(), "other")
        if name == "_create":
            return "doc_index"
        if name == "_update":
            return "doc_update"
        return name[1:] or "other"
    except Exception:  # pylint: disable=broad-except
        return "other"


def _normalise(index: str) -> str:
    if "," in index:
        return "_multi"
    return _DATE_SUFFIX.sub("", index, count=1) or "_none"


def _body_text(body: Any) -> str:
    """the start of a str, bytes or list body, never touching an iterator"""
    if isinstance(body, str):
        return body[:_BODY_WINDOW]
    if isinstance(body, (bytes, bytearray)):
        return bytes(body[:_BODY_WINDOW]).decode("utf-8", "ignore")
    if isinstance(body, (list, tuple)) and body:
        first = body[0]
        if isinstance(first, (str, bytes)):
            return _body_text(first)
        return json.dumps(first, default=str)[:_BODY_WINDOW]
    return ""


def parse_index(url: str, body: Any = None) -> str:
    """the index of a request, without its date suffix, _multi or _none"""
    try:
        segments = _segments(url)
        if segments and not segments[0].startswith("_"):
            return _normalise(segments[0])
        if segments and segments[0] == "_bulk":
            found = _BULK_INDEX.search(_body_text(body))
            if found:
                return _normalise(found.group(1))
        return "_none"
    except Exception:  # pylint: disable=broad-except
        return "_none"


def _status_class(error: Any) -> str:
    if error is None:
        return "2xx"
    code = getattr(error, "status_code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        return f"{code // 100}xx"
    return "error"


def _parent_context():
    """
    the context to parent to, None when there is no valid parent

    The current span first, else the span of the activity, which also reaches
    the pool threads a contextvar does not.
    """
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    if current_span_context() is not None:
        return otel_context.get_current()
    found = current_activity()
    ctx = found.span_context if found is not None else None
    if ctx is not None and ctx.is_valid:
        return trace.set_span_in_context(trace.NonRecordingSpan(ctx))
    return None


def _record(operation, index, started, error, parent):
    elapsed = time.perf_counter() - started
    found = current_activity()
    node_type = found.node_type if found is not None else "none"
    node = found.node if found is not None else "none"
    shared = {
        "node_type": node_type,
        "node": node,
        "operation": operation,
        "index": index,
    }
    _DURATION.record(
        elapsed,
        {
            "operation": operation,
            "index": index,
            "status_class": _status_class(error),
        },
        context=parent,
    )
    _TIME.add(elapsed, shared, context=parent)
    _REQUESTS.add(1, shared, context=parent)


def _wrap(original):
    @functools.wraps(original)
    def perform_request(self, method, url, *args, **kwargs):
        if not STATE.enabled:
            return original(self, method, url, *args, **kwargs)

        body = kwargs.get("body", args[1] if len(args) > 1 else None)
        started = time.perf_counter()
        parent = None
        operation = parse_operation(method, url)
        index = parse_index(url, body)
        scope = NOOP_SPAN
        try:
            parent = _parent_context()
            if parent is not None:
                scope = span(
                    f"opensearch {operation}",
                    kind="client",
                    attributes={
                        "db.system": "opensearch",
                        "db.operation.name": operation,
                        "db.collection.name": index,
                    },
                    parent=parent,
                )
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("opensearch telemetry setup failed", exc_info=True)
            scope = NOOP_SPAN

        error = None
        with scope as sp:
            try:
                result = original(self, method, url, *args, **kwargs)
            except BaseException as exc:
                error = exc
                _set_status(sp, exc)
                raise
            finally:
                try:
                    _record(operation, index, started, error, parent)
                except Exception:  # pylint: disable=broad-except
                    LOGGER.debug("opensearch telemetry failed", exc_info=True)
            _set_status(sp, None, 200)
            return result

    setattr(perform_request, _MARK, True)
    perform_request._maas_original = original  # pylint: disable=protected-access
    return perform_request


def _set_status(sp, error, default=None) -> None:
    try:
        code = default
        if error is not None:
            code = getattr(error, "status_code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            sp.set_attribute("http.response.status_code", code)
    except Exception:  # pylint: disable=broad-except
        pass


def instrument_opensearch() -> bool:
    """
    wrap Transport.perform_request, once

    Returns False when opensearchpy is not installed, True otherwise.
    """
    try:
        from opensearchpy.transport import Transport
    except ImportError:
        return False
    if getattr(Transport.perform_request, _MARK, False):
        return True
    Transport.perform_request = _wrap(Transport.perform_request)
    return True


def uninstrument_opensearch() -> None:
    """put the original Transport.perform_request back"""
    try:
        from opensearchpy.transport import Transport
    except ImportError:
        return
    current = Transport.perform_request
    if getattr(current, _MARK, False):
        Transport.perform_request = (
            current._maas_original
        )  # pylint: disable=protected-access
