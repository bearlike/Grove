"""Grove's OpenTelemetry gateway — the shape agent work lands in, and the
machinery that puts it there.

Grove sits in the OTLP data path here, which is the one sentence that
separates this package from :mod:`grove.core.otel_resource`. That module
stamps identity onto an env var and steps out of the way; this package
collects, transforms and re-exports, so a fleet of mixed harnesses lands as
one vocabulary regardless of which agent produced it.

:mod:`grove.core.telemetry.semconv` is the contract every other member codes
against. Read it first.

**The gateway members are deliberately NOT re-exported below.**
:mod:`~grove.core.telemetry.receiver` and
:mod:`~grove.core.telemetry.claude_code` import ``opentelemetry.proto`` at
module scope — they operate on protobuf messages, so there is nothing to defer
— and the ASGI shell needs FastAPI on top of that. Re-exporting either here
would drag the optional ``telemetry`` and ``daemon`` extras into every consumer
of the vocabulary, which is exactly what :mod:`grove.core.trace` bends over
backwards with lazy imports to avoid. Import them by module path.
"""

from grove.core.telemetry.semconv import (
    ChatMessage,
    GenAiAttr,
    GenAiOperation,
    GroveIdentityAttr,
    GroveLiveAttr,
    LangfuseAttr,
    ObservationKind,
    ObservationShape,
    TextPart,
    ToolCallPart,
    ToolCallResponsePart,
)

__all__ = [
    "ChatMessage",
    "GenAiAttr",
    "GenAiOperation",
    "GroveIdentityAttr",
    "GroveLiveAttr",
    "LangfuseAttr",
    "ObservationKind",
    "ObservationShape",
    "TextPart",
    "ToolCallPart",
    "ToolCallResponsePart",
]
