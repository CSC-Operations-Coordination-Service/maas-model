"""
The instruments of the MAAS metrics, ready to record.

One lazy proxy per counter and histogram of the "Metric spec" table, named
after its semconv constant, with its unit, buckets and description fixed here
and only here: record with them, never redefine them.

    from maas_model.telemetry import metrics

    metrics.ENGINE_DOCUMENTS.add(12, {"node": "COMPUTE_X", "result": "created"})

Like every instrument they do nothing while telemetry is disabled and never
raise when recording.
"""

from maas_model.telemetry import semconv
from maas_model.telemetry._instruments import _counter, _histogram

# messages

MESSAGES_PUBLISHED = _counter(
    semconv.MESSAGES_PUBLISHED, unit="{message}", description="messages published"
)
MESSAGES_RETURNED = _counter(
    semconv.MESSAGES_RETURNED,
    unit="{message}",
    description="messages returned as unroutable",
)
MESSAGES_CONSUMED = _counter(
    semconv.MESSAGES_CONSUMED, unit="{message}", description="messages consumed"
)
MESSAGE_DOCUMENTS = _histogram(
    semconv.MESSAGE_DOCUMENTS,
    unit="{document}",
    description="documents per message",
    boundaries=semconv.DOCUMENT_BUCKETS,
)
MESSAGE_PROCESSING_DURATION = _histogram(
    semconv.MESSAGE_PROCESSING_DURATION,
    unit="s",
    description="message processing duration",
    boundaries=semconv.DURATION_BUCKETS,
)

# engine

ENGINE_RUN_DURATION = _histogram(
    semconv.ENGINE_RUN_DURATION,
    unit="s",
    description="engine run duration",
    boundaries=semconv.DURATION_BUCKETS,
)
ENGINE_DOCUMENTS = _counter(
    semconv.ENGINE_DOCUMENTS,
    unit="{document}",
    description="documents handled by an engine, per result",
)
ENGINE_INPUT_DOCUMENTS = _counter(
    semconv.ENGINE_INPUT_DOCUMENTS,
    unit="{document}",
    description="input documents loaded by an engine",
)
ENGINE_INPUT_MISSING = _counter(
    semconv.ENGINE_INPUT_MISSING,
    unit="{document}",
    description="input documents an engine could not find",
)
ENGINE_REPORTS_DOCUMENTS = _counter(
    semconv.ENGINE_REPORTS_DOCUMENTS,
    unit="{document}",
    description="documents reported by an engine",
)

# collector

COLLECTOR_FILES = _counter(
    semconv.COLLECTOR_FILES, unit="{file}", description="files collected"
)
COLLECTOR_FILE_DURATION = _histogram(
    semconv.COLLECTOR_FILE_DURATION,
    unit="s",
    description="duration of the collection of a file",
    boundaries=semconv.DURATION_BUCKETS,
)
COLLECTOR_DOCUMENTS = _counter(
    semconv.COLLECTOR_DOCUMENTS,
    unit="{document}",
    description="documents extracted by a collector, per result",
)
COLLECTOR_DOCUMENTS_ROUTED = _counter(
    semconv.COLLECTOR_DOCUMENTS_ROUTED,
    unit="{document}",
    description="documents routed by a collector",
)
COLLECTOR_LOOP_DURATION = _histogram(
    semconv.COLLECTOR_LOOP_DURATION,
    unit="s",
    description="duration of a collector loop",
    boundaries=semconv.DURATION_BUCKETS,
)

# OpenSearch

OPENSEARCH_REQUEST_DURATION = _histogram(
    semconv.OPENSEARCH_REQUEST_DURATION,
    unit="s",
    description="OpenSearch request duration",
    boundaries=semconv.OPENSEARCH_BUCKETS,
)
OPENSEARCH_TIME = _counter(
    semconv.OPENSEARCH_TIME,
    unit="s",
    description="time spent in OpenSearch requests",
)
OPENSEARCH_REQUESTS = _counter(
    semconv.OPENSEARCH_REQUESTS,
    unit="{request}",
    description="OpenSearch requests",
)

# record_latency

LATENCY_NEGATIVE = _counter(
    semconv.LATENCY_NEGATIVE,
    description="latencies not recorded because end was before start",
)
