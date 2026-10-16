"""
Opt-in OpenTelemetry metrics and traces for the MAAS services.

Install with the telemetry extra: pip install maas-model[telemetry]

Importing this package never imports opentelemetry. Nothing is recorded until
configure() enables telemetry, from its enabled argument or MAAS_TELEMETRY.
"""

from maas_model.telemetry import semconv
from maas_model.telemetry._setup import configure, enabled, shutdown

__all__ = ["configure", "shutdown", "enabled", "semconv"]
