"""
Trace context propagation and message spans for AMQP.

Nothing here imports opentelemetry at module level: it is imported lazily, and
only once telemetry is enabled. Nothing here raises into the caller because of
telemetry, whatever the message, headers or body look like.
"""

import contextlib
import logging
import time
from typing import Any, Iterable, Mapping, Optional

from maas_model.telemetry import metrics, semconv
from maas_model.telemetry._noop import NOOP_SPAN
from maas_model.telemetry._state import STATE
from maas_model.telemetry.tracing import span

LOGGER = logging.getLogger("Telemetry")

_TRACE_KEYS = frozenset({"traceparent", "tracestate", "baggage"})


def inject(headers: Optional[dict] = None) -> dict:
    """
    add the trace context (traceparent, tracestate, baggage) to headers

    Returns headers itself, or a new dict when headers is None. Disabled, it
    returns headers unchanged, or {}.
    """
    if headers is None:
        headers = {}
    if not STATE.enabled:
        return headers
    try:
        from opentelemetry import propagate

        propagate.inject(headers)
    except Exception:  # pylint: disable=broad-except
        LOGGER.debug("trace context injection failed", exc_info=True)
    return headers


def _text_headers(headers: Mapping) -> dict:
    clean = {}
    for key, value in headers.items():
        if isinstance(key, bytes):
            key = key.decode("utf-8", "replace")
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(key, str) and isinstance(value, str):
            clean[key] = value
    return clean


def extract(headers: Optional[Mapping]):
    """
    the opentelemetry Context carried by headers, None if there is none or
    telemetry is disabled
    """
    if not STATE.enabled or not headers:
        return None
    try:
        from opentelemetry import propagate

        clean = _text_headers(headers)
        if "traceparent" not in clean:
            return None
        return propagate.extract(clean)
    except Exception:  # pylint: disable=broad-except
        LOGGER.debug("trace context extraction failed", exc_info=True)
        return None


@contextlib.contextmanager
def publish_span(
    exchange: str,
    routing_key: str,
    *,
    message_id: str = "",
    document_count: Optional[int] = None,
    document_class: str = "",
    body_size: Optional[int] = None,
    links: Iterable[Any] = (),
    attributes: Optional[Mapping] = None,
    headers: Optional[Mapping] = None,
    new_trace: bool = False,
):
    """
    PRODUCER span around a publish, yielding the headers to publish with

        with publish_span(exchange, routing_key) as headers:
            producer.publish(body, headers=headers)

    headers are merged into the yielded dict, the trace keys winning, and are
    never mutated. Disabled, it yields a copy of headers, or {}. Exceptions
    propagate.

    new_trace starts the span as a new root, whatever span is current, for
    messages aggregating several origins: they are kept as links. It starts a
    new trace but keeps the current baggage, which the headers carry: wrap the
    call in baggage() to set what an aggregated message should carry.
    Disabled, it has no effect.
    """
    if not STATE.enabled:
        yield dict(headers or {})
        return

    parent = None
    if new_trace:
        try:
            from opentelemetry import context as otel_context

            parent = otel_context.Context()
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("new trace context failed", exc_info=True)

    attrs = {
        "messaging.system": "rabbitmq",
        "messaging.operation.type": "publish",
        "messaging.destination.name": exchange,
        "messaging.rabbitmq.destination.routing_key": routing_key,
    }
    if message_id:
        attrs["messaging.message.id"] = message_id
        attrs[semconv.ATTR_MESSAGE_ID] = message_id
    if document_count is not None:
        attrs[semconv.ATTR_DOCUMENT_COUNT] = document_count
    if document_class:
        attrs[semconv.ATTR_DOCUMENT_CLASS] = document_class
    if body_size is not None:
        attrs["messaging.message.body.size"] = body_size
    if attributes:
        attrs.update(attributes)

    outcome = "ok"
    with span(
        f"publish {exchange}", kind="producer", attributes=attrs, links=links, parent=parent
    ):
        try:
            carried = {
                key: value
                for key, value in (headers or {}).items()
                if key not in _TRACE_KEYS
            }
            yield inject(carried)
        except BaseException:
            outcome = "error"
            raise
        finally:
            labels = {"exchange": exchange, "routing_key": routing_key}
            metrics.MESSAGES_PUBLISHED.add(1, dict(labels, outcome=outcome))
            if document_count is not None:
                metrics.MESSAGE_DOCUMENTS.record(
                    document_count, dict(labels, direction="out")
                )


def returned(exchange: str, routing_key: str, reason: str) -> None:
    """count a message returned by the broker as unroutable"""
    metrics.MESSAGES_RETURNED.add(
        1, {"exchange": exchange, "routing_key": routing_key, "reason": reason}
    )


class ConsumeScope:
    """what consume_span yields: set the outcome of the message"""

    def __init__(self, span_=NOOP_SPAN):
        self.span = span_
        self._outcome = "unknown"
        self._reason = "normal"

    def outcome(self, outcome: str, reason: str = "normal") -> None:
        """ack, requeue or reject, with a short reason"""
        self._outcome = outcome
        self._reason = reason


class _NoopScope(ConsumeScope):
    def outcome(self, outcome: str, reason: str = "normal") -> None:
        pass


_NOOP_SCOPE = _NoopScope()


def _delivery(message) -> tuple:
    info = getattr(message, "delivery_info", None)
    if not isinstance(info, Mapping):
        info = {}
    return (
        info.get("exchange") or "",
        info.get("routing_key") or "",
        "true" if info.get("redelivered") else "false",
    )


def _join(value) -> Optional[str]:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return None


def _origin_interface(parent) -> dict:
    """the maas.origin.interface baggage of a context, as span attributes"""
    try:
        from opentelemetry import baggage

        value = baggage.get_baggage(semconv.ATTR_ORIGIN_INTERFACE, parent)
        if value:
            return {semconv.ATTR_ORIGIN_INTERFACE: str(value)}
    except Exception:  # pylint: disable=broad-except
        LOGGER.debug("baggage read failed", exc_info=True)
    return {}


def _body_attributes(body) -> dict:
    attrs = {}
    if not isinstance(body, Mapping):
        return attrs
    if body.get("message_id"):
        attrs[semconv.ATTR_MESSAGE_ID] = str(body["message_id"])
        attrs["messaging.message.id"] = str(body["message_id"])
    document_ids = body.get("document_ids")
    if isinstance(document_ids, (list, tuple)):
        attrs[semconv.ATTR_DOCUMENT_COUNT] = len(document_ids)
    if body.get("document_class"):
        attrs[semconv.ATTR_DOCUMENT_CLASS] = str(body["document_class"])
    pipeline = _join(body.get("pipeline"))
    if pipeline is not None:
        attrs[semconv.ATTR_PIPELINE] = pipeline
    ancestors = _join(body.get("ancestor_ids"))
    if ancestors is not None:
        attrs[semconv.ATTR_ANCESTOR_IDS] = ancestors
    return attrs


@contextlib.contextmanager
def _attached(context):
    """
    make an extracted context current for the span, so that its baggage is
    carried on by what the consumer publishes; nothing to do for None
    """
    token = None
    if context is not None:
        try:
            from opentelemetry import context as otel_context

            token = otel_context.attach(context)
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("context attach failed", exc_info=True)
    try:
        yield
    finally:
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("context detach failed", exc_info=True)


@contextlib.contextmanager
def consume_span(message, *, queue: str, body: Any = None):
    """
    CONSUMER span around the processing of a received message

    message is duck typed, with headers and delivery_info (either may be
    None). The span continues the trace in the headers, or is a root. The
    scope yielded records the outcome, exceptions propagate.
    """
    if not STATE.enabled:
        yield _NOOP_SCOPE
        return

    try:
        exchange, routing_key, redelivered = _delivery(message)
        from opentelemetry.context import Context

        parent = extract(getattr(message, "headers", None))
        incoming = parent
        if parent is None:
            parent = Context()
        attrs = {
            "messaging.system": "rabbitmq",
            "messaging.operation.type": "process",
            "messaging.destination.name": exchange,
            "messaging.rabbitmq.destination.routing_key": routing_key,
            "messaging.destination.subscription.name": queue,
            semconv.ATTR_REDELIVERED: redelivered == "true",
        }
        attrs.update(_origin_interface(parent))
        attrs.update(_body_attributes(body))
        documents = body.get("document_ids") if isinstance(body, Mapping) else None
    except Exception:  # pylint: disable=broad-except
        LOGGER.debug("consume span setup failed", exc_info=True)
        yield _NOOP_SCOPE
        return

    start = time.perf_counter()
    with _attached(incoming), span(
        f"process {queue}", kind="consumer", attributes=attrs, parent=parent
    ) as consumed:
        scope = ConsumeScope(consumed)
        try:
            yield scope
        except BaseException:
            if scope._outcome == "unknown":
                scope.outcome("error", "exception")
            raise
        finally:
            try:
                consumed.set_attribute(semconv.ATTR_OUTCOME, scope._outcome)
                consumed.set_attribute(semconv.ATTR_REASON, scope._reason)
            except Exception:  # pylint: disable=broad-except
                pass
            metrics.MESSAGES_CONSUMED.add(
                1,
                {
                    "exchange": exchange,
                    "routing_key": routing_key,
                    "queue": queue,
                    "outcome": scope._outcome,
                    "reason": scope._reason,
                    "redelivered": redelivered,
                },
            )
            metrics.MESSAGE_PROCESSING_DURATION.record(
                time.perf_counter() - start,
                {"queue": queue, "outcome": scope._outcome},
            )
            if isinstance(documents, (list, tuple)):
                metrics.MESSAGE_DOCUMENTS.record(
                    len(documents),
                    {
                        "direction": "in",
                        "exchange": exchange,
                        "routing_key": routing_key,
                    },
                )
