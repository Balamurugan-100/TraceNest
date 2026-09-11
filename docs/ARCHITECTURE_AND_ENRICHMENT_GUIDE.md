# TraceNest SDK v3 — Comprehensive Architecture & Custom Enrichment Guide

This document is the definitive technical reference for **TraceNest SDK v3**. It details how TraceNest initializes OpenTelemetry defaults, where standard OTel instrumentors are leveraged, and how TraceNest layers custom APM enrichments to deliver Datadog-grade visual telemetry, PgBouncer pool topology, Django middleware waterfalls, SQL/URL sanitization, and self-tracing protection.

---

## Table of Contents

1. [Architectural Principles & Philosophy](#1-architectural-principles--philosophy)
2. [SDK Lifecycle & Bootstrap](#2-sdk-lifecycle--bootstrap)
3. [Standard OTel Defaults vs. TraceNest Custom Enrichments](#3-standard-otel-defaults-vs-tracenest-custom-enrichments)
4. [Integration Technical Deep-Dives](#4-integration-technical-deep-dives)
   - [Django Waterfall & Component Instrumentation](#a-django-waterfall--component-instrumentation)
   - [PostgreSQL & PgBouncer Topology](#b-postgresql--pgbouncer-topology)
   - [Redis Command & Pipeline Tracing](#c-redis-command--pipeline-tracing)
   - [Outgoing HTTP Requests Tracing](#d-outgoing-http-requests-tracing)
5. [Visual Icon & Span Naming System](#5-visual-icon--span-naming-system)
6. [OTLP Self-Tracing Prevention Guard](#6-otlp-self-tracing-prevention-guard)
7. [OpenTelemetry Collector & Metrics Pipeline](#7-opentelemetry-collector--metrics-pipeline)
8. [Testing & Verification Architecture](#8-testing--verification-architecture)

---

## 1. Architectural Principles & Philosophy

TraceNest SDK v3 is engineered to resolve a core trade-off in modern cloud observability: **OpenTelemetry standards compliance vs. Datadog-grade APM user experience**.

```text
               ┌─────────────────────────────────────────────────────────┐
               │                     TraceNest SDK v3                    │
               │                                                         │
               │  ┌───────────────────────┐   ┌───────────────────────┐  │
               │  │  Standard OTel APIs   │   │ TraceNest APM Hooks   │  │
               │  │  (W3C, Spans, OTLP)   │   │  (Waterfalls, Icons)  │  │
               │  └───────────┬───────────┘   └───────────┬───────────┘  │
               └──────────────┼───────────────────────────┼──────────────┘
                              │                           │
                              ▼                           ▼
               ┌─────────────────────────────────────────────────────────┐
               │              OpenTelemetry Collector                    │
               │        (SpanMetrics, PgBouncer Metrics)                 │
               └──────────────┬───────────────────────────┬──────────────┘
                              │                           │
                              ▼                           ▼
                     ┌──────────────────┐       ┌──────────────────┐
                     │   Grafana Tempo  │       │     SigNoz UI    │
                     └──────────────────┘       └──────────────────┘
```

### Key Design Directives
1. **Backend Independence**: TraceNest exports standard OpenTelemetry OTLP payloads (`opentelemetry-exporter-otlp-proto-http`). It contains zero vendor-specific API dependencies.
2. **Single Canonical Span Ownership**: Eliminates duplicate spans (such as nested driver-level `SELECT` spans inside Django database queries).
3. **Instant Visual Scannability**: Prefixes span names with curated emoji badges (`🔵`, `🐘`, `🔴`, `🌐`, `⚙️`, `🐍`, `🎨`, `🔐`) and displays full SQL statements in waterfall trees.
4. **Product-Specific Topology**: Detects PgBouncer connection pools (`port 6432` / host `pgbouncer`) and flags primary vs. read-replica database connections (`slave1`, `slave2`, `slave3`).

---

## 2. SDK Lifecycle & Bootstrap

### Initialization Flow (`tracenest.init`)

When `tracenest.init(...)` is called:

```python
import tracenest

tracenest.init(
    service="tracenest-sample-django",
    environment="production",
    endpoint="http://tp-otel-collector:4318",
    sample_rate=1.0,
    tags={"team": "backend", "datacenter": "us-east-1"},
)
```

1. **Configuration Normalization** ([`config.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/config.py)):
   - Instantiates `SDKConfig` validating endpoints, sample rates, static tags, and feature flags (`db_two_tier_spans`, `template_enabled`).
   - Automatically parses `OTEL_SERVICE_NAME`, `OTEL_ENVIRONMENT`, and `OTEL_EXPORTER_OTLP_ENDPOINT` environment variables if parameters are omitted.
2. **TracerProvider & Resource Construction** ([`tracing.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/tracing.py)):
   - Builds an OpenTelemetry `Resource` containing `service.name`, `deployment.environment`, `telemetry.sdk.name="tracenest"`, `telemetry.sdk.version="0.1.0"`, and custom static tags.
   - Instantiates `TracerProvider` with `TraceIdRatioBased` sampler if `sample_rate < 1.0`.
3. **Exporter & Batch Processor**:
   - Attaches `BatchSpanProcessor` with `OTLPSpanExporter` configured for HTTP/protobuf OTLP export.
   - Registers the global `TracerProvider` via `opentelemetry.trace.set_tracer_provider()`.
4. **Integration Manager Registration** ([`integrations/manager.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/manager.py)):
   - Scans installed libraries (`django`, `psycopg2`/`psycopg`, `redis`, `requests`).
   - Invokes `apply_integrations()`, which registers wrappers and hooks in reverse dependency order.

---

## 3. Standard OTel Defaults vs. TraceNest Custom Enrichments

The table below contrasts standard OpenTelemetry Contrib behavior with TraceNest's custom enhancements:

| Feature / Domain | Standard OTel Contrib Default | TraceNest Custom Enrichment | Why It Matters |
| :--- | :--- | :--- | :--- |
| **Span Naming (Database)** | Generic command type (e.g. `SELECT`, `INSERT`) | Visual icon + **Full SQL Statement** (e.g. `🔵 INSERT INTO "api_product" ("name", ...)`) | Enables instant identification of slow queries directly from the waterfall tree without opening span details. |
| **Database Span Count** | Creates 2-3 duplicate nested spans (`CursorWrapper` + `Psycopg2Instrumentor`) | **Single Canonical Span** via `suppress_db_instrumentation()` key suppression | Keeps waterfall trees flat, clean, and accurate in duration. |
| **PgBouncer Pool Detection** | Treated as standard `postgresql` server | Auto-detects `port 6432` / host `pgbouncer`; sets `peer.service="pgbouncer"` & `db.connection.pool="pgbouncer"` | Distinguishes connection pooler latency from underlying PostgreSQL database execution. |
| **Django Middleware** | Not instrumented or flat execution | Class-based `wrapt` wrapping of `BaseHandler.load_middleware` creating a nested waterfall tree | Visualizes exact middleware execution sequence (`⚙️ django.middleware...`) and bottleneck timing. |
| **Django Route Normalization** | Raw URL path (e.g. `/api/products/42/`) causing high metric cardinality | Low-cardinality route template (e.g. `/api/products/<id>/`) extracted from `resolver_match` | Prevents metric explosion in Prometheus / Grafana dashboards while storing full URL in `url.full`. |
| **Django Templates** | Not instrumented | Wraps `Template.render` with re-entrancy guards and exclude pattern matching (`django/forms/*`) | Identifies template rendering overhead without cluttering spans with internal form widget partials. |
| **Django Auth** | Not instrumented | Wraps `login` and `authenticate`, tagging `user.id`, `user.username`, `user.email` while redacting passwords | Correlates performance issues to specific authenticated users. |
| **Boto / S3 Cloud SDK** | Not instrumented by default | Auto-instruments `botocore` / `boto3` for S3 (AWS, MinIO, Cloudflare R2, Wasabi, LocalStack) via `BotoIntegration` | Captures object storage operations (`GetObject`, `PutObject`) across all cloud & self-hosted S3 endpoints. |
| **Self-Tracing Protection** | OTLP export calls can trigger telemetry spans recursively | `_is_telemetry_request` filters OTLP collector endpoint traffic (`4317`/`4318`) | Prevents infinite trace export feedback loops. |

---

## 4. Integration Technical Deep-Dives

### A. Django Waterfall & Component Instrumentation

TraceNest's Django integration ([`src/tracenest/integrations/django/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/)) orchestrates 6 distinct components:

```text
HTTP SERVER (django.request)
  ├── ⚙️ django.middleware.security.SecurityMiddleware.__call__
  ├── ⚙️ django.middleware.common.CommonMiddleware.__call__
  ├── ⚙️ django.middleware.csrf.CsrfViewMiddleware.__call__
  │    └── process_view
  ├── 🐍 django.view.ProductViewSet.list
  │    ├── 🔵 SELECT "api_product"."id" FROM "api_product"
  │    └── 🔴 GET cache:product_list
  ├── 🎨 django.template: catalog/list.html
  └── ⚙️ django.middleware.clickjacking.XFrameOptionsMiddleware.process_response
```

1. **Request Handler (`traced_get_response`)** ([`request.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/request.py)):
   - Wraps `django.core.handlers.base.BaseHandler.get_response`.
   - Extracts W3C `traceparent` headers from incoming `request.META` / `request.headers`.
   - Formats `http.route` using low-cardinality route cleaning (`_normalize_route`).
   - Attaches `X-Trace-ID`, `X-Span-ID`, and `traceparent` to outgoing HTTP response headers for correlation.
2. **Middleware Waterfall (`make_traced_load_middleware`)** ([`middleware.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/middleware.py)):
   - Intercepts `BaseHandler.load_middleware()` to wrap each class-based and function-based middleware in Django's middleware chain.
   - Creates `⚙️ django.middleware.<name>.__call__` spans around middleware execution.
   - Wraps `process_view`, `process_template_response`, `process_exception`, and `process_response` lifecycle hooks.
3. **Views (`traced_view_dispatch`)** ([`view.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/view.py)):
   - Wraps `django.views.generic.base.View.dispatch`, `View.setup`, and Django REST Framework `APIView.dispatch`.
   - Creates `🐍 django.view.<ViewClass>.<method>` spans.
4. **Template Rendering (`traced_template_render`)** ([`template.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/template.py)):
   - Wraps `django.template.base.Template.render` and `SimpleTemplateResponse.render`.
   - Uses `_in_template_span` ContextVar to control nested template rendering.
   - Evaluates `template_exclude` fnmatch patterns (`django/forms/*`, `debug_toolbar/*`).
5. **Cache Backends (`make_traced_cache_op`)** ([`cache.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/cache.py)):
   - Wraps `BaseCache`, `django_redis.cache.RedisCache`, `DefaultClient`, and `django.core.cache.backends.redis.RedisCache`.
   - Tracks cache operations (`get`, `set`, `delete`, `get_many`, `set_many`, `touch`, `incr`, `decr`).
   - Records `django.cache.hit` (`True`/`False`) on `get` calls.
6. **Authentication (`traced_login`, `traced_authenticate`)** ([`auth.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/django/auth.py)):
   - Wraps `django.contrib.auth.login` and `authenticate`.
   - Tags `usr.id`, `usr.username`, and `usr.email` on current active span.

---

### B. PostgreSQL & PgBouncer Topology

TraceNest's PostgreSQL integration ([`src/tracenest/integrations/postgres/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/postgres/)) instruments database interactions at the Django `CursorWrapper` level while managing low-level driver behavior.

```text
                     CursorWrapper.execute(sql)
                                 │
                     Sanitize SQL & Extract Op
                                 │
                    Check PgBouncer (Host/Port)
                                 │
                   ┌─────────────┴─────────────┐
                   ▼                           ▼
         PgBouncer Connection        Direct Postgres Connection
            (Port 6432)                 (Port 5432)
                   │                           │
          Span: 🔵 INSERT INTO ...      Span: 🐘 SELECT * FROM ...
                   │                           │
                   └─────────────┬─────────────┘
                                 │
                 with suppress_db_instrumentation():
                                 │
                     wrapped_cursor.execute(sql)
                                 │
                     (Psycopg2Instrumentor Suppressed)
```

1. **PgBouncer Detection**:
   ```python
   def is_pgbouncer_connection(db_host: Any, db_port: Any) -> bool:
       host_str = str(db_host).lower() if db_host else ""
       port_num = int(db_port) if db_port else 0
       return host_str == "pgbouncer" or port_num == 6432 or "pgbouncer" in host_str
   ```
2. **Single DB Span Enforcement**:
   To prevent `Psycopg2Instrumentor` from creating duplicate `SELECT` / `INSERT` spans inside `CursorWrapper.execute()`, TraceNest attaches the OTel instrumentation suppression key:
   ```python
   from contextlib import contextmanager
   from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY
   from opentelemetry.context import attach, detach, set_value

   @contextmanager
   def suppress_db_instrumentation():
       token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
       try:
           yield
       finally:
           detach(token)
   ```
3. **SQL Sanitization**:
   - `sanitize_sql()` strips literal strings, numeric values, and parameters, turning `WHERE id = 42` into `WHERE id = ?`.
   - `extract_operation()` extracts `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `BEGIN`, `COMMIT`.
   - `extract_query_summary()` extracts concise summaries like `SELECT api_product`.

---

### C. Redis Command & Pipeline Tracing

TraceNest's Redis integration ([`src/tracenest/integrations/redis/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/redis/)) handles both synchronous and asynchronous `redis-py` clients:

1. **Command Formatting & Masking** ([`client.py`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/redis/client.py)):
   - Redacts sensitive commands (`AUTH`, `CONFIG SET`, `CONFIG REQUIREPASS`).
   - Sanitizes IPv4/IPv6 addresses from keys and parameters using regex masking (`_IPV4_RE`, `_IPV6_RE`).
   - Truncates long arguments (> 256 bytes) and long statements (> 2048 bytes).
2. **Pipeline Execution**:
   - Formats multi-command pipelines into concise statements (e.g. `MULTI/EXEC (3 commands): GET, SET, INCR`).
   - Sets `db.redis.pipeline_length`.
3. **Re-Entrancy Guard**:
   - Attaches `_tp_in_exec` to the Redis instance to prevent recursive span creation when pipelines execute internal sub-commands.

---

### D. Outgoing HTTP Requests Tracing

TraceNest's Requests integration ([`src/tracenest/integrations/requests/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/src/tracenest/integrations/requests/)) configures OTel's official `RequestsInstrumentor`:

1. **Request Hook (`tracenest_request_hook`)**:
   - Sanitizes outgoing target URLs via `sanitize_url()`.
   - Sets span attributes: `http.request.method`, `url.full`, `server.address`, `server.port`, `peer.service`, `resource.name`.
   - Renames span to `🌐 HTTP <METHOD> <HOSTNAME>` (e.g. `🌐 HTTP GET api.github.com`).
2. **Response Hook (`tracenest_response_hook`)**:
   - Reads `response.status_code`.
   - If `status_code >= 400`, sets `error = True`, `error.type = HTTP<codenum>`, and `StatusCode.ERROR`.
3. **Self-Tracing Exporter Protection**:
   - `_is_telemetry_request()` checks if an outgoing request target matches OTLP exporter endpoints (`4317`, `4318`, `otel-collector` host) and suppresses tracing to avoid infinite export telemetry loops.

---

## 5. Visual Icon & Span Naming System

TraceNest applies a standardized visual badge system across all instrumented layers to make waterfall traces instantly readable in Grafana Tempo and SigNoz:

| Layer / Integration | Icon Badge | Span Name Format Example | Span Kind | Primary Attributes |
| :--- | :---: | :--- | :---: | :--- |
| **PgBouncer Pool** | 🔵 | `🔵 INSERT INTO "api_product" ("name", ...)` | `CLIENT` | `peer.service="pgbouncer"`, `db.connection.pool="pgbouncer"` |
| **PostgreSQL DB** | 🐘 | `🐘 SELECT * FROM "auth_user" WHERE id = ?` | `CLIENT` | `peer.service="postgres"`, `db.system="postgresql"`, `db.role="primary"` |
| **PostgreSQL Replica** | 🐘 | `🐘 SELECT "api_product"."id" FROM ...` | `CLIENT` | `peer.service="postgres-slave2"`, `db.role="replica"` |
| **Redis Command** | 🔴 | `🔴 GET user:1001` | `CLIENT` | `db.system="redis"`, `db.operation="GET"`, `db.redis.database_index=0` |
| **Redis Pipeline** | 🔴 | `🔴 PIPELINE (3 commands): GET, SET, INCR` | `CLIENT` | `db.system="redis"`, `db.redis.pipeline_length=3` |
| **Outgoing HTTP** | 🌐 | `🌐 HTTP GET api.github.com` | `CLIENT` | `http.request.method="GET"`, `server.address="api.github.com"` |
| **Django Request** | *(none)* | `django.request` | `SERVER` | `http.request.method="GET"`, `http.route="/api/products/"` |
| **Django Middleware** | ⚙️ | `⚙️ django.middleware.csrf.CsrfViewMiddleware.__call__` | `INTERNAL` | `component="django"`, `django.middleware="CsrfViewMiddleware"` |
| **Django View** | 🐍 | `🐍 django.view.ProductViewSet.list` | `INTERNAL` | `component="django"`, `django.view="ProductViewSet.list"` |
| **Django Template** | 🎨 | `🎨 django.template: catalog/list.html` | `INTERNAL` | `component="django"`, `django.template.name="catalog/list.html"` |
| **Django Auth** | 🔐 | `🔐 django.auth.login` | `INTERNAL` | `component="django"`, `user.id="42"`, `user.username="admin"` |

---

## 6. OTLP Self-Tracing Prevention Guard

When TraceNest exports batch spans via OTLP over HTTP (`/v1/traces`), the HTTP request itself must **not** generate a telemetry span. Otherwise, exporting 1 span would generate 1 HTTP export span, which would generate another HTTP export span, creating an infinite loop.

TraceNest enforces a 3-layer guard:

```text
Outgoing Request
       │
       ▼
_is_telemetry_request(url, hostname, port)
       │
  ┌────┴──────────────────────────┐
  │ Match?                        │
  ▼                               ▼
[YES: OTLP Collector Export]    [NO: Application Business HTTP Request]
  │                               │
  ▼                               ▼
Suppress Tracing               Execute tracenest_request_hook
(Return without span creation) (Create 🌐 HTTP <METHOD> <HOST> span)
```

1. **Host & Port Check**: Checks if host is `otel-collector`, `tp-otel-collector`, or port is `4317`/`4318` on `localhost`/`127.0.0.1`.
2. **Config Endpoint Check**: Verifies if URL matches configured `traces_endpoint` or `endpoint`.
3. **Re-Entrancy Guard (`reentrant_guard`)**: Context manager setting a thread-local / context flag during export execution.

---

## 7. OpenTelemetry Collector & Metrics Pipeline

TraceNest pairs with a custom **OpenTelemetry Collector Contrib** distribution ([`docker/otel-collector/otel-collector-config.yaml`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/docker/otel-collector/otel-collector-config.yaml)).

```yaml
receivers:
  otlp:
    protocols:
      grpc: { endpoint: "0.0.0.0:4317" }
      http: { endpoint: "0.0.0.0:4318" }

  postgresql/pgbouncer:
    endpoint: pgbouncer:6432
    transport: tcp
    username: django
    password: django
    databases: [pgbouncer]
    collection_interval: 10s

connectors:
  spanmetrics:
    histogram:
      explicit:
        buckets: [2ms, 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s]
    dimensions:
      - { name: http.request.method }
      - { name: http.status_code }
      - { name: http.route }
      - { name: db.system }
      - { name: db.operation }
      - { name: db.query.summary }
      - { name: db.connection.pool }
      - { name: peer.service }

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls: { insecure: true }
  prometheus:
    endpoint: "0.0.0.0:8889"
    namespace: apm

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/tempo, spanmetrics]
    metrics:
      receivers: [otlp, spanmetrics, postgresql/pgbouncer]
      processors: [batch]
      exporters: [prometheus]
```

### Collector Capabilities
- **SpanMetrics Connector**: Automatically derives RED metrics (Rate, Error Rate, Duration histograms) from trace spans grouped by route, database operation, and peer service.
- **PgBouncer Metrics Receiver**: Polls PgBouncer `SHOW POOLS` and `SHOW STATS` every 10s to collect active/waiting client connections and pool saturation.

---

## 8. Testing & Verification Architecture

The test suite in [`tests/`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/tests/) provides 100% offline verification using OpenTelemetry's `InMemorySpanExporter`:

```bash
.venv/bin/pytest
```

- **`test_init.py`** (18 tests): SDK configuration, environment variable overrides, reset logic.
- **`test_django.py` & `test_django_advanced.py`** (30 tests): Middleware waterfall hierarchy, view setup/dispatch, DRF view dispatch, template rendering, template exclusions, Django cache operations, auth login/authenticate tagging.
- **`test_postgres.py`** (16 tests): PgBouncer pool detection, single-span enforcement (`suppress_db_instrumentation`), SQL sanitization, operation extraction, primary/replica role tagging.
- **`test_redis.py`** (10 tests): Command formatting, AUTH/CONFIG redaction, IP address regex masking, pipeline execution.
- **`test_requests.py`** (6 tests): Outgoing HTTP request hooks, URL sanitization, response status code recording, self-tracing prevention filter.
