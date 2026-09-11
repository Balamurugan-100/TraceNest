"""Unit tests for Redis Integration."""

import asyncio
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import tracenest
from tracenest.integrations.redis import RedisIntegration
from tracenest.integrations.redis.client import (
    _extract_redis_conn_meta,
    format_redis_statement,
    traced_async_pipeline_execute,
    traced_async_redis_execute_command,
    traced_pipeline_execute,
    traced_redis_execute_command,
)


class MockConnectionPool:
    def __init__(self, host="redis.internal", port=6380, db=2, path=None):
        self.connection_kwargs = {
            "host": host,
            "port": port,
            "db": db,
        }
        if path:
            self.connection_kwargs["path"] = path


class MockRedisClient:
    def __init__(self, host="redis.internal", port=6380, db=2, raise_exc=None):
        self.connection_pool = MockConnectionPool(host=host, port=port, db=db)
        self.raise_exc = raise_exc
        self.calls = []

    def execute_command(self, *args, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        self.calls.append((args, kwargs))
        return "OK"


class MockPipeline:
    def __init__(self, host="redis.internal", port=6380, db=2, transaction=True, raise_exc=None):
        self.connection_pool = MockConnectionPool(host=host, port=port, db=db)
        self.transaction = transaction
        self.raise_exc = raise_exc
        self.command_stack = [
            (("SET", "user:1", "alice"), {}),
            (("GET", "user:1"), {}),
            (("INCR", "counter"), {}),
        ]
        self.executed = False

    def execute(self, *args, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        self.executed = True
        return ["OK", "alice", 1]


class MockAsyncRedisClient:
    def __init__(self, host="redis.internal", port=6380, db=2, raise_exc=None):
        self.connection_pool = MockConnectionPool(host=host, port=port, db=db)
        self.raise_exc = raise_exc
        self.calls = []

    async def execute_command(self, *args, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        self.calls.append((args, kwargs))
        return "ASYNC_OK"


class MockAsyncPipeline:
    def __init__(self, host="redis.internal", port=6380, db=2, transaction=False, raise_exc=None):
        self.connection_pool = MockConnectionPool(host=host, port=port, db=db)
        self.transaction = transaction
        self.raise_exc = raise_exc
        self.command_stack = [
            (("HSET", "hash:1", "field", "val"), {}),
            (("HGET", "hash:1", "field"), {}),
        ]
        self.executed = False

    async def execute(self, *args, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        self.executed = True
        return [1, "val"]


@pytest.fixture(autouse=True)
def clean_sdk():
    tracenest._reset_for_testing()
    yield
    tracenest._reset_for_testing()


@pytest.fixture
def memory_exporter():
    exporter = InMemorySpanExporter()
    tracenest.init(
        service_name="test-redis-service",
        environment="test",
        exporter=exporter,
        export_batch=False,
    )
    return exporter


def test_format_redis_statement():
    op, stmt, summary = format_redis_statement(("GET", "my_key"))
    assert op == "GET"
    assert stmt == "GET my_key"
    assert summary == "GET my_key"

    op, stmt, summary = format_redis_statement((b"SET", b"session:99", b"active", 3600))
    assert op == "SET"
    assert stmt == "SET session:99 active 3600"
    assert summary == "SET session:99"

    # IP sanitization in DRF rate limit keys
    op, stmt, summary = format_redis_statement(("GET", ":1:throttle_burst_test_192.168.65.1"))
    assert op == "GET"
    assert stmt == "GET :1:throttle_burst_test_?"
    assert summary == "GET :1:throttle_burst_test_?"
    assert "192.168.65.1" not in stmt

    # AUTH sanitization
    op, stmt, summary = format_redis_statement(("AUTH", "supersecretpassword"))
    assert op == "AUTH"
    assert stmt == "AUTH ?"
    assert summary == "AUTH"

    op, stmt, summary = format_redis_statement(("AUTH", "user", "supersecretpassword"))
    assert op == "AUTH"
    assert stmt == "AUTH ? ?"
    assert summary == "AUTH"

    # CONFIG SET sanitization
    op, stmt, summary = format_redis_statement(("CONFIG", "SET", "requirepass", "secret123"))
    assert op == "CONFIG"
    assert stmt == "CONFIG SET ?"
    assert summary == "CONFIG SET"

    # Empty / Single op
    op, stmt, summary = format_redis_statement(("PING",))
    assert op == "PING"
    assert stmt == "PING"
    assert summary == "PING"

    op, stmt, summary = format_redis_statement(())
    assert op == "COMMAND"
    assert stmt == "COMMAND"


def test_extract_redis_conn_meta():
    client = MockRedisClient(host="custom.redis.lan", port=6399, db=5)
    host, port, db_index, peer_service = _extract_redis_conn_meta(client)
    assert host == "custom.redis.lan"
    assert port == 6399
    assert db_index == 5
    assert peer_service == "custom.redis.lan"

    # Unix socket fallback
    client_unix = MockRedisClient()
    client_unix.connection_pool.connection_kwargs = {"path": "/var/run/redis.sock", "db": 0}
    host, port, db_index, peer_service = _extract_redis_conn_meta(client_unix)
    assert host == "/var/run/redis.sock"
    assert db_index == 0
    assert peer_service == "/var/run/redis.sock"


def test_traced_redis_execute_command_success(memory_exporter):
    client = MockRedisClient(host="cache.example.com", port=6379, db=1)

    res = traced_redis_execute_command(
        client.execute_command,
        client,
        ("SET", ":1:throttle_burst_test_192.168.65.1", "payload_data"),
        {},
    )
    assert res == "OK"

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    # Datadog parity: span.name is command verb "SET", full statement in db.statement
    assert span.name == "🔴 SET"
    assert span.kind == SpanKind.CLIENT
    assert span.status.status_code == StatusCode.OK

    attrs = span.attributes
    assert attrs["db.system"] == "redis"
    assert attrs["peer.service"] == "cache.example.com"
    assert attrs["db.name"] == "1"
    assert attrs["db.instance"] == "1"
    assert attrs["db.redis.database_index"] == 1
    assert attrs["net.peer.name"] == "cache.example.com"
    assert attrs["net.peer.port"] == 6379
    assert attrs["server.address"] == "cache.example.com"
    assert attrs["server.port"] == 6379
    assert attrs["db.operation"] == "SET"
    assert attrs["db.operation.name"] == "SET"
    assert attrs["db.statement"] == "SET :1:throttle_burst_test_? payload_data"
    assert attrs["db.query.summary"] == "SET :1:throttle_burst_test_?"
    assert attrs["db.redis.args_count"] == 3


def test_traced_redis_execute_command_error(memory_exporter):
    class CustomRedisError(Exception):
        pass

    client = MockRedisClient(raise_exc=CustomRedisError("Connection refused"))

    with pytest.raises(CustomRedisError):
        traced_redis_execute_command(
            client.execute_command,
            client,
            ("GET", "bad_key"),
            {},
        )

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error"] is True
    assert span.attributes["error.type"] == "CustomRedisError"
    assert len(span.events) >= 1
    assert span.events[0].name == "exception"


def test_traced_pipeline_execute(memory_exporter):
    pipe = MockPipeline(host="redis.cluster", port=7000, db=0, transaction=True)

    res = traced_pipeline_execute(
        pipe.execute,
        pipe,
        (),
        {},
    )
    assert res == ["OK", "alice", 1]

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert "MULTI/EXEC (3 commands)" in span.name
    assert span.kind == SpanKind.CLIENT
    assert span.status.status_code == StatusCode.OK

    attrs = span.attributes
    assert attrs["db.system"] == "redis"
    assert attrs["peer.service"] == "redis.cluster"
    assert attrs["db.operation"] == "MULTI/EXEC"
    assert attrs["db.redis.pipeline_length"] == 3
    assert attrs["server.address"] == "redis.cluster"
    assert attrs["server.port"] == 7000


def test_traced_async_redis(memory_exporter):
    client = MockAsyncRedisClient(host="async.redis.io", port=6379, db=3)

    res = asyncio.run(
        traced_async_redis_execute_command(
            client.execute_command,
            client,
            ("HGET", "user:profile", "email"),
            {},
        )
    )
    assert res == "ASYNC_OK"

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.name == "🔴 HGET"
    assert span.attributes["db.operation"] == "HGET"
    assert span.attributes["db.name"] == "3"
    assert span.attributes["peer.service"] == "async.redis.io"


def test_traced_async_pipeline(memory_exporter):
    pipe = MockAsyncPipeline(host="async.redis.io", port=6379, db=0, transaction=False)

    res = asyncio.run(
        traced_async_pipeline_execute(
            pipe.execute,
            pipe,
            (),
            {},
        )
    )
    assert res == [1, "val"]

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert "PIPELINE (2 commands)" in span.name
    assert span.attributes["db.operation"] == "PIPELINE"
    assert span.attributes["db.redis.pipeline_length"] == 2


def test_reentrancy_guard(memory_exporter):
    client = MockRedisClient()

    def inner_call(*args, **kwargs):
        return traced_redis_execute_command(
            lambda *a, **k: "INNER_DONE",
            client,
            ("GET", "internal_key"),
            {},
        )

    res = traced_redis_execute_command(
        inner_call,
        client,
        ("SET", "outer_key", "val"),
        {},
    )
    assert res == "INNER_DONE"

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["db.operation"] == "SET"


def test_redis_integration_manager_registration():
    from tracenest.integrations import get_integration_manager

    mgr = get_integration_manager()
    integ = mgr._registered_classes["redis"]
    assert integ == "tracenest.integrations.redis.RedisIntegration"
    resolved = mgr._resolve_class(integ)
    assert resolved is RedisIntegration


def test_django_redis_cache_tracing(memory_exporter):
    """Verify that django_redis cache backends emit django_redis.cache.<op> spans."""
    from tracenest.integrations.django.cache import make_traced_cache_op

    class FakeRedisCache:
        __module__ = "django_redis.cache"

        def get(self, key):
            return "cached_tenant"

    cache = FakeRedisCache()
    traced_get = make_traced_cache_op("get")

    result = traced_get(lambda k: cache.get(k), cache, ("tenant:subdomain.com",), {})
    assert result == "cached_tenant"

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    # Datadog Parity check: span name must be django_redis.cache.get
    assert span.name == "🔴 django_redis.cache.get"
    assert span.attributes["django.cache.operation"] == "get"
    assert span.attributes["django.cache.key"] == "tenant:subdomain.com"
    assert span.attributes["django.cache.backend"] == "FakeRedisCache"
    assert span.attributes["django.cache.hit"] is True

