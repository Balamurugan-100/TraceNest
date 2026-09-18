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
        from .client import (
            traced_redis_execute_command,
            traced_pipeline_execute,
            traced_async_redis_execute_command,
            traced_async_pipeline_execute,
        )

        patched_any = False

        # 1. Sync Redis client & pipeline
        try:
            import redis

            self.wrap("redis.Redis", "execute_command", traced_redis_execute_command)
            if hasattr(redis, "StrictRedis") and redis.StrictRedis is not redis.Redis:
                self.wrap("redis.StrictRedis", "execute_command", traced_redis_execute_command)
            self.wrap("redis.client.Pipeline", "execute", traced_pipeline_execute)
            patched_any = True
        except Exception as exc:
            logger.debug("Redis sync patch skipped: %s", exc)

        # 2. Async Redis client & pipeline
        try:
            import redis.asyncio

            self.wrap("redis.asyncio.Redis", "execute_command", traced_async_redis_execute_command)
            if hasattr(redis.asyncio, "StrictRedis") and redis.asyncio.StrictRedis is not redis.asyncio.Redis:
                self.wrap("redis.asyncio.StrictRedis", "execute_command", traced_async_redis_execute_command)
            self.wrap("redis.asyncio.client.Pipeline", "execute", traced_async_pipeline_execute)
            patched_any = True
        except Exception as exc:
            logger.debug("Redis async patch skipped: %s", exc)

        self._is_patched = patched_any

    def uninstrument(self) -> bool:
        self.unwrap_all()
        self._is_patched = False
        return True

