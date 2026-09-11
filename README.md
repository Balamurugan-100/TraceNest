# TraceNest

TraceNest is an OpenTelemetry-based observability SDK for synchronous Django applications. It creates a Django request waterfall without application-code changes, then exports standard OTLP traces and metrics to an OpenTelemetry Collector.

> [!NOTE]
> For a comprehensive technical deep-dive into TraceNest's internals, OTel defaults integration, PgBouncer pool topology, visual span icon system, and SQL/URL sanitization, read the [**TraceNest Architecture & Custom Enrichment Guide**](file:///Users/bala/workspace/datadog-replacement/sdk-v3/docs/ARCHITECTURE_AND_ENRICHMENT_GUIDE.md).

## Repository Structure

The repository is organized into two main subdirectories:

- **[`sdk/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/sdk)**: Contains the core `tracenest` Python SDK package source, package specifications (`pyproject.toml`), and SDK documentation.
- **[`harness/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/harness)**: Contains unit/integration test suites (`harness/tests`), sample Django applications (`sample-app` and `sample-django-lite-app`), Docker observability infrastructure configs (`docker/`), Grafana dashboards (`dashboards/`), docker-compose setup, and traffic generator scripts (`scripts/`).

---

The SDK is backend-neutral: use any OTLP-compatible collector and a trace backend such as Tempo to inspect individual request waterfalls.

## What Django instrumentation captures

One request produces a single trace with this hierarchy:

```text
django.request [SERVER]
└── django.middleware.SecurityMiddleware
    └── ... other configured middleware
        └── django.view.ProductTemplateView
            └── django.template: products/list.html
                └── included templates
```

The root request span includes the HTTP method, sanitized URL, low-cardinality route, status code, trace context, and errors. The SDK also records request count, error count, and request-duration metrics.

## Requirements and scope

- Python 3.8.18+
- Django 3.2+
- Synchronous Django / WSGI applications
- An OTLP HTTP collector endpoint (default: `http://localhost:4318`)

ASGI, async views, async middleware, and streaming-response instrumentation are not supported in this version. Function-based middleware is skipped; class-based middleware is instrumented.

## Install

From this repository:

```bash
python -m pip install -e .
```

For development and tests:

```bash
python -m pip install -e '.[dev]'
```

## Add the SDK to a Django app

### Option 1: Direct initialization in settings.py (recommended)

A single `init()` call handles everything — it auto-detects installed integrations (Django, PostgreSQL, Redis, HTTP client) and patches them automatically:

```python
# settings.py
import tracenest

tracenest.init(
    service="my-django-app",
    environment="production",
    endpoint="http://otel-collector:4318",
)
```

That's it. No `patch_all()` needed — `auto_patch=True` is the default.

### Option 2: Django middleware

Add the TraceNest middleware to your `MIDDLEWARE` list. It initializes the SDK on the first request:

```python
MIDDLEWARE = [
    "tracenest.integrations.django.TraceNestMiddleware",
    # ... your other middleware ...
]
```

Configuration comes from environment variables (`TRACENEST_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, etc.).

### Option 3: Environment variables only

The most minimal setup — just set environment variables and call `init()` with no arguments:

```python
import tracenest

tracenest.init()
```

### Option 4: Custom bootstrap function

For more control (error handling, conditional setup), create a bootstrap function:

```python
# config/otel.py
import os
import logging

logger = logging.getLogger("config.otel")


def setup_telemetry():
    if os.environ.get("TRACENEST_DISABLED", "").lower() in ("1", "true"):
        return
    try:
        import tracenest
    except ImportError:
        return
    try:
        tracenest.init(
            service=os.environ.get("TRACENEST_SERVICE_NAME", "my-app"),
            environment=os.environ.get("TRACENEST_ENVIRONMENT", "development"),
            endpoint=os.environ.get("TRACENEST_ENDPOINT", "http://otel-collector:4318"),
        )
        logger.info("TraceNest initialized")
    except Exception:
        logger.exception("TraceNest init failed")
```

Call `setup_telemetry()` from your `settings.py`.

## Configuration

Explicit `init()` arguments take precedence over environment variables, which take precedence over defaults.

| Setting | Environment variable | Default |
| --- | --- | --- |
| Service name | `TRACENEST_SERVICE_NAME` or `OTEL_SERVICE_NAME` | `unknown-service` |
| Environment | `TRACENEST_ENVIRONMENT` or `OTEL_ENVIRONMENT` | `development` |
| Version | `TRACENEST_VERSION` or `OTEL_SERVICE_VERSION` | `0.1.0` |
| OTLP base endpoint | `TRACENEST_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` |
| Trace endpoint | `TRACENEST_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Derived as `<endpoint>/v1/traces` |
| Metric endpoint | `TRACENEST_METRICS_ENDPOINT` or `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Derived as `<endpoint>/v1/metrics` |
| Trace sample rate | `TRACENEST_SAMPLE_RATE` or `OTEL_TRACES_SAMPLER_ARG` | `1.0` |
| Disable the SDK | `TRACENEST_DISABLED` or `OTEL_SDK_DISABLED` | `false` |
| Debug logging | `TRACENEST_DEBUG` or `OTEL_LOG_LEVEL=debug` | `false` |
| Auto-patch integrations | `auto_patch` kwarg | `true` |

When `auto_patch=True` (the default), all installed integrations are automatically detected and patched during `init()`. This includes Django, PostgreSQL, Redis, and HTTP client instrumentors. You can disable specific integrations via the `integrations` dict:

```python
tracenest.init(
    integrations={"redis": False, "requests": False},  # disable Redis and HTTP client tracing
)
```

Useful Django template options:

```python
tracenest.init(
    trace_nested_templates=True,  # default True: captures template includes
    template_enabled=True,
    template_exclude=["django/forms/*", "debug_toolbar/*", "*/widgets/*"],
)
```

## Trace and metric data

| Signal | Name | Purpose |
| --- | --- | --- |
| Trace | `django.request` | Root `SERVER` span for each HTTP request |
| Trace | `django.middleware.<name>` | Class-based middleware execution |
| Trace | `django.view.<name>` | Django/DRF view execution |
| Trace | `django.template: <name>` | Template and included-template rendering |
| Metric | `http.server.requests` | Request counter |
| Metric | `http.server.errors` | 5xx/error counter |
| Metric | `http.server.request.duration` | Request duration histogram, in seconds |

The SDK extracts an incoming W3C `traceparent` header, so a Django request continues an existing distributed trace. Traced responses include `X-Trace-ID` and `X-Span-ID` headers for correlation.

## Run the included demo

The repository includes a Django app and a local OpenTelemetry Collector, Tempo, Prometheus, and Grafana stack.

```bash
docker compose up --build
curl http://localhost:8001/api/products-tmpl/
```

Open Grafana at [http://localhost:3000](http://localhost:3000), then open the Tempo datasource or the Django dashboard. Search Tempo with:

```traceql
{ span.http.route = "/api/products-tmpl/" }
```

Select a `django.request` trace to open its full waterfall. Middleware timings are inclusive because Django middleware is nested: do not add their durations together.

## Verify the SDK

```bash
.venv/bin/python -m pytest -q
```

The Django tests cover parent-child span relationships, route normalization, errors, metrics, repeated instrumentation, and clean uninstrumentation.

## Current boundaries

This level is limited to the Django request-to-response lifecycle with full Grafana dashboards for Django. Database, Redis, and outbound HTTP instrumentation are implemented in the SDK and generate spans/metrics, but dedicated dashboards for these services are deferred beyond the current PoC scope. ASGI, async views, async middleware, Celery, and streaming-response instrumentation are not supported.
