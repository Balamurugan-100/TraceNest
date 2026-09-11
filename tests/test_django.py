"""Unit tests for Phase 3: Django Integration (Full Waterfall)."""

import os
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import django
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.template import Template, Context
from django.test import RequestFactory
from django.urls import path

# Configure minimal Django settings for testing if not already configured
if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="test-secret-key-12345",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.middleware.common.CommonMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
            }
        ],
    )
    django.setup()

import tracenest
from tracenest.config import SDKConfig
from tracenest.integrations.django import DjangoIntegration


def sample_view(request):
    return HttpResponse("Hello from Django View", status=200)


def sample_template_view(request):
    t = Template("<h1>Hello, {{ name }}!</h1>")
    t.name = "sample_hello.html"
    rendered = t.render(Context({"name": "World"}))
    return HttpResponse(rendered, status=200)


def sample_error_view(request):
    return HttpResponse("Internal Server Error", status=500)


def sample_throttled_view(request):
    return JsonResponse({"detail": "Request was throttled. Expected available in 55 seconds."}, status=429)


class MockViewSet:
    """Simulates a Django REST Framework ViewSet."""
    pass


def create_drf_view_func(viewset_cls, action_name):
    def view_func(request, *args, **kwargs):
        return JsonResponse({"status": "ok"})
    view_func.cls = viewset_cls
    view_func.actions = {"get": action_name}
    return view_func


urlpatterns = [
    path("test/sample/", sample_view, name="sample-view"),
    path("test/template/", sample_template_view, name="template-view"),
    path("test/error/", sample_error_view, name="error-view"),
    path("test/throttled/", sample_throttled_view, name="throttled-view"),
]


@pytest.fixture(autouse=True)
def clean_sdk_and_django():
    tracenest._reset_for_testing()
    integration = DjangoIntegration()
    integration.instrument()
    yield
    integration.uninstrument()
    tracenest._reset_for_testing()


def test_request_span_created():
    """Verify standard HTTP request creates a SERVER root span with HTTP attributes."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="django-test-svc", exporter=exporter, export_batch=False)

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/sample/")

    response = handler.get_response(request)
    assert response.status_code == 200
    assert "X-Trace-ID" in response
    assert "X-Span-ID" in response

    spans = exporter.get_finished_spans()
    request_spans = [s for s in spans if s.name == "django.request"]
    assert len(request_spans) == 1

    req_span = request_spans[0]
    assert req_span.kind == SpanKind.SERVER
    # Stable semconv (old http.method/http.target removed)
    assert req_span.attributes["http.request.method"] == "GET"
    assert req_span.attributes["http.response.status_code"] == 200
    assert req_span.attributes["url.path"] == "/test/sample/"
    assert req_span.attributes["http.route"] == "/test/sample/"
    assert req_span.status.status_code == StatusCode.OK


def test_view_span_resolves_name():
    """Verify internal view execution generates a django.view child span."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="django-test-svc", exporter=exporter, export_batch=False)

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/sample/")

    response = handler.get_response(request)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    view_spans = [s for s in spans if "django.view" in s.name]
    assert len(view_spans) >= 1
    view_span = view_spans[0]
    assert view_span.kind == SpanKind.INTERNAL
    assert view_span.attributes["django.view"] == "sample_view"


def test_template_span_created():
    """Verify template rendering generates a django.template child span."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="django-test-svc", exporter=exporter, export_batch=False)

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/template/")

    response = handler.get_response(request)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    template_spans = [s for s in spans if "django.template" in s.name]
    assert len(template_spans) == 1
    t_span = template_spans[0]
    assert t_span.kind == SpanKind.INTERNAL
    assert t_span.attributes["django.template.name"] == "sample_hello.html"


def test_error_500_marks_span():
    """Verify 500 responses mark request spans as ERROR."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="django-test-svc", exporter=exporter, export_batch=False)

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/error/")

    response = handler.get_response(request)
    assert response.status_code == 500

    spans = exporter.get_finished_spans()
    req_span = next(s for s in spans if s.name == "django.request")
    assert req_span.attributes["http.response.status_code"] == 500
    assert req_span.attributes["error"] is True
    assert req_span.status.status_code == StatusCode.ERROR


def test_w3c_traceparent_propagation():
    """Verify incoming W3C traceparent headers link the root request span to the distributed trace."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="django-test-svc", exporter=exporter, export_batch=False)

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    parent_span_id = "00f067aa0ba902b7"
    traceparent = f"00-{trace_id}-{parent_span_id}-01"

    request = factory.get("/test/sample/", HTTP_TRACEPARENT=traceparent)
    response = handler.get_response(request)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    req_span = next(s for s in spans if s.name == "django.request")

    assert format(req_span.context.trace_id, "032x") == trace_id
    assert format(req_span.parent.span_id, "016x") == parent_span_id


def test_throttled_429_marks_span():
    """Verify 429 Too Many Requests sets status code correctly without 5xx error flag."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="django-test-svc", exporter=exporter, export_batch=False)

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/throttled/")

    response = handler.get_response(request)
    assert response.status_code == 429

    spans = exporter.get_finished_spans()
    req_span = next(s for s in spans if s.name == "django.request")
    assert req_span.attributes["http.response.status_code"] == 429
    assert req_span.attributes["error"] is False
    assert req_span.status.status_code == StatusCode.OK


def test_static_tags_applied_to_request_span():
    """Verify static tags from SDKConfig.tags are applied to request spans."""
    exporter = InMemorySpanExporter()
    tracenest.init(
        service="django-test-svc",
        exporter=exporter,
        export_batch=False,
        tags={"org": "testpress", "institute": "karunya", "subdomain": "lms"},
    )

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/sample/")
    response = handler.get_response(request)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    req_span = next(s for s in spans if s.name == "django.request")
    assert req_span.attributes["org"] == "testpress"
    assert req_span.attributes["institute"] == "karunya"
    assert req_span.attributes["subdomain"] == "lms"


def test_static_tags_applied_to_view_span():
    """Verify static tags from SDKConfig.tags are applied to view spans."""
    exporter = InMemorySpanExporter()
    tracenest.init(
        service="django-test-svc",
        exporter=exporter,
        export_batch=False,
        tags={"org": "testpress", "institute": "karunya"},
    )

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/sample/")
    response = handler.get_response(request)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    view_spans = [s for s in spans if "django.view" in s.name]
    assert len(view_spans) >= 1
    view_span = view_spans[0]
    assert view_span.attributes["org"] == "testpress"
    assert view_span.attributes["institute"] == "karunya"


def test_on_request_span_callback():
    """Verify on_request_span callback is invoked with span and request."""
    callback_invoked = []

    def my_callback(span, request):
        callback_invoked.append(True)
        span.set_attribute("custom.callback", True)
        span.set_attribute("custom.request_path", getattr(request, "path", ""))

    exporter = InMemorySpanExporter()
    tracenest.init(
        service="django-test-svc",
        exporter=exporter,
        export_batch=False,
        tags={"org": "testpress"},
        on_request_span=my_callback,
    )

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/sample/")
    response = handler.get_response(request)
    assert response.status_code == 200
    assert len(callback_invoked) == 1

    spans = exporter.get_finished_spans()
    req_span = next(s for s in spans if s.name == "django.request")
    assert req_span.attributes["org"] == "testpress"
    assert req_span.attributes["custom.callback"] is True
    assert req_span.attributes["custom.request_path"] == "/test/sample/"


def test_on_request_span_callback_exception_silenced():
    """Verify on_request_span callback exceptions don't crash the request."""
    def bad_callback(span, request):
        raise RuntimeError("boom")

    exporter = InMemorySpanExporter()
    tracenest.init(
        service="django-test-svc",
        exporter=exporter,
        export_batch=False,
        on_request_span=bad_callback,
    )

    from django.core.handlers.wsgi import WSGIHandler
    handler = WSGIHandler()
    handler.load_middleware()

    factory = RequestFactory()
    request = factory.get("/test/sample/")
    response = handler.get_response(request)
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    req_span = next(s for s in spans if s.name == "django.request")
    assert req_span.attributes["http.response.status_code"] == 200


def test_tags_from_env_var(monkeypatch):
    """Verify tags can be set via TRACENEST_TAGS env var."""
    monkeypatch.setenv("TRACENEST_TAGS", "org=testpress,institute=karunya")
    cfg = SDKConfig.from_env_and_kwargs()
    assert cfg.tags == {"org": "testpress", "institute": "karunya"}
    monkeypatch.delenv("TRACENEST_TAGS")


def test_tags_kwargs_override_env(monkeypatch):
    """Verify kwargs tags override env var tags."""
    monkeypatch.setenv("TRACENEST_TAGS", "org=env_org,env_key=env_val")
    cfg = SDKConfig.from_env_and_kwargs(tags={"org": "kwarg_org", "kwarg_key": "kwarg_val"})
    assert cfg.tags == {"org": "kwarg_org", "kwarg_key": "kwarg_val"}
    monkeypatch.delenv("TRACENEST_TAGS")

