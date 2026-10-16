"""
configure(), shutdown() and enabled().
"""

import atexit
import logging
import os
import socket
import threading
from typing import Optional

from maas_model.telemetry import _state

LOGGER = logging.getLogger("Telemetry")

SCOPE_NAME = "maas_model.telemetry"

DEFAULT_EXPORT_INTERVAL_MS = 30000

_TRUE_VALUES = ("1", "true", "yes", "on")

_atexit_registered = False


def enabled() -> bool:
    """whether telemetry is recording"""
    return _state.STATE.enabled


def _wanted(enabled_arg: Optional[bool]) -> bool:
    if enabled_arg is not None:
        return enabled_arg
    return os.environ.get("MAAS_TELEMETRY", "").strip().lower() in _TRUE_VALUES


def _export_interval_ms() -> int:
    try:
        return int(os.environ["OTEL_METRIC_EXPORT_INTERVAL"])
    except (KeyError, ValueError):
        return DEFAULT_EXPORT_INTERVAL_MS


def _maas_model_version() -> str:
    from maas_model import __version__

    return __version__


def configure(
    component: str,
    *,
    deployment: Optional[str] = None,
    instance_id: Optional[str] = None,
    collector_type: Optional[str] = None,
    project: str = "",
    project_version: str = "",
    enabled: Optional[bool] = None,
    instrument_opensearch: bool = True,
    span_exporter=None,
    metric_reader=None,
) -> bool:
    """
    Set up the tracer and meter providers, when telemetry is wanted.

    Telemetry is wanted when enabled is True, or when it is None and the
    MAAS_TELEMETRY environment variable is 1, true, yes or on. It needs the
    telemetry extra as well. Returns whether telemetry is enabled. A second
    call returns the current state without reconfiguring.

    Without span_exporter and metric_reader the data goes to the OTLP/HTTP
    endpoint of the standard OTEL_EXPORTER_OTLP_* environment variables.
    """
    state = _state.STATE
    if state.enabled:
        return True
    if not _wanted(enabled):
        return False

    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        LOGGER.warning(
            "telemetry is wanted but opentelemetry is not installed, "
            "install maas-model[telemetry]"
        )
        return False

    hostname = socket.gethostname()
    deployment = deployment or os.environ.get("MAAS_DEPLOYMENT") or hostname
    attributes = {
        "service.namespace": "maas",
        "service.name": os.environ.get("OTEL_SERVICE_NAME") or f"maas-{component}",
        "service.instance.id": instance_id
        or os.environ.get("MAAS_INSTANCE_ID")
        or f"{hostname}-{os.getpid()}",
        "service.version": project_version or _maas_model_version(),
        "maas.component": component,
        "maas.deployment": deployment,
    }
    if collector_type:
        attributes["maas.collector.type"] = collector_type
    resource = Resource.create(attributes)

    try:
        tracer_provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        if span_exporter is not None:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor

            tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

        if metric_reader is None:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(), export_interval_millis=_export_interval_ms()
            )
        meter_provider = MeterProvider(
            resource=resource, metric_readers=[metric_reader], shutdown_on_exit=False
        )
    except ImportError:
        LOGGER.warning(
            "telemetry is wanted but the OTLP exporter is not installed, "
            "install maas-model[telemetry]",
            exc_info=True,
        )
        return False

    state.tracer_provider = tracer_provider
    state.meter_provider = meter_provider
    state.tracer = tracer_provider.get_tracer(SCOPE_NAME)
    state.meter = meter_provider.get_meter(SCOPE_NAME)
    state.base_attributes = {"component": component, "deployment": deployment}
    state.project = project
    state.project_version = project_version
    state.instrument_opensearch = instrument_opensearch
    state.generation += 1
    state.enabled = True

    global _atexit_registered
    if not _atexit_registered:
        atexit.register(shutdown)
        _atexit_registered = True

    if instrument_opensearch:
        try:
            from maas_model.telemetry import opensearch

            opensearch.instrument_opensearch()
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("opensearch instrumentation failed", exc_info=True)

    LOGGER.info("telemetry enabled for %s", attributes["service.name"])
    return True


def shutdown(timeout_s: float = 5.0) -> None:
    """
    Flush and shut the providers down, then disable telemetry.

    Bounded by timeout_s: an unreachable collector must not hold the process
    up. Whatever is still unsent after that is dropped.
    """
    state = _state.STATE
    providers = [state.tracer_provider, state.meter_provider]

    def stop():
        for provider in providers:
            if provider is None:
                continue
            try:
                provider.force_flush()
                provider.shutdown()
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("telemetry provider shutdown failed", exc_info=True)

    thread = threading.Thread(target=stop, name="telemetry-shutdown", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        LOGGER.warning("telemetry shutdown timed out after %ss", timeout_s)
    _state.reset()
