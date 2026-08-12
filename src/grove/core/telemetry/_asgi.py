"""The OTLP/HTTP surface — the only module here that knows what a request is.

Split from :mod:`grove.core.telemetry.receiver` for one concrete reason rather
than a taste for layers: **FastAPI resolves a handler's annotations against its
module's globals**, and this package uses ``from __future__ import annotations``,
so a route defined under a function-local ``from fastapi import Request`` has an
unresolvable string annotation and FastAPI silently re-reads the parameter as a
query field — every POST answers 422 and nothing in the message mentions
imports. Putting the routes in a module that imports FastAPI at the top makes
the annotations resolvable, and keeps the pure core importable on an install
without the ``daemon`` extra, which is the property that mattered.

Everything difficult lives next door. This module parses a body, hands it to a
queue, and serializes an answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, Response
from loguru import logger
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from grove.core.telemetry.receiver import OtlpIngest


def build_receiver_app(*, ingest: OtlpIngest | None = None) -> FastAPI:
    """The OTLP receiver as a standalone ASGI app. See :mod:`.receiver`."""
    resolved = ingest if ingest is not None else OtlpIngest()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Standalone-runtime bookends.

        **Mounting does not run this** — Starlette drives only the outermost
        app's lifespan. That is why :meth:`OtlpIngest.submit` starts its own
        worker, and why a mounting daemon that wants a clean stop calls
        ``app.state.ingest.aclose()`` from its own lifespan.
        """
        await resolved.start()
        try:
            yield
        finally:
            await resolved.aclose()

    app = FastAPI(title="Grove OTLP receiver", docs_url=None, redoc_url=None, lifespan=lifespan)
    # Published so a mounting host can reach an ingest it did not construct.
    app.state.ingest = resolved

    @app.post("/v1/traces")
    async def export_traces(request: Request) -> Response:
        body = await request.body()
        try:
            parsed = _parse_traces(body, request.headers.get("content-type", ""))
        except Exception as exc:
            logger.debug("otlp receiver rejected an undecodable trace payload: {}", exc)
            return Response(status_code=400)
        spans = sum(len(ss.spans) for rs in parsed.resource_spans for ss in rs.scope_spans)
        rejected = await resolved.submit(list(parsed.resource_spans), spans=spans)
        return Response(content=_traces_response(rejected), media_type="application/x-protobuf")

    @app.post("/v1/logs")
    async def export_logs(request: Request) -> Response:
        """Accept and park the logs stream — **#500 owns the transform.**

        Not implemented here, and not a 404 either. Codex writes its prompts and
        tool payloads exclusively to the logs stream, and Claude Code writes
        assistant responses and its own native cost there too (measured
        2026-08-11: ``prompt``, ``response``, ``tool_input`` and ``cost_usd``
        all arrive as log records and on no span). A 404 makes an exporter
        retry and then drop them with nothing said, so the seam stays open and
        honest until the logs transform lands.
        """
        body = await request.body()
        logger.debug("otlp receiver parked {} bytes of logs (see #500)", len(body))
        return Response(content=b"", media_type="application/x-protobuf")

    return app


def _parse_traces(body: bytes, content_type: str) -> ExportTraceServiceRequest:
    """Decode an OTLP/HTTP export body, protobuf or JSON.

    Both encodings are part of OTLP/HTTP and an SDK picks between them from
    ``OTEL_EXPORTER_OTLP_PROTOCOL`` — the operator's variable, not Grove's.
    Refusing one would fail a correct configuration with a 400 that names
    nothing.

    The casts are because ``google.protobuf`` ships no type stubs in this
    dependency set, so every generated message method is ``Any`` and
    ``warn_return_any`` fires. Adding ``types-protobuf`` would type them for
    real, and would also newly type-check :mod:`grove.core.trace`'s exporter
    subclass — a change to another module's gate, so it is a separate decision
    rather than a side effect of this one.
    """
    if "json" in content_type:
        from google.protobuf import json_format  # type: ignore[import-untyped] # noqa: PLC0415

        parsed = json_format.Parse(body.decode(), ExportTraceServiceRequest())
        return cast(ExportTraceServiceRequest, parsed)
    return cast(ExportTraceServiceRequest, ExportTraceServiceRequest.FromString(body))


def _traces_response(rejected: int) -> bytes:
    """A serialized OTLP response, carrying partial success when spans were shed.

    A bare 200 over a shed batch is a lie the sender cannot detect, and OTLP
    already has the field for saying so — the sender's own dropped-span metric
    then reports the truth without anyone reading Grove's logs.
    """
    response = ExportTraceServiceResponse()
    if rejected:
        response.partial_success.rejected_spans = rejected
        response.partial_success.error_message = (
            "grove otlp receiver queue full; spans shed to avoid agent backpressure"
        )
    return cast(bytes, response.SerializeToString())
