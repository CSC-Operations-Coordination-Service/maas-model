# Telemetry

`maas_model.telemetry` records OpenTelemetry metrics and traces for the MAAS services,
engines and collectors. It is opt-in: with telemetry disabled, which is the default, every
call is a no-op costing less than 1 µs, and `opentelemetry` is never imported.

This guide is for the developers of engines and collectors.

## 1. Enabling telemetry

Install the extra, it installs `opentelemetry-api`, `opentelemetry-sdk` and
`opentelemetry-exporter-otlp-proto-http`, all `>=1.30,<2`:

```console
pip install "maas-model[telemetry]"
```

A service must require `maas-model[telemetry]` if it enables telemetry. Without the extra,
`configure()` logs a warning, returns `False` and the service runs as before.

Call `configure()` once, at start-up, and `shutdown()` before exiting (it is also
registered with `atexit`):

```python
from maas_model import telemetry

telemetry.configure("engine", project="maas-engine", project_version="1.2.3")
...
telemetry.shutdown()
```

`configure()` returns whether telemetry is enabled. Telemetry is enabled when
`enabled=True` is passed, or when `enabled` is left to `None` and `MAAS_TELEMETRY` is
`1`, `true`, `yes` or `on`, and OpenTelemetry can be imported. A second call returns the
current state. The module owns its providers and never sets the OpenTelemetry global ones.

| Environment variable | Effect |
|---|---|
| `MAAS_TELEMETRY` | `1`, `true`, `yes` or `on` enables telemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | where the data is sent |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | use `http/protobuf`, the only protocol the extra installs |
| `OTEL_SERVICE_NAME` | `service.name`, default `maas-<component>` |
| `MAAS_DEPLOYMENT` | `maas.deployment`, default the host name |
| `MAAS_INSTANCE_ID` | `service.instance.id`, default `<hostname>-<pid>` |
| `OTEL_METRIC_EXPORT_INTERVAL` | metric export interval in ms, default 30000 |
| `OTEL_RESOURCE_ATTRIBUTES` | merged into the resource |

The `component` argument (`engine`, `collector`, ...) becomes `maas.component`.
`collector_type` adds `maas.collector.type`. The `deployment` and `instance_id` arguments
take precedence over the environment. Traces use the SDK default sampler
(`parentbased_always_on`), metrics use cumulative temporality.

The OpenSearch client is instrumented when telemetry is enabled, pass
`instrument_opensearch=False` to opt out.

## 2. What you get for free

Once `configure()` has enabled telemetry, the library records:

- AMQP publish and consume spans and metrics, through `telemetry.amqp.publish_span()`,
  `telemetry.amqp.consume_span()` and `telemetry.amqp.returned()`. The trace context
  travels in the message headers. `publish_span(..., headers=...)` merges the caller's
  headers into the ones it yields (the trace keys win, the mapping is not mutated).
- Baggage: `with telemetry.baggage({"maas.origin.interface": "PRIP_S1"}):` attaches it to
  the current context, so it travels with the headers and `consume_span` sets it as the
  span attribute `maas.origin.interface`. Disabled, it is a reusable no-op.
- OpenSearch request duration, time and count, per operation, index and node, with no
  code of the service's own.
- The declared topology and the build, as info metrics: `telemetry.topology.declare_route()`
  and `declare_node()`. Observed routes (`source="observed"`) are capped at
  `semconv.MAX_OBSERVED_ROUTES` (1000): past it new ones are ignored, with one warning.

The engine and collector metrics (`maas.engine.*`, `maas.collector.*`) arrive once
maas-engine and maas-collector integrate the library (Phases 2 and 3): they record them
with the instruments of `telemetry.metrics`.

The list of metrics and their labels is the "Metric spec" table of the design spec,
`maas-development` repository, `docs/superpowers/specs/2026-10-16-maas-observability-design.md`,
section "Metric spec".

### The spec metrics: `telemetry.metrics`

`telemetry.metrics` holds one ready instrument per counter and histogram of the Metric
spec table, named after its `telemetry.semconv` constant, with its unit, buckets and
description fixed. Record with them, never define the same metric again:

```python
from maas_model.telemetry import metrics

metrics.ENGINE_DOCUMENTS.add(12, {"node": "COMPUTE_X", "result": "created"})
metrics.ENGINE_RUN_DURATION.record(
    3.2, {"node": "COMPUTE_X", "routing_key": "rk", "outcome": "ok"}
)
```

| Instrument | Kind | Unit | Buckets |
|---|---|---|---|
| `MESSAGES_PUBLISHED`, `MESSAGES_RETURNED`, `MESSAGES_CONSUMED` | counter | `{message}` | |
| `MESSAGE_DOCUMENTS` | histogram | `{document}` | `DOCUMENT_BUCKETS` |
| `MESSAGE_PROCESSING_DURATION` | histogram | `s` | `DURATION_BUCKETS` |
| `ENGINE_RUN_DURATION` | histogram | `s` | `DURATION_BUCKETS` |
| `ENGINE_DOCUMENTS`, `ENGINE_INPUT_DOCUMENTS`, `ENGINE_INPUT_MISSING`, `ENGINE_REPORTS_DOCUMENTS` | counter | `{document}` | |
| `COLLECTOR_FILES` | counter | `{file}` | |
| `COLLECTOR_FILE_DURATION`, `COLLECTOR_LOOP_DURATION` | histogram | `s` | `DURATION_BUCKETS` |
| `COLLECTOR_DOCUMENTS`, `COLLECTOR_DOCUMENTS_ROUTED` | counter | `{document}` | |
| `OPENSEARCH_REQUEST_DURATION` | histogram | `s` | `OPENSEARCH_BUCKETS` |
| `OPENSEARCH_TIME` | counter | `s` | |
| `OPENSEARCH_REQUESTS` | counter | `{request}` | |
| `LATENCY_NEGATIVE` | counter | none | |

The labels of each metric are those of the Metric spec table.

Every recording carries the `component` and `deployment` labels.

Spans are available to the code of a service as well:

```python
with telemetry.span("load documents", attributes={"maas.document.count": 12}) as span:
    span.set_attribute("maas.pipeline", "daily")

@telemetry.traced(kind="internal")
def compute(...): ...
```

`@telemetry.traced` also works bare, without parentheses. `span()` yields a no-op span
while telemetry is disabled. `current_span_context()` and
`set_span_attributes()` act on the current span. `activity(node_type, node)` marks the node
a thread is working on, `current_activity()` reads it from worker threads, which is how the
OpenSearch time is attributed to a node.

## 3. Adding a business metric

A business metric is a metric about what the service computes, not about how it runs. Its
name starts with `maas.business.`. Create the instrument at module level, or use a
decorator. A bad definition raises `ValueError` when it is made. Recording never raises:
an error is logged at DEBUG and the value is dropped.

### Datatake compute rate

Count what is computed, and time how long it takes:

```python
from maas_model import telemetry

DATATAKES = telemetry.counter(
    "maas.business.cds.datatakes.computed", unit="{datatake}"
)


@telemetry.timed("maas.business.cds.datatake.compute.duration")
def compute_datatake(datatake):
    with telemetry.span("compute") as span:
        span.set_attribute("maas.pipeline", datatake.pipeline)
        ...
    DATATAKES.add(1, {"mission": datatake.mission})
```

`timed` records the duration in seconds of each call, with the label `outcome` set to `ok`
or `error`. It works on sync, async and generator functions. `counted` is the decorator
form of the counter, it adds `1`, or `amount(result)`, after each successful call.

### Publication latency

The time between the publication of a product and its ingestion, for each document the
function yields:

```python
@telemetry.record_latency(
    "maas.business.publication.latency",
    start="publication_date",
    end="ingestionTime",
    on="yield",
)
def ingest(files):
    for document in parse(files):
        yield document
```

`start` and `end` are a dotted field path (attribute or dict key), `"@now"`, or a callable
returning a datetime, an ISO 8601 string or `None`. `on` is `"return"` (the result, each
element of a list or tuple), `"yield"` (each item of a generator) or `"arg:<param>"` (a
bound argument). `on="return"` on a generator function raises a `ValueError`: use
`on="yield"`. Naive datetimes are taken as UTC. A document with a missing or unparseable
date is skipped, and a negative delta is counted in `maas.latency.negative` instead of
being recorded.

### Timeliness

The age of a document when the service handles it:

```python
@telemetry.record_latency(
    "maas.business.timeliness", start="ingestionTime", end="@now"
)
def publish(documents):
    ...
    return documents
```

## 4. Rules

- Business metric names start with `maas.business.`, other names are refused with a
  `ValueError`.
- Never put an identifier in a label. The keys `id`, `*_id`, `*_ids`, `message_id`, `file`
  and `file_name` are refused by the decorators with a `ValueError`, and dropped with one
  warning when given to an instrument at record time. Identifiers belong in span
  attributes (for example `maas.message_id`), not in metrics.
- Keep the labels low cardinality, short and flat: a mission, an outcome, a node, never a
  date, a file name or a free text.
- An instrument keeps at most 500 distinct attribute sets. Past that, the value of every
  label other than `component` and `deployment` becomes `other`, and one warning is
  logged for the instrument.
- Label values are strings, `None` becomes `unknown`.

## 5. Prometheus names after OTLP translation

The metrics are pushed over OTLP to Mimir. MAAS's Mimir runs with
`limits.otel_metric_suffixes_enabled: true`, so the names are translated:

- `.` becomes `_`
- the unit `s` adds `_seconds`
- counters add `_total`
- curly-brace units, such as `{document}`, add nothing
- histograms add the `_bucket`, `_sum` and `_count` series

| OpenTelemetry name | Prometheus name |
|---|---|
| `maas.engine.run.duration` (histogram, `s`) | `maas_engine_run_duration_seconds_bucket` |
| `maas.opensearch.time` (counter, `s`) | `maas_opensearch_time_seconds_total` |
| `maas.message.documents` (histogram, `{document}`) | `maas_message_documents_bucket` |
| `maas.business.cds.datatakes.computed` (counter) | `maas_business_cds_datatakes_computed_total` |

Mimir sets `job="maas/<service.name>"`, for example `job="maas/maas-engine"`, and
`instance=<service.instance.id>`. There is no `service_name` label. Dashboards select with
the `component` and `deployment` labels, which every MAAS metric has.

## 6. Testing

`maas_model.telemetry.testing` captures the telemetry of a test in memory. Its
`telemetry_capture` fixture enables telemetry on in-memory exporters and shuts it down at
the end of the test. Import the fixture in the `conftest.py` of your tests, which needs the
`testing` extra (it installs OpenTelemetry):

```python
from types import SimpleNamespace

from maas_model.telemetry.testing import telemetry_capture  # noqa: F401


def test_datatake(telemetry_capture):
    datatake = SimpleNamespace(mission="S1", pipeline="daily")
    compute_datatake(datatake)

    point = telemetry_capture.point(
        "maas.business.cds.datatakes.computed", mission="S1"
    )
    assert point.value == 1
    assert telemetry_capture.span("compute").attributes["maas.pipeline"] == "daily"
```

`spans()` and `span(name)` give the finished spans, `points(name)` and
`point(name, **attrs)` the metric data points: `value` for a counter or gauge, `count` and
`sum` for a histogram. `testing.capture(component)` does the same without pytest. The metric
names are the OpenTelemetry ones, with dots.

The capture starts and ends with an empty topology registry and no activity: routes and
nodes declared before it, at import time for example, are forgotten, so declare them
inside the test that checks them.

A code base that must also work without OpenTelemetry can run its no-op tests with
`tox -e no-otel`, which installs no OpenTelemetry at all.

In maas-model, `tox -e perf` checks the 1 µs per call budget of disabled telemetry. The
check needs to run without coverage, so the default test run skips it.
