# OpenTelemetry Collector

This optional deployment sits between native agent telemetry and Langfuse. It
copies bare token fields into the `gen_ai.usage.*` fields Langfuse prices and
normalizes the session id onto spans. Do not point agents directly at Langfuse
if usage accounting relies on these transformations.

## Deploy with Portainer

This repository contains portable stack intent, not a host deployment. Create
or update the stack in Portainer. Set these stack environment values there:

| Variable | Purpose |
| --- | --- |
| `LANGFUSE_OTLP_ENDPOINT` | Langfuse OTLP HTTP endpoint |
| `LANGFUSE_BASIC_AUTH` | HTTP `Authorization` header value |
| `OTEL_COLLECTOR_CONFIG_PATH` | Absolute host path to this deployed `config.yaml` |
| `OTEL_COLLECTOR_NETWORK` | Existing external Docker network shared with consumers |

Keep secret values in Portainer's protected stack environment or its configured
secret-projection mechanism. Do not commit an environment file.

The stack publishes OTLP HTTP on `4318` and OTLP gRPC on `4317`. Host processes
can use the loopback HTTP endpoint. A container must use its own reachable host
gateway; `127.0.0.1` is the container itself and will not reach the collector.

## Verify the service

The collector image is distroless, so it cannot run a shell-based Docker
healthcheck. Portainer accepting a stack update only proves that it accepted the
request; separately verify:

1. the service is running without restart churn;
2. `POST /v1/traces` on the published HTTP receiver returns an OTLP success
   response for a syntactically valid probe;
3. the collector logs show no exporter error after traffic arrives; and
4. Portainer's stack environment still contains non-empty endpoint and
   authorization values before any read-modify-write redeploy.

An end-to-end trace must be checked in the configured Langfuse project. Confirm
a new, distinct session contains both an original token field (for example,
`input_tokens`) and its corresponding `gen_ai.usage.*` field. A zero cost after
those fields arrive is a Langfuse pricing-model concern, not a reason to bypass
the collector.
