"""
tp_obs_v3: Python Observability SDK for OpenTelemetry.

Provides complete waterfall tracing, auto-instrumentation, and drop-in ddtrace replacement.
"""

import atexit
import logging
import threading
from typing import Any, Dict, Optional, Union

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanProcessor,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap

from tp_obs_v3.config import SDKConfig
from tp_obs_v3.exports import get_current_span, get_tracer
from tp_obs_v3.sanitize import sanitize_sql, sanitize_url
from tp_obs_v3.version import __version__

logger = logging.getLogger("tp_obs_v3")

_INITIALIZED = False
_INIT_LOCK = threading.Lock()
_ACTIVE_PROVIDER: Optional[TracerProvider] = None
_ACTIVE_CONFIG: Optional[SDKConfig] = None


def init(
    service: Optional[str] = None,
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    version: Optional[str] = None,
    endpoint: Optional[str] = None,
    traces_endpoint: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    sample_rate: Optional[float] = None,
    disabled: Optional[bool] = None,
    debug: Optional[bool] = None,
    resource_attributes: Optional[Dict[str, Any]] = None,
    integrations: Optional[Dict[str, bool]] = None,
    exporter: Optional[SpanExporter] = None,
    span_processor: Optional[SpanProcessor] = None,
    export_batch: bool = True,
    **kwargs: Any,
) -> TracerProvider:
    """
    Initialize the tp_obs_v3 OpenTelemetry SDK.

    Configures the global TracerProvider, W3C context propagation,
    and OTLP HTTP span exporter. Thread-safe and idempotent.
    """
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_CONFIG

    with _INIT_LOCK:
        if _INITIALIZED and _ACTIVE_PROVIDER is not None:
            logger.debug("tp_obs_v3 is already initialized. Returning existing provider.")
            return _ACTIVE_PROVIDER

        config = SDKConfig.from_env_and_kwargs(
            service=service,
            service_name=service_name,
            environment=environment,
            version=version,
            endpoint=endpoint,
            traces_endpoint=traces_endpoint,
            headers=headers,
            sample_rate=sample_rate,
            disabled=disabled,
            debug=debug,
            resource_attributes=resource_attributes,
            integrations=integrations,
            **kwargs,
        )
        _ACTIVE_CONFIG = config

        if config.debug:
            logging.basicConfig(level=logging.DEBUG)
            logger.setLevel(logging.DEBUG)

        # Build Resource attributes
        resource_data = {
            "service.name": config.service_name,
            "deployment.environment": config.environment,
            "service.version": config.version,
            "telemetry.sdk.name": "tp_obs_v3",
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": __version__,
        }
        if config.resource_attributes:
            resource_data.update(config.resource_attributes)
        resource = Resource.create(resource_data)

        # Configure Sampler
        if config.disabled or config.sample_rate <= 0.0:
            sampler = ALWAYS_OFF
        elif config.sample_rate >= 1.0:
            sampler = ALWAYS_ON
        else:
            sampler = ParentBased(root=TraceIdRatioBased(config.sample_rate))

        # Create TracerProvider
        provider = TracerProvider(resource=resource, sampler=sampler)

        # Configure Exporter and Processor if not disabled
        if not config.disabled:
            if exporter is None:
                # Target endpoint: default to endpoint/v1/traces unless specific traces_endpoint is set
                otlp_endpoint = config.traces_endpoint or f"{config.endpoint.rstrip('/')}/v1/traces"
                exporter = OTLPSpanExporter(
                    endpoint=otlp_endpoint,
                    headers=config.headers or None,
                )

            if span_processor is None:
                if export_batch:
                    span_processor = BatchSpanProcessor(exporter)
                else:
                    span_processor = SimpleSpanProcessor(exporter)

            provider.add_span_processor(span_processor)

        # Set as global tracer provider
        trace.set_tracer_provider(provider)
        _ACTIVE_PROVIDER = provider
        _INITIALIZED = True

        # Setup W3C Trace Context propagator
        set_global_textmap(TraceContextTextMapPropagator())

        # Register shutdown on process exit
        atexit.register(provider.shutdown)

        logger.info(
            "tp_obs_v3 initialized successfully (service=%s, env=%s, endpoint=%s)",
            config.service_name,
            config.environment,
            config.endpoint,
        )

        return provider


def get_config() -> Optional[SDKConfig]:
    """Return the active SDKConfig or None if not initialized."""
    return _ACTIVE_CONFIG


def patch_all(**kwargs: Any) -> None:
    """
    Auto-discover and patch all supported integrations.
    Full integration registry will be applied in Phase 2.
    """
    logger.info("tp_obs_v3 patch_all called")


def _reset_for_testing() -> None:
    """Internal helper to reset singleton state between unit tests."""
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_CONFIG
    with _INIT_LOCK:
        if _ACTIVE_PROVIDER is not None:
            try:
                _ACTIVE_PROVIDER.shutdown()
            except Exception:
                pass
        _INITIALIZED = False
        _ACTIVE_PROVIDER = None
        _ACTIVE_CONFIG = None
        # Reset OpenTelemetry global state
        trace._TRACER_PROVIDER = None
        if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
            trace._TRACER_PROVIDER_SET_ONCE._done = False


__all__ = [
    "init",
    "patch_all",
    "get_config",
    "get_tracer",
    "get_current_span",
    "SDKConfig",
    "sanitize_url",
    "sanitize_sql",
    "__version__",
]
