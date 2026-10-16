# Changelog

## Version 3.2.0

- `MAASQueryMessage` has a new field `output_document_class`, the class the reports
  name when it is not the scanned one. It defaults to an empty string, so existing
  payloads keep their meaning.
- New module `maas_model.telemetry`, shared by the MAAS services, to record
  OpenTelemetry metrics and traces. It is opt-in and needs the new `telemetry` extra,
  which installs `opentelemetry-api`, `opentelemetry-sdk` and
  `opentelemetry-exporter-otlp-proto-http`, all `>=1.30,<2`: a service that enables it
  must require `maas-model[telemetry]`. The floor is 1.30 because the histogram bucket
  advisories need it. The base install is unchanged and never imports opentelemetry,
  and `import maas_model.telemetry` works without it. The `testing` extra now installs
  OpenTelemetry too.
- `configure(component, ...)` turns telemetry on, when `enabled=True` is passed or the
  environment variable `MAAS_TELEMETRY` is `1`, `true`, `yes` or `on`, and exports over
  OTLP/HTTP to the standard `OTEL_EXPORTER_OTLP_*` endpoint. Nothing else is recorded.
  While it is disabled each call costs less than 1 µs and returns at once.
- The module provides `span`, `traced`, `counter`, `histogram`, the decorators `timed`,
  `counted` and `record_latency` for business metrics (names start with
  `maas.business.`), AMQP publish and consume spans in `telemetry.amqp`, the declared
  topology in `telemetry.topology`, and the timing of OpenSearch requests. Label names
  that look like identifiers are refused, and an instrument keeps at most 500 attribute
  sets. See `docs/telemetry.md`.
- `maas_model.telemetry.metrics` holds one ready instrument per counter and histogram
  of the observability Metric spec (`MESSAGES_PUBLISHED`, `ENGINE_RUN_DURATION`,
  `COLLECTOR_FILES`, `OPENSEARCH_TIME`, ...), named after the `semconv` constants, with
  their units and buckets fixed in one place. Engines and collectors record with them.
- `maas_model.telemetry.testing` and its `telemetry_capture` pytest fixture capture the
  spans and metrics of a test in memory. A capture starts and ends with an empty
  topology registry and no activity. `tox -e no-otel` checks that the module works
  without OpenTelemetry installed, and `tox -e perf` checks the 1 µs per call budget of
  disabled telemetry.
- `telemetry.baggage()` attaches baggage to the current context, and `consume_span` sets
  `maas.origin.interface` from it as a span attribute. `publish_span` takes optional
  `headers`, merged into the yielded ones. `declare_route(source="observed")` is capped
  at `semconv.MAX_OBSERVED_ROUTES` (1000) routes, config routes are not.

## Version 3.1.0

- New module `maas_model.vault`, shared by the MAAS services, to read secrets from an
  OpenBao or HashiCorp Vault KV version 2 engine. It needs the new `vault` extra,
  which installs `hvac>=2.3,<3.0`: a service that imports it must require
  `maas-model[vault]`. The base install is unchanged and does not pull hvac. The
  `testing` extra now installs hvac too.
- `VaultSettings` holds the vault address (`url`), the mount point of the KV engine
  (`kv_mount`, `secret` by default) and the authentication: `auth_method` is
  `approle` (the default), with `role_id` and `secret_id_file`, or `token`, with an
  optional `token_file`. It reads no environment variable: each service fills it
  from its own settings.
- `VaultClient` checks its settings when it is created. A missing setting, or an
  unknown auth method, raises a `ValueError` naming the services' environment
  variable to set: `VAULT_ADDR`, `VAULT_AUTH_METHOD` (`approle` or `token`),
  `VAULT_ROLE_ID` and `VAULT_SECRET_ID_FILE` for AppRole, `VAULT_TOKEN` or
  `VAULT_TOKEN_FILE` for a token.
- AppRole login reads the secret id file again at each login, so a rotated secret id
  is used at the next login, without a restart. Token authentication reads
  `token_file` at each login, or else takes the token from `VAULT_TOKEN` or
  `~/.vault-token`, like the vault CLI. Secret files may end with a new line. TLS
  verification follows `VAULT_CACERT` and `VAULT_CAPATH`, like the vault CLI.
- The client logs in at its first read and keeps its token. When the vault refuses a
  read, the client looks its token up (`auth/token/lookup-self`): only a token that
  is no longer valid, expired or revoked, makes it log in again and retry the read
  once. A valid token refused by the policy raises `hvac.exceptions.Forbidden`
  without a new login. Other errors, such as a sealed or unreachable vault, are
  raised at once.
- Telling an expired token from a policy refusal needs read access to
  `auth/token/lookup-self`, which the default policy grants. Without it, the client
  takes a policy refusal for an expired token: it logs in again and retries once
  before raising `Forbidden`.
- A token taken from `VAULT_TOKEN` or `~/.vault-token` is never replaced: once it
  expires, reads raise `hvac.exceptions.Forbidden`. For a token renewed outside the
  service, use `token_file` (`VAULT_TOKEN_FILE` in the services).
- `VaultClient.read_secret(path)` returns the latest version of a secret, or `None`
  when there is no such secret. The path leaves out the mount point and the `data/`
  segment of the KV version 2 API.
- `read_connection(client, path, service)` turns a secret holding `url`, `username`
  and `password` into a `ConnectionSettings`, for a service such as OpenSearch or
  AMQP. `username` and `password` may be left out for a service without
  authentication. A missing secret, or one without `url`, raises a `ValueError` that
  names the service and the secret, never a secret value.
- `ConnectionSettings` leaves the password out of its `repr`, so it does not show
  when the settings are logged.
- New `maas_model.testing.fakevault.FakeVault`, for the tests of the services: an
  in-process stand-in for the vault HTTP API (AppRole login, token lookup, KV
  version 2 read and write) that real hvac clients talk to. It can expire the tokens
  of its AppRole logins, deny paths and answer like a sealed vault.
- Package metadata: the author is now Serco - Space
  (`dev-eof@coordination-service.eu`) instead of telespazio, and the project URL is
  <https://github.com/CSC-Operations-Coordination-Service/maas-model>.

Version 3.0.0 imported maas-model from the Technical-Dashboard repository
(`modules/maas-model`); no changelog was kept before 3.1.0.
