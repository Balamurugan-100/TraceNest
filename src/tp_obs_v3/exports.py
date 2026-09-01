from typing import Optional
from opentelemetry import trace
from opentelemetry.trace import (
    Span,
    SpanContext,
    SpanKind,
    StatusCode,
    Tracer,
    TracerProvider,
    get_current_span,
    get_tracer_provider,
    set_tracer_provider,
)


def get_tracer(
    instrumenting_module_name: str,
    instrumenting_library_version: Optional[str] = None,
    tracer_provider: Optional[TracerProvider] = None,
    schema_url: Optional[str] = None,
) -> Tracer:
    """Get a Tracer from the specified or active global TracerProvider."""
    return trace.get_tracer(
        instrumenting_module_name,
        instrumenting_library_version=instrumenting_library_version,
        tracer_provider=tracer_provider,
        schema_url=schema_url,
    )


__all__ = [
    "trace",
    "Tracer",
    "TracerProvider",
    "Span",
    "SpanContext",
    "SpanKind",
    "StatusCode",
    "get_tracer",
    "get_tracer_provider",
    "set_tracer_provider",
    "get_current_span",
]
