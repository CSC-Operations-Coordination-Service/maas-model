"""
Opt-in OpenTelemetry metrics and traces for the MAAS services.

Install with the telemetry extra: pip install maas-model[telemetry]

Importing this package never imports opentelemetry. Nothing is recorded until
configure() enables telemetry, from its enabled argument or MAAS_TELEMETRY.
"""

from maas_model.telemetry import semconv
from maas_model.telemetry._instruments import counter, histogram
from maas_model.telemetry._setup import configure, enabled, shutdown
from maas_model.telemetry.activity import Activity, activity, current_activity
from maas_model.telemetry.tracing import (
    current_span_context,
    set_span_attributes,
    span,
    traced,
)

__all__ = [
    "configure",
    "shutdown",
    "enabled",
    "semconv",
    "counter",
    "histogram",
    "span",
    "traced",
    "current_span_context",
    "set_span_attributes",
    "activity",
    "current_activity",
    "Activity",
]
