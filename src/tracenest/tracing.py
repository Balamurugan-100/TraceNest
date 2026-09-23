"""Shared tracing primitives that remove boilerplate from integration wrappers."""

import contextlib
import contextvars
from typing import Any, Dict, FrozenSet, Iterator, Optional, Tuple

from opentelemetry.trace import Context, Span, SpanKind, StatusCode, get_tracer

from tracenest.route_context import get_current_method, get_current_route

_active_reentrant_guards: contextvars.ContextVar[FrozenSet[Tuple[int, str]]] = contextvars.ContextVar(
    "_tracenest_active_guards", default=frozenset()
)


def _with_request_route(attributes: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Merge the in-flight Django request route into span attributes.

    Lets per-endpoint spanmetrics series (``http_route`` label) be emitted
    for child spans even though the SERVER span only learns its normalized
    route after the handler returns. Never overwrites explicit attributes.
    """
    route = get_current_route()
    if not route:
        return attributes
    attrs = dict(attributes) if attributes else {}
    if "http.route" not in attrs:
        attrs["http.route"] = route
    return attrs


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
        attributes=_with_request_route(attributes),
        context=context,
        record_exception=False,
        set_status_on_exception=False,
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
    Guard a traced wrapper against recursive invocation on the same instance / context.

    Thread-safe and async-safe via contextvars, preventing cross-thread race
    conditions on shared database connections or cursors.

    Yields True for the outermost call (trace) and False when the guard is
    already held (call through without tracing).
    """
    guard_key = (id(instance), attr)
    active = _active_reentrant_guards.get()
    if guard_key in active:
        yield False
        return

    token = _active_reentrant_guards.set(active | {guard_key})
    try:
        yield True
    finally:
        try:
            _active_reentrant_guards.reset(token)
        except Exception:
            pass