"""Django middleware for automatic TraceNest initialization.

Add this to your Django settings.py MIDDLEWARE list to auto-initialize TraceNest:

    MIDDLEWARE = [
        "tracenest.integrations.django.TraceNestMiddleware",
        # ... your other middleware ...
    ]

The middleware reads configuration from environment variables:
    TRACENEST_SERVICE_NAME / OTEL_SERVICE_NAME — service name
    TRACENEST_ENVIRONMENT / OTEL_ENVIRONMENT — deployment environment
    TRACENEST_ENDPOINT / OTEL_EXPORTER_OTLP_ENDPOINT — OTLP endpoint

Or set TRACENEST_DISABLED=1 to disable tracing entirely.

This is an alternative to calling tracenest.init() directly in settings.py.
"""
import logging
import os

logger = logging.getLogger("tracenest.integrations.django")

_initialized = False


class TraceNestMiddleware:
    """Django middleware that initializes TraceNest on first request.

    This middleware is intentionally lightweight — it only runs the SDK
    initialization once (on the very first request) and then becomes a
    pass-through for all subsequent requests.

    Usage in settings.py:
        MIDDLEWARE = [
            "tracenest.integrations.django.TraceNestMiddleware",
            # ... other middleware ...
        ]

    Environment variables:
        TRACENEST_DISABLED / OTEL_SDK_DISABLED — set to "1" to disable
        TRACENEST_SERVICE_NAME / OTEL_SERVICE_NAME — service name
        TRACENEST_ENVIRONMENT / OTEL_ENVIRONMENT — deployment environment
        TRACENEST_ENDPOINT / OTEL_EXPORTER_OTLP_ENDPOINT — OTLP collector endpoint
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._ensure_initialized()

    def _ensure_initialized(self):
        global _initialized
        if _initialized:
            return

        # Check if disabled
        if os.environ.get("TRACENEST_DISABLED", os.environ.get("OTEL_SDK_DISABLED", "")).lower() in ("1", "true", "yes"):
            logger.debug("TraceNest disabled via environment variable")
            _initialized = True
            return

        try:
            import tracenest
        except ImportError:
            logger.debug("TraceNest not installed; skipping initialization")
            _initialized = True
            return

        try:
            tracenest.init(
                service=os.environ.get("TRACENEST_SERVICE_NAME") or os.environ.get("OTEL_SERVICE_NAME"),
                environment=os.environ.get("TRACENEST_ENVIRONMENT") or os.environ.get("OTEL_ENVIRONMENT"),
                endpoint=os.environ.get("TRACENEST_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
                # auto_patch=True is the default — all installed integrations
                # (Django, PostgreSQL, Redis, requests) are auto-detected
            )
            logger.info("TraceNest initialized via Django middleware")
        except Exception as exc:
            logger.warning("TraceNest initialization failed: %s", exc)

        _initialized = True

    def __call__(self, request):
        # No-op after initialization — zero overhead on subsequent requests
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Ensure exception tracing is captured even if middleware init fails."""
        return None
