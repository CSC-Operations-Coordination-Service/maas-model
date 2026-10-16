"""
Process wide telemetry state.

The module owns its tracer and meter providers: the OpenTelemetry global
providers are never set, so it does not interfere with an application that
configures its own.
"""

import dataclasses
from typing import Any, Dict, List


@dataclasses.dataclass
class State:
    """what configure() set up"""

    enabled: bool = False

    tracer: Any = None

    meter: Any = None

    tracer_provider: Any = None

    meter_provider: Any = None

    # attributes added to every metric recording
    base_attributes: Dict[str, str] = dataclasses.field(default_factory=dict)

    project: str = ""

    project_version: str = ""

    instrument_opensearch: bool = False

    # the OTLP exporters configure() created, shut down to stop their retries
    otlp_exporters: List[Any] = dataclasses.field(default_factory=list)

    # bumped by reset(), so that cached instruments know they are stale
    generation: int = 0


STATE = State()


def reset() -> None:
    """disable telemetry and forget the providers, without shutting them down"""
    STATE.enabled = False
    STATE.tracer = None
    STATE.meter = None
    STATE.tracer_provider = None
    STATE.meter_provider = None
    STATE.base_attributes = {}
    STATE.project = ""
    STATE.project_version = ""
    STATE.instrument_opensearch = False
    STATE.otlp_exporters = []
    STATE.generation += 1
