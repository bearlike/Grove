"""The protobuf boundary: Python values in and out of an OTLP span.

Isolated into one module because ``AnyValue`` is a seven-way ``oneof`` and every
call site that unwraps it by hand gets a *different* subset of the cases right —
the one that always gets forgotten is that an unset ``AnyValue`` has no active
field at all, so a bare ``value.string_value`` reads a well-typed empty string
for an attribute nobody sent.

**Nothing here rebuilds a span's attribute list.** :meth:`OtlpAttributes.apply`
overwrites the keys it is handed and leaves every other attribute exactly where
it was, which is what lets the transform re-spell the handful of keys it
understands while an array-valued attribute it has no opinion about
(``gen_ai.response.finish_reasons``, measured on a live 2.1.227 span) survives
untouched. A rewrite-from-scratch would silently drop precisely the attributes
Grove has not been taught about yet — the failure mode that outlives us.

Importing this module requires the optional ``telemetry`` extra (it is
``opentelemetry-proto``, pulled in by the OTLP exporter). That is why
:mod:`grove.core.telemetry` does not re-export anything from here: the
vocabulary in :mod:`grove.core.telemetry.semconv` must stay importable on a
bare install.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

AttributeValue = str | int | float | bool | tuple[str, ...]
"""The attribute types Grove reads and writes.

Deliberately the same union :class:`grove.core.trace.SpanRecord` uses, because
every value written back through here came out of a ``SpanRecord`` — so the two
must be widened together. They were not, once: ``langfuse.trace.tags`` is
specified as ``string[]``, and a tuple reaching :meth:`OtlpAttributes._assign`
before the array arm existed would have been assigned to ``string_value``,
which protobuf rejects at runtime. The re-export tier is the only caller that
would have hit it, and only for a span Grove had stamped tags onto — a narrow
enough path to have shipped unnoticed.
"""

_NS_PER_SECOND = 1_000_000_000


class OtlpAttributes:
    """Read scalars out of an OTLP attribute list, and merge scalars back in.

    A class rather than two free functions because the pair is one contract:
    :meth:`scalars` defines what "a value Grove can reason about" means, and
    :meth:`apply` is the only writer that honours it. Splitting them is how a
    reader that understands ``double_value`` acquires a writer that cannot
    produce one.
    """

    @staticmethod
    def value_of(value: AnyValue) -> AttributeValue | None:
        """The scalar behind an ``AnyValue``, or ``None`` for unset/composite.

        ``bool_value`` is checked by the ``oneof`` rather than by ``isinstance``
        because Python's ``bool`` is an ``int``: a truthiness- or type-based
        unwrap turns ``success=False`` into ``0`` and back into an integer
        attribute, which reads as a count.
        """
        match value.WhichOneof("value"):
            case "string_value":
                return value.string_value
            case "bool_value":
                return value.bool_value
            case "int_value":
                return value.int_value
            case "double_value":
                return value.double_value
            case _:
                # array/kvlist/bytes/unset. Composite values pass through
                # untouched rather than being flattened — see the module
                # docstring.
                return None

    @staticmethod
    def scalars(attributes: Iterable[KeyValue]) -> dict[str, AttributeValue]:
        """Every scalar attribute as a plain dict, composites omitted."""
        resolved: dict[str, AttributeValue] = {}
        for attribute in attributes:
            value = OtlpAttributes.value_of(attribute.value)
            if value is not None:
                resolved[attribute.key] = value
        return resolved

    @staticmethod
    def apply(span: Span, values: Mapping[str, AttributeValue]) -> None:
        """Merge ``values`` onto ``span``, overwriting by key and adding the rest."""
        existing = {attribute.key: attribute for attribute in span.attributes}
        for key, value in values.items():
            target = existing.get(key)
            if target is None:
                target = span.attributes.add()
                target.key = key
            OtlpAttributes._assign(target.value, value)

    @staticmethod
    def _assign(target: AnyValue, value: AttributeValue) -> None:
        """Set an ``AnyValue`` from a Python value. ``bool`` before ``int``.

        The tuple arm CLEARS the array before filling it, because ``apply``
        overwrites an existing attribute in place: re-stamping a span whose
        key already held an array would otherwise append to it, and a tag list
        that grows by one copy of itself per export is a bug that only appears
        under retry.
        """
        if isinstance(value, bool):
            target.bool_value = value
        elif isinstance(value, int):
            target.int_value = value
        elif isinstance(value, float):
            target.double_value = value
        elif isinstance(value, tuple):
            del target.array_value.values[:]
            for element in value:
                target.array_value.values.add().string_value = element
        else:
            target.string_value = value


def ns_to_datetime(nanoseconds: int) -> datetime:
    """OTLP epoch-nanoseconds as an aware UTC datetime.

    Divides rather than using ``fromtimestamp(ns / 1e9)``: a float second count
    loses sub-microsecond resolution at 2026 epochs, and these timestamps are
    what a span's rendered width is computed from.
    """
    seconds, remainder = divmod(nanoseconds, _NS_PER_SECOND)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=remainder // 1000)


def datetime_to_ns(moment: datetime) -> int:
    """An aware datetime as OTLP epoch-nanoseconds (naive is read as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * _NS_PER_SECOND)


class SpanEnvelope:
    """One span together with the resource and scope it arrived under.

    The buffer groups by TRACE while OTLP groups by ``(resource, scope)``, so a
    trace's spans have to be carried out of their envelopes and put back into
    new ones. Keeping the two descriptors beside each span is what makes
    :func:`repack` lossless — a rebuild that assumed one resource per request
    would silently merge two services' spans the first time an agent exported
    through a shared collector.

    Not a ``@dataclass``: protobuf messages define ``__eq__`` by value and are
    unhashable, and a slotted dataclass here would only add a frozen-ness this
    type cannot honour anyway (``span`` is mutated in place by the transform).
    """

    __slots__ = ("resource", "resource_schema_url", "scope", "scope_schema_url", "span")

    def __init__(
        self,
        *,
        resource: Resource,
        resource_schema_url: str,
        scope: InstrumentationScope,
        scope_schema_url: str,
        span: Span,
    ) -> None:
        self.resource = resource
        self.resource_schema_url = resource_schema_url
        self.scope = scope
        self.scope_schema_url = scope_schema_url
        self.span = span

    @staticmethod
    def unpack(resource_spans: Iterable[ResourceSpans]) -> list[SpanEnvelope]:
        """Flatten OTLP's two-level envelope into one span per element."""
        return [
            SpanEnvelope(
                resource=rs.resource,
                resource_schema_url=rs.schema_url,
                scope=ss.scope,
                scope_schema_url=ss.schema_url,
                span=span,
            )
            for rs in resource_spans
            for ss in rs.scope_spans
            for span in ss.spans
        ]


def repack(envelopes: Sequence[SpanEnvelope]) -> list[ResourceSpans]:
    """Rebuild OTLP envelopes, one ``ScopeSpans`` per distinct scope.

    Grouping is by the descriptors' SERIALIZED bytes rather than by identity:
    the same logical resource arrives as a fresh message object in every
    request, so an identity key would emit one ``ResourceSpans`` per span and
    multiply the payload by its own descriptor.
    """
    grouped: dict[tuple[bytes, str], dict[tuple[bytes, str], list[Span]]] = {}
    descriptors: dict[tuple[bytes, str], tuple[Resource, str]] = {}
    scopes: dict[tuple[bytes, str], tuple[InstrumentationScope, str]] = {}
    for envelope in envelopes:
        resource_key = (envelope.resource.SerializeToString(), envelope.resource_schema_url)
        scope_key = (envelope.scope.SerializeToString(), envelope.scope_schema_url)
        descriptors.setdefault(resource_key, (envelope.resource, envelope.resource_schema_url))
        scopes.setdefault(scope_key, (envelope.scope, envelope.scope_schema_url))
        grouped.setdefault(resource_key, {}).setdefault(scope_key, []).append(envelope.span)

    packed: list[ResourceSpans] = []
    for resource_key, by_scope in grouped.items():
        resource, resource_schema_url = descriptors[resource_key]
        resource_spans = ResourceSpans(resource=resource, schema_url=resource_schema_url)
        for scope_key, spans in by_scope.items():
            scope, scope_schema_url = scopes[scope_key]
            resource_spans.scope_spans.append(
                ScopeSpans(scope=scope, schema_url=scope_schema_url, spans=spans)
            )
        packed.append(resource_spans)
    return packed
