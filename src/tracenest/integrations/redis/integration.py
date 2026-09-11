"""RedisIntegration — leverages OpenTelemetry redis instrumentor for sync and async clients."""

import importlib
import logging

from tracenest.integrations.base import BaseIntegration

logger = logging.getLogger("tracenest.integrations.redis")


class RedisIntegration(BaseIntegration):
    """Deep Redis command and pipeline tracing for redis-py and asyncio clients."""

    name = "redis"

    def is_installed(self) -> bool:
        try:
            importlib.import_module("redis")
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor

            instrumentor = RedisInstrumentor()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument()
            self._is_patched = True
        except Exception as exc:
            logger.debug("RedisInstrumentor patch skipped: %s", exc)

    def uninstrument(self) -> bool:
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor

            instrumentor = RedisInstrumentor()
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()
            self._is_patched = False
            return True
        except Exception as exc:
            logger.debug("Failed to uninstrument redis: %s", exc)
            return False

