"""Unit tests for Phase 1: SDK scaffold, config, and OTel bootstrap."""

import os
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

import tp_obs_v3
from tp_obs_v3.config import SDKConfig
from tp_obs_v3.sanitize import sanitize_sql, sanitize_url


@pytest.fixture(autouse=True)
def clean_sdk_state():
    """Ensure clean SDK state before and after each test."""
    tp_obs_v3._reset_for_testing()
    yield
    tp_obs_v3._reset_for_testing()


def test_init_creates_tracer_provider():
    """Verify init() sets up TracerProvider with correct Resource attributes."""
    exporter = InMemorySpanExporter()
    provider = tp_obs_v3.init(
        service="test-service",
        environment="staging",
        version="1.2.3",
        resource_attributes={"custom.tag": "value123"},
        exporter=exporter,
        export_batch=False,
    )

    assert provider is not None
    assert trace.get_tracer_provider() == provider

    resource_attrs = provider.resource.attributes
    assert resource_attrs["service.name"] == "test-service"
    assert resource_attrs["deployment.environment"] == "staging"
    assert resource_attrs["service.version"] == "1.2.3"
    assert resource_attrs["telemetry.sdk.name"] == "tp_obs_v3"
    assert resource_attrs["telemetry.sdk.language"] == "python"
    assert resource_attrs["custom.tag"] == "value123"


def test_init_idempotency():
    """Verify that calling init() multiple times returns the same provider."""
    provider1 = tp_obs_v3.init(service="first-service")
    provider2 = tp_obs_v3.init(service="second-service")
    assert provider1 is provider2


def test_config_from_env_and_kwargs(monkeypatch):
    """Verify SDKConfig properly resolves env vars and kwargs overrides."""
    monkeypatch.setenv("OTEL_SERVICE_NAME", "env-service")
    monkeypatch.setenv("OTEL_ENVIRONMENT", "production")
    monkeypatch.setenv("OTEL_SERVICE_VERSION", "2.0.0")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "api-key=secret123,team=infra")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.5")

    # When kwargs are omitted, env vars are used
    cfg1 = SDKConfig.from_env_and_kwargs()
    assert cfg1.service_name == "env-service"
    assert cfg1.environment == "production"
    assert cfg1.version == "2.0.0"
    assert cfg1.endpoint == "http://otel-collector:4318"
    assert cfg1.headers == {"api-key": "secret123", "team": "infra"}
    assert cfg1.sample_rate == 0.5

    # Kwargs override env vars
    cfg2 = SDKConfig.from_env_and_kwargs(
        service="override-service",
        environment="dev",
        sample_rate=0.8,
        headers={"team": "product"},
    )
    assert cfg2.service_name == "override-service"
    assert cfg2.environment == "dev"
    assert cfg2.sample_rate == 0.8
    assert cfg2.headers["team"] == "product"
    assert cfg2.headers["api-key"] == "secret123"


def test_disabled_mode():
    """Verify that disabled mode prevents spans from being recorded."""
    exporter = InMemorySpanExporter()
    provider = tp_obs_v3.init(
        service="disabled-service",
        disabled=True,
        exporter=exporter,
        export_batch=False,
    )
    tracer = trace.get_tracer("disabled-test")
    with tracer.start_as_current_span("test-span") as span:
        span.set_attribute("key", "val")

    spans = exporter.get_finished_spans()
    assert len(spans) == 0


def test_span_creation_and_export():
    """Verify spans are created, executed within context, and exported correctly."""
    exporter = InMemorySpanExporter()
    tp_obs_v3.init(
        service="span-test-service",
        exporter=exporter,
        export_batch=False,
    )

    tracer = tp_obs_v3.get_tracer("test-tracer")
    with tracer.start_as_current_span("parent-operation") as parent:
        parent.set_attribute("parent.attr", "parent_val")
        current_span = tp_obs_v3.get_current_span()
        assert current_span == parent

        with tracer.start_as_current_span("child-operation") as child:
            child.set_attribute("child.attr", "child_val")
            assert tp_obs_v3.get_current_span() == child

    spans = exporter.get_finished_spans()
    assert len(spans) == 2

    child_span = spans[0]
    parent_span = spans[1]

    assert child_span.name == "child-operation"
    assert child_span.attributes["child.attr"] == "child_val"
    assert child_span.parent.span_id == parent_span.context.span_id

    assert parent_span.name == "parent-operation"
    assert parent_span.attributes["parent.attr"] == "parent_val"


def test_sanitize_url():
    """Verify URL credentials and fragments are removed properly."""
    # Basic URL
    assert sanitize_url("http://example.com/path") == "http://example.com/path"

    # URL with username and password
    url_with_auth = "https://user:secretpass@api.external.com:8080/v1/data?query=1#top"
    cleaned = sanitize_url(url_with_auth)
    assert "secretpass" not in cleaned
    assert "user" not in cleaned
    assert "#top" not in cleaned
    assert cleaned == "https://api.external.com:8080/v1/data?query=1"

    # Strip query option
    cleaned_no_query = sanitize_url(url_with_auth, strip_query=True)
    assert cleaned_no_query == "https://api.external.com:8080/v1/data"

    # None and empty
    assert sanitize_url(None) == ""
    assert sanitize_url("") == ""


def test_sanitize_sql():
    """Verify SQL strings and numbers are parameterized and whitespace is normalized."""
    # Strings and numbers replacement
    raw_query = "SELECT * FROM users WHERE id = 42 AND email = 'alice@example.com' AND active = 1"
    expected = "SELECT * FROM users WHERE id = ? AND email = ? AND active = ?"
    assert sanitize_sql(raw_query) == expected

    # Multiple whitespace and newlines
    multiline_query = """
        SELECT  id,   name
        FROM    products
        WHERE   price > 99.99
    """
    assert sanitize_sql(multiline_query) == "SELECT id, name FROM products WHERE price > ?"

    # Preserves table/column names that contain numbers or words
    col_query = "SELECT user_v2_id FROM table1 WHERE status = 'ACTIVE'"
    assert sanitize_sql(col_query) == "SELECT user_v2_id FROM table1 WHERE status = ?"

    # Truncation
    long_query = "SELECT " + ("a" * 5000)
    sanitized_long = sanitize_sql(long_query, max_length=100)
    assert len(sanitized_long) > 100
    assert sanitized_long.endswith("... [truncated]")

    # None and empty
    assert sanitize_sql(None) == ""
    assert sanitize_sql("") == ""
