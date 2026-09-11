"""Tests for Boto / S3 / AWS SDK integration."""

from unittest.mock import MagicMock, patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import tracenest
from tracenest.integrations.boto import BotoIntegration
from tracenest.integrations.manager import get_integration_manager


def setup_function():
    tracenest._reset_for_testing()


def teardown_function():
    tracenest._reset_for_testing()


def test_boto_integration_discovery():
    """Verify BotoIntegration is registered and auto-detected."""
    mgr = get_integration_manager()
    mgr.apply_integrations()
    boto_inst = mgr.get("boto")
    assert boto_inst is not None
    assert isinstance(boto_inst, BotoIntegration)
    assert boto_inst.is_installed() is True

    # Test aliases
    assert mgr.get("boto3") is boto_inst
    assert mgr.get("botocore") is boto_inst
    assert mgr.get("aws") is boto_inst
    assert mgr.get("s3") is boto_inst


def test_boto_integration_instrument_lifecycle():
    """Verify BotoIntegration instrument and uninstrument calls."""
    exporter = InMemorySpanExporter()
    tracenest.init(
        service="boto-test-service",
        exporter=exporter,
        export_batch=False,
    )

    mgr = get_integration_manager()
    boto_inst = mgr.get("boto")

    assert boto_inst._instrumented is True

    # Uninstrument cleanly
    boto_inst.uninstrument()
    assert boto_inst._instrumented is False

    # Re-instrument
    boto_inst.instrument()
    assert boto_inst._instrumented is True
