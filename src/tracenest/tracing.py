"""Shared tracing primitives that remove boilerplate from integration wrappers."""

import contextlib
from typing import Any, Dict, Iterator, Optional

from opentelemetry.trace import Context, Span, SpanKind, StatusCode, get_tracer


@contextlib.contextmanager
def traced_span(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: str = "tracenest",
    context: Optional[Context] = None,
) -> Iterator[Span]:
    """
    Start a span and collapse the standard try/success/error/raise wrapper.

    On success the span is marked OK unless the caller already set a status.
    On failure the exception is recorded, error attributes are set, the span
    is marked ERROR, and the exception is re-raised.

    Yields the active Span so callers can enrich it after the wrapped call.
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes,
        context=context,
    ) as span:
        try:
            yield span
            if span.is_recording() and hasattr(span, "status") and span.status.status_code == StatusCode.UNSET:
                span.set_status(StatusCode.OK)
        except Exception as exc:
            if span.is_recording():
                span.record_exception(exc)
                span.set_attribute("error", True)
                span.set_attribute("error.type", exc.__class__.__name__)
                span.set_status(StatusCode.ERROR, description=str(exc))
            raise


@contextlib.contextmanager
def reentrant_guard(instance: Any, attr: str) -> Iterator[bool]:
    """
    Guard a traced wrapper against recursive invocation on the same instance.

    Yields True for the outermost call (trace) and False when the guard is
    already held (call through without tracing). The flag is always cleared,
    even when the wrapped call raises.
    """
    if getattr(instance, attr, False):
        yield False
        return
    setattr(instance, attr, True)
    try:
        yield True
    finally:
        setattr(instance, attr, False)