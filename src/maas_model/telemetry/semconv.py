"""
Metric names, span attribute keys and histogram buckets.

Single source of truth for the names the MAAS services export.
"""

import re

# messages, unit {message}
MESSAGES_PUBLISHED = "maas.messages.published"
MESSAGES_RETURNED = "maas.messages.returned"
MESSAGES_CONSUMED = "maas.messages.consumed"

# unit {document}
MESSAGE_DOCUMENTS = "maas.message.documents"

# unit s
MESSAGE_PROCESSING_DURATION = "maas.message.processing.duration"

# engine
ENGINE_RUN_DURATION = "maas.engine.run.duration"
ENGINE_DOCUMENTS = "maas.engine.documents"
ENGINE_INPUT_DOCUMENTS = "maas.engine.input.documents"
ENGINE_INPUT_MISSING = "maas.engine.input.missing"
ENGINE_REPORTS_DOCUMENTS = "maas.engine.reports.documents"

# collector
COLLECTOR_FILES = "maas.collector.files"
COLLECTOR_FILE_DURATION = "maas.collector.file.duration"
COLLECTOR_DOCUMENTS = "maas.collector.documents"
COLLECTOR_DOCUMENTS_ROUTED = "maas.collector.documents.routed"
COLLECTOR_LOOP_DURATION = "maas.collector.loop.duration"

# OpenSearch requests
OPENSEARCH_REQUEST_DURATION = "maas.opensearch.request.duration"

# counter, unit s
OPENSEARCH_TIME = "maas.opensearch.time"
OPENSEARCH_REQUESTS = "maas.opensearch.requests"

# topology and build
TOPOLOGY_ROUTE_INFO = "maas.topology.route_info"
NODE_INFO = "maas.node_info"
BUILD_INFO = "maas.build_info"

LATENCY_NEGATIVE = "maas.latency.negative"

# every business metric name starts with this
BUSINESS_PREFIX = "maas.business."

# histogram buckets
DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    120,
    300,
    600,
)
OPENSEARCH_BUCKETS = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
)
DOCUMENT_BUCKETS = (1, 5, 10, 50, 100, 500, 1000, 5000)
LATENCY_BUCKETS = (
    1,
    5,
    15,
    30,
    60,
    120,
    300,
    600,
    1800,
    3600,
    7200,
    14400,
    21600,
    43200,
    86400,
    172800,
    604800,
)

# metric label names that look like identifiers are refused
ID_LABEL_DENYLIST = re.compile(r"^(id|.*_id|.*_ids|message_id|file(_name)?)$")

# span attribute keys
ATTR_MESSAGE_ID = "maas.message_id"
ATTR_DOCUMENT_COUNT = "maas.document.count"
ATTR_DOCUMENT_CLASS = "maas.document.class"
ATTR_PIPELINE = "maas.pipeline"
ATTR_ANCESTOR_IDS = "maas.message.ancestor_ids"
ATTR_OUTCOME = "maas.message.outcome"
ATTR_REASON = "maas.message.reject_reason"
ATTR_REDELIVERED = "maas.message.redelivered"
ATTR_ENGINE_ID = "maas.engine.id"
ATTR_INTERFACE = "maas.interface"
ATTR_FILE_NAME = "maas.file.name"
ATTR_REPLAY = "maas.replay"
ATTR_NODE_TYPE = "maas.node_type"
ATTR_NODE = "maas.node"
