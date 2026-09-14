# TraceNest SDK — Internal Architecture, Implementation & Developer Instrumentation Guide

> **Target Audience**: This document is written for any Python software engineer with basic Python knowledge and a basic conceptual understanding of metrics (what counters and durations are). No prior experience with OpenTelemetry internals or distributed tracing compilers is required.

---

## Table of Contents

1. [Foundational Concepts: Observability from First Principles](#1-foundational-concepts-observability-from-first-principles)
   - [Metrics, Logs, and Traces: What Are They?](#metrics-logs-and-traces-what-are-they)
   - [What is Distributed Tracing?](#what-is-distributed-tracing)
   - [Anatomy of a Span](#anatomy-of-a-span)
   - [Context Propagation (The W3C Traceparent Standard)](#context-propagation-the-w3c-traceparent-standard)
   - [The OpenTelemetry Core Pipeline](#the-opentelemetry-core-pipeline)
2. [Why TraceNest Was Built (Problems with Vanilla OTel)](#2-why-tracenest-was-built-problems-with-vanilla-otel)
   - [The Datadog Parity Problem](#the-datadog-parity-problem)
   - [The Duplicate Span Dilemma](#the-duplicate-span-dilemma)
   - [Metric Cardinality Explosion](#metric-cardinality-explosion)
   - [Connection Pool Opacity (PgBouncer)](#connection-pool-opacity-pgbouncer)
   - [The OTLP Self-Tracing Infinite Loop](#the-otlp-self-tracing-infinite-loop)
   - [The Zero-Crash Golden Rule](#the-zero-crash-golden-rule)
3. [Architecture Overview & Codebase Tour](#3-architecture-overview--codebase-tour)
   - [Repository Layout](#repository-layout)
   - [Execution Flow: From `init()` to OTLP Export](#execution-flow-from-init-to-otlp-export)
4. [Core SDK Primitives: How They Are Coded](#4-core-sdk-primitives-how-they-are-coded)
   - [Configuration Engine (`tracenest/config.py`)](#configuration-engine-tracenestconfigpy)
   - [The Fail-Safe Exporter (`tracenest/exporter.py`)](#the-fail-safe-exporter-tracenestexporterpy)
   - [Rule-Based Sampler (`tracenest/sampler.py`)](#rule-based-sampler-tracenestsamplerpy)
   - [SQL and URL Sanitization (`tracenest/sanitize.py`)](#sql-and-url-sanitization-tracenestsanitizepy)
   - [Tracing Primitives & Guards (`tracenest/tracing.py`)](#tracing-primitives--guards-tracenesttracingpy)
   - [SDK Bootstrap & Lifecycle (`tracenest/__init__.py`)](#sdk-bootstrap--lifecycle-tracenest__init__py)
5. [The Monkey-Patching Engine (`tracenest/integrations/`)](#5-the-monkey-patching-engine-tracenestintegrations)
   - [Why `wrapt` Instead of Naive Monkey-Patching?](#why-wrapt-instead-of-naive-monkey-patching)
   - [The `BaseIntegration` Contract (`base.py`)](#the-baseintegration-contract-basepy)
   - [The `IntegrationManager` Registry (`manager.py`)](#the-integrationmanager-registry-managerpy)
6. [Deep-Dive into Built-in Integrations](#6-deep-dive-into-built-in-integrations)
   - [Django Integration (Request, Middleware, Views, Templates, Cache, Auth)](#django-integration)
   - [PostgreSQL & PgBouncer Integration (Single Canonical Span & Topology)](#postgresql--pgbouncer-integration)
   - [Redis Integration (Sanitization, Pipelines, Async Support)](#redis-integration)
   - [Outgoing HTTP Requests (Requests Hook & Self-Tracing Guard)](#outgoing-http-requests)
   - [AWS / Boto Integration (Cloud & S3 Storage)](#aws--boto-integration)
7. [Visual Badges & Span Naming Reference](#7-visual-badges--span-naming-reference)
8. [OpenTelemetry Collector & Metrics Pipeline](#8-opentelemetry-collector--metrics-pipeline)
9. [Step-by-Step Guide: How to Build a New Instrumentation](#9-step-by-step-guide-how-to-build-a-new-instrumentation)
   - [Architectural Checklist](#architectural-checklist)
   - [Step 1: Identify Targets and Seams](#step-1-identify-targets-and-seams)
   - [Step 2: Subclass `BaseIntegration`](#step-2-subclass-baseintegration)
   - [Step 3: Implement Re-entrancy & Tracing Wrappers](#step-3-implement-re-entrancy--tracing-wrappers)
   - [Step 4: Register with `IntegrationManager`](#step-4-register-with-integrationmanager)
   - [Step 5: Write Offline Tests with `InMemorySpanExporter`](#step-5-write-offline-tests-with-inmemoryspanexporter)
10. [Fully Worked Example: Building a Celery Task Integration](#10-fully-worked-example-building-a-celery-task-integration)
11. [Testing & Verification Architecture](#11-testing--verification-architecture)
12. [Common Pitfalls & Troubleshooting Cheatsheet](#12-common-pitfalls--troubleshooting-cheatsheet)

---

## 1. Foundational Concepts: Observability from First Principles

Before diving into how TraceNest is coded, we need to establish a shared vocabulary for observability.

### Metrics, Logs, and Traces: What Are They?

When a web server is handling requests, software engineers need to understand three questions:
1. **Is the system healthy?** (Metrics)
2. **What specifically happened?** (Logs)
3. **Where was time spent across interconnected systems?** (Traces)

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        THE THREE PILLARS                               │
│                                                                        │
│   METRICS (Aggregated Numbers)                                         │
│   "Our server received 1,200 requests/sec, 2% failed, p95 is 45ms."    │
│                                                                        │
│   LOGS (Discrete Text Events)                                          │
│   "[2026-09-14 12:01:02] ERROR: Database connection timed out."        │
│                                                                        │
│   TRACES (Causal Request Waterfalls)                                   │
│   User Request -> Auth Middleware -> SQL Query -> Redis Cache -> HTML   │
└────────────────────────────────────────────────────────────────────────┘
```

#### What is a Metric?
A metric is a numeric measurement recorded over time. Unlike a log message that describes a single event, a metric aggregates data points into statistical summaries.

The most critical metrics in web engineering follow the **RED Method**:
- **Rate**: Number of requests per second (e.g. 500 req/sec). Measured as a **Counter** (a number that only goes up).
- **Errors**: Number of failed requests per second (e.g. 5 errors/sec). Measured as a **Counter**.
- **Duration**: How long requests take from start to finish (e.g. 25ms, 120ms). Measured as a **Histogram** (bucketing execution times to compute percentiles like Median/P50, P95, and P99).

### What is Distributed Tracing?

Imagine a user clicks "Checkout" in a web app. To fulfill that one request, the application executes code across multiple layers:
1. The web framework receives an HTTP `POST /api/checkout/`.
2. Security and authentication middleware run.
3. The view function queries PostgreSQL to check user balance and deduct inventory.
4. The view sends a command to Redis to invalidate cached product counts.
5. The view makes an outbound HTTP call to a payment gateway (e.g., Stripe).
6. The view returns an HTTP `200 OK` JSON response.

If that request takes 2.5 seconds instead of 100 milliseconds, **where was the time spent?**
- Was the database slow?
- Was Redis locked?
- Was Stripe taking 2.3 seconds?
- Did a Django template take forever to render?

**Distributed Tracing solves this problem.** It tracks the end-to-end execution of that single request across all software boundaries and visualizes it as a tree of timed blocks called a **waterfall**.

```text
django.request: POST /api/checkout/ ──────────────────────────────────── [2500ms]
  ├── ⚙️ SecurityMiddleware.__call__ ─────────────────────────────── [2498ms]
  │     ├── ⚙️ SessionMiddleware.__call__ ────────────────────────── [2495ms]
  │     │     ├── 🐍 CheckoutView.post ───────────────────────────── [2480ms]
  │     │     │     ├── 🔵 SELECT * FROM "inventory" WHERE ... ─────── [15ms]
  │     │     │     ├── 🔴 GET user:1042:balance ────────────────────── [2ms]
  │     │     │     ├── 🌐 HTTP POST api.stripe.com/v1/charges ──── [2420ms]  <-- BOTTLENECK!
  │     │     │     └── 🔵 UPDATE "inventory" SET qty = ... ────────── [12ms]
```

Looking at this waterfall, any engineer immediately identifies that Stripe took 2,420ms. Without distributed tracing, developers would waste hours guessing or adding print statements.

### Anatomy of a Span

A **Span** is the fundamental building block of a trace. It represents a single contiguous unit of work that has a beginning, an end, and contextual metadata.

A Span contains:
- **Name**: A human-readable label (e.g. `django.request`, `🔵 SELECT * FROM users`, `🔴 GET cache:key`).
- **Trace ID**: A globally unique 32-character hexadecimal string identifying the entire request journey. Every span in that request shares the exact same `trace_id`.
- **Span ID**: A unique 16-character hexadecimal string identifying *this specific unit of work*.
- **Parent Span ID**: The `span_id` of the caller that invoked this span. If a span has no parent, it is the **Root Span**.
- **Timestamps**: Start time (`start_time_unix_nano`) and End time (`end_time_unix_nano`).
- **SpanKind**: Tells the UI what role this span plays:
  - `SERVER`: Receives an incoming network call (e.g., Django processing an incoming HTTP request).
  - `CLIENT`: Makes an outgoing call to a dependency (e.g., querying Postgres, calling Redis, sending an HTTP request).
  - `INTERNAL`: Internal in-process work (e.g., a middleware executing, a view method, rendering a template).
  - `PRODUCER` / `CONSUMER`: Message queues (e.g., RabbitMQ, Celery).
- **Attributes**: Key-value pairs providing context (e.g., `http.method = "POST"`, `db.system = "postgresql"`, `http.status_code = 200`).
- **Status**: `OK`, `ERROR`, or `UNSET`. When code throws an exception, the span status becomes `ERROR`.
- **Events / Exceptions**: Recorded stack traces and error messages if the span crashed.

### Context Propagation (The W3C Traceparent Standard)

When Service A calls Service B over HTTP, how does Service B know it is part of Service A's trace?

Through **Context Propagation**. The OpenTelemetry standard uses the **W3C Trace Context** specification. Before sending an HTTP request, Service A attaches a special header called `traceparent`:

```http
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
              │  └──────────────┬───────────────┘ └───────┬──────┘ └─┬┘
           Version          Trace ID                  Span ID      Flags (01 = Sampled)
```

When Service B receives this request, it parses the `traceparent` header, extracts the `Trace ID`, and sets Service A's `Span ID` as the `Parent Span ID` of its own root span. The trace tree remains linked across servers.

### The OpenTelemetry Core Pipeline

In OpenTelemetry Python, telemetry flows through a clean pipeline of 5 components:

```text
┌──────────────┐     creates      ┌──────────────┐
│ Application  │ ───────────────> │  Tracer      │
└──────────────┘                  └──────┬───────┘
                                         │ starts
                                         ▼
                                  ┌──────────────┐
                                  │   Span       │
                                  └──────┬───────┘
                                         │ finishes
                                         ▼
                                  ┌──────────────┐
                                  │   Sampler    │ (Keep or Drop?)
                                  └──────┬───────┘
                                         │ Keep
                                         ▼
                                  ┌──────────────┐
                                  │SpanProcessor │ (Batch into memory queue)
                                  └──────┬───────┘
                                         │ periodically flushes
                                         ▼
                                  ┌──────────────┐
                                  │ SpanExporter │ (Send via HTTP to Collector)
                                  └──────────────┘
```

1. **`TracerProvider`**: The central factory that holds configuration, resources, samplers, and processors.
2. **`Tracer`**: Obtained via `trace.get_tracer("module_name")`. It is the object you use to start spans (`tracer.start_as_current_span(...)`).
3. **`Sampler`**: Determines whether a span should be recorded or discarded to save bandwidth and storage.
4. **`SpanProcessor`**: Hooks into the lifecycle of spans. `BatchSpanProcessor` queues finished spans in memory and flushes them in batches every few seconds in a background thread.
5. **`SpanExporter`**: Translates internal span objects into serialized formats (such as Protobuf over HTTP) and pushes them to the OpenTelemetry Collector on port 4318.

---

## 2. Why TraceNest Was Built (Problems with Vanilla OTel)

If OpenTelemetry already has official open-source packages (`opentelemetry-sdk`, `opentelemetry-instrumentation-django`), why did we build TraceNest?

Vanilla OpenTelemetry contrib packages were designed as generic, lowest-common-denominator telemetry generators. In production, they exhibit critical drawbacks when trying to replace enterprise APM solutions like Datadog:

### The Datadog Parity Problem
- Vanilla `opentelemetry-instrumentation-django` creates **a single flat span** for the entire HTTP request. It does not instrument the middleware chain, individual views, or template rendering.
- When an engineer inspects a slow request, they cannot tell whether time was wasted in `CsrfViewMiddleware`, authentication, or template parsing.
- **TraceNest Solution**: Intercepts `BaseHandler.load_middleware` and `Template.render` to construct a 1-to-1 Datadog-grade waterfall showing every layer of execution.

### The Duplicate Span Dilemma
- In a standard Python stack with Django and PostgreSQL, you typically have two libraries: Django's database backend (`django.db.backends.utils.CursorWrapper`) and the database driver (`psycopg2`).
- If you instrument both with standard OTel, every SQL query generates **2 or 3 duplicate spans**:
  ```text
  CursorWrapper.execute (from Django)
    └── Psycopg2.execute (from driver)
          └── Raw socket write
  ```
- This clutters the trace waterfall, triples telemetry egress costs, and doubles metric query counts.
- **TraceNest Solution**: A custom suppression context manager (`suppress_db_instrumentation()`) that attaches OpenTelemetry's internal `_SUPPRESS_INSTRUMENTATION_KEY`, ensuring that Django's high-level cursor creates **one single canonical span** while suppressing driver-level noise.

### Metric Cardinality Explosion
- In OpenTelemetry, when Prometheus metrics are generated from trace spans (via the `spanmetrics` connector), every unique attribute value creates a new metric timeseries.
- If span names or HTTP route attributes contain raw URLs with IDs (e.g. `/api/products/1042/`, `/api/products/1043/`), a site with 100,000 products will generate **100,000 distinct Prometheus metric series**. This crashes Prometheus ("Cardinality Explosion").
- **TraceNest Solution**: A route normalization engine (`_normalize_route()`) that inspects Django's `resolver_match` and regex patterns, converting `/api/products/1042/` into low-cardinality route templates like `/api/products/<id>/`.

### Connection Pool Opacity (PgBouncer)
- Most high-scale Django architectures route database queries through **PgBouncer** (a lightweight connection pooler) on port `6432`.
- Vanilla OTel cannot differentiate whether a connection went directly to Postgres or through PgBouncer.
- **TraceNest Solution**: Auto-detects port `6432` and host `pgbouncer`, tagging spans with `db.connection.pool = "pgbouncer"` and `peer.service = "pgbouncer"`. In the UI, pool queries are visually badged with `🔵` while direct Postgres queries are badged with `🐘`.

### The OTLP Self-Tracing Infinite Loop
- When TraceNest sends a batch of spans to the OTel collector at `http://collector:4318/v1/traces`, it uses the Python `requests` library.
- If the `requests` library is auto-instrumented to trace all outgoing HTTP calls, sending spans will generate an outgoing HTTP span.
- That new span must now be exported... which triggers another HTTP request... which creates another span...
- **Result: An infinite export loop that exhausts memory and CPU.**
- **TraceNest Solution**: A 3-layer self-tracing prevention guard (`_is_telemetry_request()`) in `requests/client.py` that identifies collector traffic and suppresses trace generation on exporter calls.

### The Zero-Crash Golden Rule
- **Telemetry must NEVER take down the application.** If the observability collector goes offline, crashes, or refuses connections, the host Django app must continue serving users at 100% speed with zero errors.
- **TraceNest Solution**: `SafeSpanExporter` wraps standard exporters in a bulletproof `try/except` block, catches network connection errors, rate-limits error logging to once every 5 minutes, and absorbs failures silently.

---

## 3. Architecture Overview & Codebase Tour

### Repository Layout

TraceNest is cleanly organized under `src/tracenest/`:

```text
src/tracenest/
├── __init__.py           # SDK public API, init() lifecycle, global TracerProvider singleton
├── config.py             # SDKConfig dataclass, env var parsing, settings auto-detection
├── exporter.py           # SafeSpanExporter wrapper preventing crashes if collector is down
├── sampler.py            # TraceNestRuleBasedSampler (ignores /health, per-route ratios)
├── sanitize.py           # Regex-based SQL literal scrubber and URL credential stripper
├── tracing.py            # Boilerplate-free context managers: traced_span() & reentrant_guard()
├── version.py            # SDK semantic version (__version__ = "0.1.0")
└── integrations/         # Library-specific auto-instrumentation modules
    ├── __init__.py       # Exports BaseIntegration and get_integration_manager()
    ├── base.py           # BaseIntegration ABC: wrapt monkey-patching and unwrap tracking
    ├── manager.py        # IntegrationManager: registration, aliases, and lifecycle
    ├── django/           # Django request, middleware waterfall, view, template, cache, auth
    ├── postgres/         # Django CursorWrapper, PgBouncer detection, single-span guard
    ├── redis/            # Redis command sanitization, pipeline tracing, async support
    ├── requests/         # Outbound HTTP tracing, URL sanitization, self-tracing guard
    └── boto/             # Botocore/Boto3 AWS and S3 object storage tracing
```

### Execution Flow: From `init()` to OTLP Export

Here is the exact lifecycle when a developer calls `tracenest.init()` in their Django app:

```text
1. Developer calls tracenest.init(service="my-app", endpoint="http://collector:4318")
                          │
                          ▼
2. SDKConfig loads & normalizes configuration (Priority: Kwargs > Env Vars > Defaults)
                          │
                          ▼
3. Resource constructed: service.name, environment, telemetry.sdk.version
                          │
                          ▼
4. Sampler constructed: TraceNestRuleBasedSampler wrapped in ParentBased
                          │
                          ▼
5. TracerProvider created with Resource + Sampler
                          │
                          ▼
6. SafeSpanExporter created wrapping OTLPSpanExporter(http://collector:4318/v1/traces)
                          │
                          ▼
7. BatchSpanProcessor attached to TracerProvider with SafeSpanExporter
                          │
                          ▼
8. Global Trace Provider set (trace.set_tracer_provider)
                          │
                          ▼
9. Global TextMap Propagator set to TraceContextTextMapPropagator (W3C traceparent)
                          │
                          ▼
10. atexit.register(_shutdown) registered for graceful process termination
                          │
                          ▼
11. IntegrationManager.apply_integrations() scans installed libraries:
      - django installed?   --> Patch BaseHandler, View, Template, Cache, Auth
      - postgres installed? --> Patch CursorWrapper, suppress Psycopg2 duplicate spans
      - redis installed?    --> Patch Redis.execute_command & Pipeline.execute
      - requests installed? --> Attach hooks to RequestsInstrumentor
      - boto installed?     --> Patch BotocoreInstrumentor
```

---

## 4. Core SDK Primitives: How They Are Coded

Let's examine how each foundational module inside `src/tracenest/` is coded.

### Configuration Engine (`tracenest/config.py`)

Every feature in the SDK is controlled by a unified configuration dataclass: `SDKConfig`.

#### How Settings Precedence Works
When resolving any setting (e.g. `sample_rate`), `SDKConfig.from_env_and_kwargs()` prioritizes sources in a strict 5-tier order:
1. **Explicit keyword arguments** passed to `tracenest.init(sample_rate=0.5)`
2. **TraceNest-specific environment variables** (`TRACENEST_SAMPLE_RATE`)
3. **OpenTelemetry standard environment variables** (`OTEL_TRACES_SAMPLER_ARG`)
4. **Django `settings.py` auto-detection** (`getattr(settings, "TRACENEST_SAMPLE_RATE", ...)`)
5. **Built-in default values** (`1.0`)

#### Code Breakdown: Safe Boolean and Header Parsers
Environment variables are always strings. A naive `bool("false")` in Python evaluates to `True`! TraceNest avoids this bug using `_str_to_bool`:

```python
def _str_to_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")
```

For headers passed in environment variables like `OTEL_EXPORTER_OTLP_HEADERS="key1=val1,key2=val2"`, TraceNest safely parses them into a Python dictionary:

```python
def _parse_headers(headers_str: Optional[str]) -> Dict[str, str]:
    if not headers_str:
        return {}
    headers = {}
    for item in headers_str.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers
```

---

### The Fail-Safe Exporter (`tracenest/exporter.py`)

If an application is under heavy load and the OTel Collector container restarts, standard exporters will throw connection refused errors (`Errno 61` or `ConnectionRefusedError`) on every batch export. In unhandled setups, this can log thousands of exceptions per second and saturate disks.

TraceNest solves this with `SafeSpanExporter`:

```python
class SafeSpanExporter(SpanExporter):
    """Fail-safe wrapper around any OpenTelemetry SpanExporter."""

    def __init__(self, exporter: SpanExporter, endpoint: Optional[str] = None):
        self._exporter = exporter
        self._endpoint = endpoint or getattr(exporter, "_endpoint", "collector")
        self._last_log_time = 0.0
        self._error_count = 0

    def export(self, spans: Any) -> SpanExportResult:
        try:
            res = self._exporter.export(spans)
            if res == SpanExportResult.SUCCESS and self._error_count > 0:
                logger.info("TraceNest: Connection to collector at %s restored.", self._endpoint)
                self._error_count = 0
            return res
        except Exception as exc:
            self._error_count += 1
            now = time.time()
            # Rate-limit warnings: log the first failure, then at most once every 5 minutes (300s)
            if now - self._last_log_time > 300:
                self._last_log_time = now
                logger.warning(
                    "TraceNest: Unable to export spans to collector at %s (%s). "
                    "Host application is completely unaffected. (Failures: %d)",
                    self._endpoint,
                    exc,
                    self._error_count,
                )
            return SpanExportResult.FAILURE
```

**Key Takeaways**:
- The exception is caught inside `export()`. It **never** bubbles up to the application threads.
- `self._last_log_time > 300`: Logs at most once every 5 minutes.
- When connection returns, it logs an informational recovery message and resets the error counter.

---

### Rule-Based Sampler (`tracenest/sampler.py`)

In high-throughput services, sampling every single trace (100%) can generate gigabytes of data per minute. However, you often want:
1. **Health checks ignored entirely**: Don't waste storage on Kubernetes liveness probes (`/healthz`, `/metrics`).
2. **Critical endpoints sampled at 100%**: Always trace `/api/checkout/*`.
3. **Background queries sampled at 10%**: Trace 1 in 10 for `/api/search/*`.
4. **Inherited parent decisions**: If an upstream service decided to trace a request, downstream services should respect that decision (`ParentBased`).

TraceNest implements `TraceNestRuleBasedSampler`:

```python
class TraceNestRuleBasedSampler(Sampler):
    def __init__(
        self,
        global_sample_rate: float = 1.0,
        ignore_endpoints: Optional[List[str]] = None,
        endpoint_sample_rules: Optional[Dict[str, float]] = None,
    ):
        self.global_sample_rate = max(0.0, min(1.0, float(global_sample_rate)))
        self.ignore_endpoints = list(ignore_endpoints or [])
        self.endpoint_sample_rules = dict(endpoint_sample_rules or {})

        # Pre-instantiate ratio samplers for instant hash lookup
        self._global_ratio_sampler = TraceIdRatioBased(self.global_sample_rate)
        self._rule_ratio_samplers = {
            pattern: TraceIdRatioBased(max(0.0, min(1.0, float(rate))))
            for pattern, rate in self.endpoint_sample_rules.items()
        }

    def should_sample(
        self,
        parent_context: Optional[Context],
        trace_id: int,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
        links: Optional[Sequence[Link]] = None,
    ) -> SamplingResult:
        attributes = attributes or {}
        target_path = (
            attributes.get("http.target")
            or attributes.get("url.path")
            or attributes.get("http.route")
            or name
        )
        if isinstance(target_path, str):
            clean_path = target_path.split("?")[0]

            # 1. Drop ignored paths (e.g. /health*)
            for pattern in self.ignore_endpoints:
                if fnmatch.fnmatch(clean_path, pattern):
                    return SamplingResult(Decision.DROP)

            # 2. Check route-specific ratio rules
            for pattern, ratio_sampler in self._rule_ratio_samplers.items():
                if fnmatch.fnmatch(clean_path, pattern):
                    return ratio_sampler.should_sample(
                        parent_context=parent_context,
                        trace_id=trace_id,
                        name=name,
                        kind=kind,
                        attributes=attributes,
                        links=links,
                    )

        # 3. Fallback to global sample rate
        return self._global_ratio_sampler.should_sample(
            parent_context=parent_context,
            trace_id=trace_id,
            name=name,
            kind=kind,
            attributes=attributes,
            links=links,
        )
```

Finally, `create_tracenest_sampler` wraps this root sampler in OpenTelemetry's `ParentBased` sampler:
```python
def create_tracenest_sampler(...) -> Sampler:
    root_sampler = TraceNestRuleBasedSampler(...)
    return ParentBased(root=root_sampler)
```
If an incoming request already has a sampled W3C `traceparent` header from a gateway, `ParentBased` respects that decision; if it is a new request, it evaluates our rule-based sampler.

---

### SQL and URL Sanitization (`tracenest/sanitize.py`)

Sending raw SQL queries or URLs with passwords into telemetry backends is a major security vulnerability (exposing PII, session tokens, or credentials).

#### SQL Sanitization
Given the SQL statement:
```sql
SELECT * FROM users WHERE email = 'alice@example.com' AND status = 1 AND age > 21
```
The sanitized statement stored in `db.statement` should be:
```sql
SELECT * FROM users WHERE email = ? AND status = ? AND age > ?
```

TraceNest achieves this using two compiled regex patterns:

```python
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_NUMERIC_LITERAL_RE = re.compile(r"(?<=[^\w\$.])\b\d+(?:\.\d+)?\b")

def sanitize_sql(sql: Optional[str], max_length: int = 4096) -> str:
    if not sql:
        return ""
    # 1. Replace string literals with ?
    sanitized = _STRING_LITERAL_RE.sub("?", str(sql))
    # 2. Replace standalone numeric literals with ?
    sanitized = _NUMERIC_LITERAL_RE.sub("?", sanitized)
    # 3. Collapse multiple whitespace characters into a single space
    normalized = _WHITESPACE_RE.sub(" ", sanitized).strip()

    if len(normalized) > max_length:
        return normalized[:max_length] + " ... [truncated]"
    return normalized
```

#### URL Sanitization
If an outbound HTTP URL contains credentials:
`https://admin:secret_token_123@api.payment.com/v1/charge?token=abc#receipt`
`sanitize_url()` strips the userinfo and fragment:
`https://api.payment.com/v1/charge?token=abc`

---

### Tracing Primitives & Guards (`tracenest/tracing.py`)

In vanilla Python OTel, starting a span and handling exceptions requires 15 lines of repetitive boilerplate:
```python
# Vanilla OTel boilerplate (Repeated in every function without TraceNest):
tracer = trace.get_tracer("my-tracer")
with tracer.start_as_current_span("my-span") as span:
    try:
        result = do_work()
        span.set_status(StatusCode.OK)
        return result
    except Exception as exc:
        span.record_exception(exc)
        span.set_attribute("error", True)
        span.set_attribute("error.type", exc.__class__.__name__)
        span.set_status(StatusCode.ERROR, str(exc))
        raise
```

TraceNest collapses all of this into one clean, reusable context manager: **`traced_span`**:

```python
@contextlib.contextmanager
def traced_span(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: str = "tracenest",
    context: Optional[Context] = None,
) -> Iterator[Span]:
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes,
        context=context,
    ) as span:
        try:
            yield span
            if span.is_recording() and hasattr(span, "status") and span.status.status_code == StatusCode.UNSET:
                span.set_status(StatusCode.OK)
        except Exception as exc:
            if span.is_recording():
                span.record_exception(exc)
                span.set_attribute("error", True)
                span.set_attribute("error.type", exc.__class__.__name__)
                span.set_status(StatusCode.ERROR, description=str(exc))
            raise
```

#### The Re-entrancy Guard
Suppose a database cursor executes a query. Inside that query execution, a helper method calls another method on the same cursor. Without protection, you would get nested spans for the exact same query.

TraceNest provides `reentrant_guard(instance, attr)`:

```python
@contextlib.contextmanager
def reentrant_guard(instance: Any, attr: str) -> Iterator[bool]:
    if getattr(instance, attr, False):
        yield False  # Already in an execution: DO NOT create a nested span
        return
    setattr(instance, attr, True)
    try:
        yield True   # Outermost caller: Create the span
    finally:
        setattr(instance, attr, False)
```

---

### SDK Bootstrap & Lifecycle (`tracenest/__init__.py`)

`tracenest.init(...)` is the single entry point. It is **thread-safe** and **idempotent**: calling it twice in the same process returns the existing provider without re-patching.

```python
_INITIALIZED = False
_INIT_LOCK = threading.Lock()
_ACTIVE_PROVIDER: Optional[TracerProvider] = None

def init(...) -> TracerProvider:
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_CONFIG

    with _INIT_LOCK:
        if _INITIALIZED and _ACTIVE_PROVIDER is not None:
            return _ACTIVE_PROVIDER  # Idempotent return

        config = SDKConfig.from_env_and_kwargs(...)
        
        # 1. Build Resource attributes
        resource_data = {
            "service.name": config.service_name,
            "deployment.environment.name": config.environment,
            "service.version": config.version,
            "telemetry.sdk.name": "tracenest",
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": __version__,
        }
        resource = Resource.create(resource_data)

        # 2. Sampler
        sampler = create_tracenest_sampler(...)

        # 3. Provider & Safe Exporter
        provider = TracerProvider(resource=resource, sampler=sampler)
        safe_exporter = SafeSpanExporter(OTLPSpanExporter(endpoint=config.traces_endpoint))
        provider.add_span_processor(BatchSpanProcessor(safe_exporter))

        # 4. Set Global State
        trace.set_tracer_provider(provider)
        set_global_textmap(TraceContextTextMapPropagator())

        # 5. Auto-patch integrations
        if auto_patch:
            get_integration_manager().apply_integrations(config=config)

        # 6. Graceful Flush on Exit
        atexit.register(lambda: provider.shutdown())

        _INITIALIZED = True
        return provider
```

---

## 5. The Monkey-Patching Engine (`tracenest/integrations/`)

How does TraceNest intercept calls in third-party libraries (like Django or Redis) without asking developers to change their application code? Through **Monkey-Patching**.

### Why `wrapt` Instead of Naive Monkey-Patching?

A naive Python monkey patch looks like this:
```python
# NAIVE PATCH - DO NOT DO THIS:
original_render = Template.render

def my_render(self, *args, **kwargs):
    print("Rendering!")
    return original_render(self, *args, **kwargs)

Template.render = my_render
```
**Why naive patching breaks in production**:
1. **Lost Function Signatures**: Introspection tools (like Django's internal checks or IDE debuggers) cannot inspect argument names.
2. **Broken Bound Methods**: If you patch methods on classes vs instances, `self` binding can fail.
3. **Unwrapping is Impossible**: If tests run, or if a user uninstruments, restoring the exact previous state across multiple libraries is prone to memory leaks and bugs.
4. **Decorator Incompatibility**: Naive patching breaks if another library already wrapped the function.

**The Solution**: We use **`wrapt`** (`wrapt.wrap_function_wrapper`).
`wrapt` implements an Object Proxy in C/Python that preserves function signatures, docstrings, `__qualname__`, bound method mechanics, and can be cleanly unwrapped.

### The `BaseIntegration` Contract (`base.py`)

Every integration in TraceNest inherits from `BaseIntegration`:

```python
class BaseIntegration(abc.ABC):
    name: str = "base"

    def __init__(self, config: Optional[SDKConfig] = None) -> None:
        self._config = config
        self._instrumented: bool = False
        self._wrapped_targets: List[Tuple[Any, str, Any]] = []

    @abc.abstractmethod
    def is_installed(self) -> bool:
        """Check if target library is present in the environment."""
        pass

    @abc.abstractmethod
    def _apply_patch(self) -> None:
        """Apply wrappers using self.wrap()."""
        pass

    def wrap(self, target: Union[str, Any], attribute_name: str, wrapper: Callable) -> None:
        """Safely wraps a target function while tracking it for clean uninstrumentation."""
        if isinstance(target, str):
            module_name, _, class_or_fn = target.rpartition(".")
            target_mod = importlib.import_module(module_name)
            target_obj = getattr(target_mod, class_or_fn)
        else:
            target_obj = target

        original = getattr(target_obj, attribute_name, None)
        wrapt.wrap_function_wrapper(target_obj, attribute_name, wrapper)
        self._wrapped_targets.append((target_obj, attribute_name, original))

    def unwrap_all(self) -> None:
        """Restores all wrapped targets to their original states in reverse order."""
        while self._wrapped_targets:
            target_obj, attr_name, original = self._wrapped_targets.pop()
            current = getattr(target_obj, attr_name, None)
            if hasattr(current, "__wrapped__"):
                setattr(target_obj, attr_name, current.__wrapped__)
            elif original is not None:
                setattr(target_obj, attr_name, original)
```

**Why this design is elegant**:
- Any integration only needs to define `is_installed()` and `_apply_patch()`.
- Unwrapping is handled automatically by `unwrap_all()` by walking backward through `self._wrapped_targets`.
- It supports string target names (e.g. `"django.views.generic.base.View"`) with lazy import resolution.

### The `IntegrationManager` Registry (`manager.py`)

`IntegrationManager` coordinates the lifecycle of all integrations:
- Maintains a registry of built-in integrations and aliases (`postgresql` -> `postgres`, `boto3` -> `boto`).
- `apply_integrations(config)` checks which libraries are installed (`instance.is_installed()`) and activates them idempotently.
- `uninstrument_all()` allows unit tests to cleanly reset the entire runtime.

---

## 6. Deep-Dive into Built-in Integrations

Now let's examine how each built-in integration is implemented.

---

### Django Integration

TraceNest's Django integration (`src/tracenest/integrations/django/`) orchestrates 6 distinct layers:

```text
HTTP SERVER (django.request)  [request.py]
  ├── ⚙️ SecurityMiddleware.__call__  [middleware.py]
  ├── ⚙️ CommonMiddleware.__call__
  ├── ⚙️ CsrfViewMiddleware.__call__
  │    └── process_view
  ├── 🐍 ProductViewSet.list  [view.py]
  │    ├── 🔵 SELECT * FROM api_product  [postgres/cursor.py]
  │    └── 🔴 GET cache:product_list  [cache.py]
  ├── 🎨 django.template: catalog/list.html  [template.py]
  └── ⚙️ XFrameOptionsMiddleware.process_response
```

#### 1. Request Handler (`request.py`)
Intercepts `django.core.handlers.base.BaseHandler.get_response`:
- **Span Name**: `django.request` with `SpanKind.SERVER`.
- **W3C Context Extraction**: Inspects `request.META` (or `request.headers`) for `HTTP_TRACEPARENT`, calling `opentelemetry.propagate.extract()`.
- **Route Normalization**:
  If a user visits `/api/products/42/`, Django's URL resolver matches `path('api/products/<int:id>/', ...)`.
  TraceNest extracts `route = "/api/products/<id>/"` from `request.resolver_match`.
  It records:
  - `url.full = "http://example.com/api/products/42/"` (Full detail for trace inspection)
  - `http.route = "/api/products/<id>/"` (Low cardinality for Prometheus aggregation)
- **Response Headers**: Injects `X-Trace-ID`, `X-Span-ID`, and `traceparent` onto the outgoing HTTP response object for frontend-to-backend correlation.

#### 2. Middleware Waterfall (`middleware.py`)
Standard OpenTelemetry does not trace middleware. TraceNest intercepts `BaseHandler.load_middleware()`:
```python
def make_traced_load_middleware(integration):
    def traced_load_middleware(wrapped, instance, args, kwargs):
        result = wrapped(*args, **kwargs)
        middleware_list = getattr(settings, "MIDDLEWARE", [])
        for middleware_path in middleware_list:
            middleware_cls = import_string(middleware_path)
            for hook in ("__call__", "process_request", "process_view", "process_response", "process_exception"):
                if hasattr(middleware_cls, hook):
                    integration.wrap(middleware_cls, hook, _make_hook_wrapper(...))
        return result
```
- Each middleware hook creates a span: `⚙️ <MiddlewareName>.<hook>` with `SpanKind.INTERNAL`.
- Supports asynchronous middleware: if `inspect.iscoroutinefunction(wrapped_call)` is true, it awaits the coroutine inside the span so the duration measures the true asynchronous completion.

#### 3. View Dispatch (`view.py`)
Intercepts `View.setup`, `View.dispatch`, and Django REST Framework's `APIView.dispatch`:
- Resolves class names and action names (e.g., `ProductViewSet.list`).
- Names the span: `🐍 django.view.<ViewName>.<action>`.

#### 4. Template Rendering (`template.py`)
Intercepts `django.template.base.Template.render`:
- Evaluates exclude patterns (`django/forms/*`, `debug_toolbar/*`, `*/widgets/*`) via `fnmatch` to prevent form partials from bloating the span tree.
- Uses a `ContextVar[bool]` (`_in_template_span`) to control whether nested template includes should be traced or folded.
- Names the span: `🎨 django.template: <template_name>`.

#### 5. Cache Backend (`cache.py`)
Intercepts Django's cache backends (`BaseCache`, `django_redis`, `DefaultClient`):
- Traces `get`, `set`, `delete`, `get_many`, `set_many`, `incr`, `decr`.
- On `get()` calls, inspects the return value to record `django.cache.hit = True` or `False`.

#### 6. Authentication (`auth.py`)
Intercepts `django.contrib.auth.login` and `authenticate`:
- Tags `usr.id`, `usr.username`, and `usr.email` on the current active span.
- Redacts password parameters completely.

---

### PostgreSQL & PgBouncer Integration

Implemented in `src/tracenest/integrations/postgres/`.

```text
CursorWrapper.execute(sql)
            │
Sanitize SQL & Extract Operation
            │
Check PgBouncer (Host == "pgbouncer" or Port == 6432)
            │
   ┌────────┴────────┐
   ▼                 ▼
[PgBouncer]      [Direct Postgres]
🔵 INSERT ...     🐘 SELECT ...
   │                 │
   └────────┬────────┘
            │
with suppress_db_instrumentation():
   wrapped_cursor.execute(sql)  <-- Psycopg2 driver span is suppressed!
```

#### Solving the Duplicate Span Problem
When Django's `CursorWrapper` executes a query, it calls the underlying `psycopg2.cursor.execute()`. If the official `Psycopg2Instrumentor` is active, it would generate an unwanted child span.

TraceNest suppresses this using OpenTelemetry's internal context token:

```python
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
Inside `traced_django_cursor_exec`:
```python
with suppress_db_instrumentation():
    result = wrapped(*args, **kwargs)
```
This guarantees that **exactly one span** is emitted per query.

#### PgBouncer Pool Detection & Replica Tagging
In `cursor.py`, `_extract_django_db_meta()` inspects the Django connection:
```python
def is_pgbouncer_connection(db_host: Any, db_port: Any) -> bool:
    host_str = str(db_host).lower() if db_host else ""
    port_num = int(db_port) if db_port else 0
    return host_str == "pgbouncer" or port_num == 6432 or "pgbouncer" in host_str
```
- If true: sets `peer.service = "pgbouncer"`, `db.connection.pool = "pgbouncer"`, and icon `🔵`.
- If false: sets `peer.service = "postgres"`, and icon `🐘`.
- If the Django connection alias contains `slave`, `replica`, or `read`, it tags `db.role = "replica"`; otherwise `db.role = "primary"`.

---

### Redis Integration

Implemented in `src/tracenest/integrations/redis/client.py`.

#### Command Sanitization & IP Masking
Redis keys often contain sensitive user IDs, tokens, or IP addresses (e.g. rate limiter keys: `throttle_burst:192.168.1.50`).

TraceNest masks IP addresses using regex:
```python
_IPV4_RE = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
_IPV6_RE = re.compile(r"(?<![0-9a-fA-F:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}(?![0-9a-fA-F:])")

def _sanitize_string(s: str) -> str:
    s = _IPV4_RE.sub("?", s)
    s = _IPV6_RE.sub("?", s)
    return s
```
It redacts sensitive commands completely:
- `AUTH <password>` -> `AUTH ?`
- `CONFIG SET masterauth <secret>` -> `CONFIG SET ?`

#### Pipeline Support
When executing a pipeline containing 15 commands:
- Names the span: `🔴 PIPELINE (15 commands): GET, SET, INCR, ...`
- Sets `db.redis.pipeline_length = 15`.

#### Async & Sync Support
Intercepts both synchronous `redis.Redis.execute_command` and asynchronous `redis.asyncio.Redis.execute_command`.

---

### Outgoing HTTP Requests

Implemented in `src/tracenest/integrations/requests/`.

Wraps OpenTelemetry's `RequestsInstrumentor` with custom hooks:
1. **`tracenest_request_hook`**:
   - Sanitizes outgoing target URLs.
   - Names the span: `🌐 HTTP <METHOD> <HOSTNAME>` (e.g. `🌐 HTTP GET api.stripe.com`).
   - Sets standard attributes: `http.request.method`, `server.address`, `server.port`, `peer.service`.
2. **`tracenest_response_hook`**:
   - Records `http.response.status_code`.
   - If status code >= 400, sets `error = True`, `error.type = "HTTP404"`, and marks span status as `StatusCode.ERROR`.
3. **OTLP Self-Tracing Prevention Guard (`_is_telemetry_request`)**:
   ```python
   def _is_telemetry_request(sanitized_url: str, hostname: str, port: int) -> bool:
       if "otel-collector" in hostname or "tp-otel-collector" in hostname:
           return True
       if port in (4317, 4318) and hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
           return True
       return False
   ```
   If an outgoing HTTP request targets the OTel Collector endpoint, tracing is suppressed, terminating any recursive feedback loops.

---

### AWS / Boto Integration

Implemented in `src/tracenest/integrations/boto/`.

Auto-instruments `botocore` using OpenTelemetry's `BotocoreInstrumentor`:
- Automatically traces Amazon S3, SQS, DynamoDB, SNS, SES, KMS, and third-party S3-compatible object stores (Cloudflare R2, MinIO, Wasabi, LocalStack).
- Records `rpc.system = "aws"`, `rpc.service`, `rpc.method` (e.g. `GetObject`, `PutObject`).

---

## 7. Visual Badges & Span Naming Reference

TraceNest enforces a standardized visual icon badge system. In tools like Grafana Tempo and SigNoz, developers can scan waterfalls without opening span details:

| Icon | Component / Layer | Span Name Example | Span Kind | Attributes Recorded |
| :---: | :--- | :--- | :---: | :--- |
| *(root)* | **Django Request** | `django.request` | `SERVER` | `http.route`, `http.status_code`, `url.full` |
| ⚙️ | **Django Middleware** | `⚙️ SecurityMiddleware.__call__` | `INTERNAL` | `django.middleware.name`, `django.middleware.method` |
| 🐍 | **Django View** | `🐍 ProductViewSet.list` | `INTERNAL` | `django.view.name`, `http.route` |
| 🎨 | **Django Template** | `🎨 django.template: catalog.html`| `INTERNAL` | `django.template.name` |
| 🔵 | **PgBouncer Pool** | `🔵 INSERT INTO "cart" ...` | `CLIENT` | `peer.service="pgbouncer"`, `db.connection.pool="pgbouncer"` |
| 🐘 | **Direct Postgres** | `🐘 SELECT * FROM "users" ...` | `CLIENT` | `peer.service="postgres"`, `db.role="primary"` |
| 🔴 | **Redis Command** | `🔴 GET session:1001` | `CLIENT` | `db.system="redis"`, `db.operation="GET"` |
| 🔴 | **Redis Pipeline** | `🔴 PIPELINE (3 commands): ...` | `CLIENT` | `db.redis.pipeline_length=3` |
| 🌐 | **Outgoing HTTP** | `🌐 HTTP GET api.stripe.com` | `CLIENT` | `server.address="api.stripe.com"`, `http.status_code` |
| 🔐 | **Django Auth** | `🔐 django.auth.login` | `INTERNAL` | `usr.id`, `usr.username`, `usr.email` |
| ☁️ | **AWS / Cloud SDK** | `☁️ s3:GetObject bucket-name` | `CLIENT` | `rpc.system="aws"`, `rpc.service="s3"` |

---

## 8. OpenTelemetry Collector & Metrics Pipeline

TraceNest exports OTLP trace data to an **OpenTelemetry Collector Contrib** instance. 

The Collector transforms raw trace spans into Prometheus RED metrics in real time using the **`spanmetrics` connector**:

```text
TraceNest SDK
     │ (OTLP Protobuf over HTTP /v1/traces)
     ▼
OTel Collector Receiver (:4318)
     │
     ├───> Processors (Batch)
     │          │
     │          ├───> Exporter: Tempo (:4317) ──> Grafana Trace View
     │          │
     │          └───> Connector: SpanMetrics
     │                     │ (Derives request count, errors, and duration histograms)
     │                     ▼
     └──────> Exporter: Prometheus (:8889) ───> Grafana APM Dashboards
```

### Why SpanMetrics is Powerful
The application does not need to maintain separate metric counters. When TraceNest emits a `django.request` or database span, `spanmetrics` automatically generates:
- `traces_spanmetrics_calls_total{http_route="/api/products/<id>/", http_status_code="200"}`
- `traces_spanmetrics_latency_bucket{http_route="/api/products/<id>/", le="0.05"}`

This produces complete RED (Rate, Errors, Duration) dashboards with zero application overhead.

> [!TIP]
> For a full tutorial on writing PromQL queries against `apm_calls_total` and `apm_duration_milliseconds_bucket`, configuring Grafana panels, and linking metric spikes directly to Tempo trace waterfalls, read the [**Prometheus, PromQL & Grafana Dashboard Guide**](file:///home/kaizen/workspace/professional/OTEL-SDK/docs/PROMQL_AND_GRAFANA_DASHBOARDS_GUIDE.md).

---

## 9. Step-by-Step Guide: How to Build a New Instrumentation

If you need to add a new library instrumentation to TraceNest (e.g. Celery, Elasticsearch, RabbitMQ, PyMongo, or FastKafka), follow this exact step-by-step blueprint.

### Architectural Checklist

Before writing code, answer these 6 questions:
1. **Target Library**: What package needs instrumentation (e.g. `celery`)?
2. **Seam / Interception Point**: Which exact class and method is invoked on every operation (e.g. `celery.app.task.Task.__call__`)?
3. **Span Kind**: Is this `CLIENT` (outbound call), `SERVER` (handling an incoming task), or `INTERNAL`?
4. **Context Propagation**: Does this library cross network/process boundaries? If yes, how do we inject and extract W3C `traceparent` headers?
5. **Re-entrancy Risk**: Can the wrapped method invoke other instrumented methods recursively? If yes, attach `reentrant_guard`.
6. **Visual Badge**: What emoji badge represents this component (e.g. `🥬` for Celery, `📦` for Mongo)?

---

### Step 1: Identify Targets and Seams
Find where the work happens. For example, in a database or message queue client, find the `execute()` or `send()` method.

### Step 2: Subclass `BaseIntegration`
Create a new file in `src/tracenest/integrations/<name>/integration.py`:

```python
from tracenest.integrations.base import BaseIntegration

class MyCustomIntegration(BaseIntegration):
    name = "mycustom"

    def is_installed(self) -> bool:
        try:
            import mycustom_library
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        self.wrap(
            "mycustom_library.client.Client",
            "execute_operation",
            traced_mycustom_operation,
        )
```

### Step 3: Implement Re-entrancy & Tracing Wrappers
Write your wrapper function using `traced_span` and `reentrant_guard`:

```python
from opentelemetry.trace import SpanKind
from tracenest.tracing import reentrant_guard, traced_span

def traced_mycustom_operation(wrapped, instance, args, kwargs):
    with reentrant_guard(instance, "_tp_in_custom_op") as should_trace:
        if not should_trace:
            return wrapped(*args, **kwargs)

        op_name = args[0] if args else "operation"
        span_name = f"📦 mycustom.{op_name}"
        span_attrs = {
            "component": "mycustom",
            "mycustom.op": str(op_name),
        }

        with traced_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=span_attrs,
            tracer_name="tracenest.mycustom",
        ):
            return wrapped(*args, **kwargs)
```

### Step 4: Register with `IntegrationManager`
Open `src/tracenest/integrations/manager.py` and register the new class in `_BUILTIN_INTEGRATIONS`:

```python
_BUILTIN_INTEGRATIONS: Dict[str, str] = {
    # ... existing integrations ...
    "mycustom": "tracenest.integrations.mycustom.MyCustomIntegration",
}
```
Add any convenient aliases in `_INTEGRATION_ALIASES`:
```python
_INTEGRATION_ALIASES: Dict[str, str] = {
    # ...
    "customlib": "mycustom",
}
```

### Step 5: Write Offline Tests with `InMemorySpanExporter`
Every integration should have 100% offline unit tests verifying:
- Spans are generated with the correct name and attributes.
- Exceptions are caught and marked as `ERROR`.
- Uninstrumentation cleanly restores original behavior.

---

## 10. Fully Worked Example: Building a Celery Task Integration

Let's walk through building a complete, real-world **Celery Task Instrumentation** from scratch.

### The Objective
When a background Celery worker executes a task:
1. Extract the incoming W3C `traceparent` from Celery message headers so it links to the web request that enqueued it.
2. Create a `CONSUMER` root span named `🥬 celery.task: <task_name>`.
3. Record task ID, retry counts, and routing keys.
4. Record exceptions if the task crashes.

### Step 1: Create the Directory Structure
```text
src/tracenest/integrations/celery/
├── __init__.py
└── integration.py
```

### Step 2: Implement `integration.py`

```python
"""Celery integration for TraceNest SDK."""

import importlib
import logging
from typing import Any, Callable, Dict, Optional

from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, StatusCode, get_tracer
from tracenest.config import SDKConfig
from tracenest.integrations.base import BaseIntegration
from tracenest.tracing import reentrant_guard, traced_span

logger = logging.getLogger("tracenest.integrations.celery")


def traced_task_call(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    """Wraps Celery Task.__call__ to trace worker task execution."""
    with reentrant_guard(instance, "_tp_task_executing") as should_trace:
        if not should_trace:
            return wrapped(*args, **kwargs)

        task_name = getattr(instance, "name", instance.__class__.__name__)
        request = getattr(instance, "request", None)
        
        # 1. Extract W3C traceparent headers passed from Celery producer
        headers: Dict[str, str] = {}
        if request and hasattr(request, "headers") and isinstance(request.headers, dict):
            headers = {str(k).lower(): str(v) for k, v in request.headers.items()}
        parent_ctx = extract(headers)

        task_id = str(getattr(request, "id", "")) if request else ""
        retries = int(getattr(request, "retries", 0)) if request else 0

        span_name = f"🥬 celery.task: {task_name}"
        span_attrs = {
            "component": "celery",
            "messaging.system": "celery",
            "messaging.operation": "process",
            "celery.task.name": task_name,
            "celery.task.id": task_id,
            "celery.task.retries": retries,
            "resource.name": task_name,
        }

        with traced_span(
            span_name,
            kind=SpanKind.CONSUMER,
            attributes=span_attrs,
            tracer_name="tracenest.celery",
            context=parent_ctx,
        ) as span:
            return wrapped(*args, **kwargs)


class CeleryIntegration(BaseIntegration):
    """Distributed tracing for Celery background tasks."""

    name = "celery"

    def is_installed(self) -> bool:
        try:
            importlib.import_module("celery")
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        try:
            from celery.app.task import Task
            self.wrap(Task, "__call__", traced_task_call)
            logger.debug("Successfully instrumented Celery Task.__call__")
        except Exception as exc:
            logger.debug("Celery Task patch skipped: %s", exc)
```

### Step 3: Register in `manager.py`
Add to `_BUILTIN_INTEGRATIONS`:
```python
_BUILTIN_INTEGRATIONS = {
    # ...
    "celery": "tracenest.integrations.celery.CeleryIntegration",
}
```

### Step 4: Write the Unit Test (`tests/test_celery.py`)

```python
import pytest
from unittest.mock import MagicMock
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import tracenest
from tracenest.integrations.celery.integration import CeleryIntegration

def test_celery_task_tracing():
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    
    # Initialize with mock exporter
    tracenest.init(service="test-celery", exporter=exporter, span_processor=SimpleSpanProcessor(exporter))

    # Create dummy Celery Task class
    class FakeTask:
        name = "tasks.send_email"
        request = MagicMock(id="task-12345", retries=0, headers={})

        def __call__(self, recipient, subject):
            return "Email sent to " + recipient

    task = FakeTask()
    integration = CeleryIntegration()
    integration.wrap(FakeTask, "__call__", tracenest.integrations.celery.integration.traced_task_call)

    # Execute task
    result = task("user@example.com", "Welcome!")
    assert result == "Email sent to user@example.com"

    # Assert span created
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "🥬 celery.task: tasks.send_email"
    assert span.attributes["messaging.system"] == "celery"
    assert span.attributes["celery.task.id"] == "task-12345"

    integration.unwrap_all()
    tracenest._reset_for_testing()
```

That's all it takes. Any developer can build, test, and ship a production-grade instrumentation in under an hour!

---

## 11. Testing & Verification Architecture

TraceNest is built with **100% offline unit-testability**. You never need a live Docker container, Postgres database, or OTel Collector to test the SDK.

### The `InMemorySpanExporter` Pattern
In unit tests, we pass OpenTelemetry's `InMemorySpanExporter`:

```python
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

def test_something():
    exporter = InMemorySpanExporter()
    tracenest.init(service="test", exporter=exporter, export_batch=False)
    
    # Run code that generates spans ...

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "expected_span_name"
```

### The Test Reset Fixture (`_reset_for_testing()`)
Because TraceNest is a global singleton, tests must restore global state before and after execution:
```python
def setup_function():
    tracenest._reset_for_testing()

def teardown_function():
    tracenest._reset_for_testing()
```
`_reset_for_testing()`:
1. Shuts down the active `TracerProvider`.
2. Uninstruments all active monkey-patches via `manager.uninstrument_all()`.
3. Resets OpenTelemetry's private `_TRACER_PROVIDER` global.

---

## 12. Common Pitfalls & Troubleshooting Cheatsheet

### 1. Spans Not Showing Up in the Collector
- **Check Sample Rate**: Is `sample_rate=0.0` or `TRACENEST_SAMPLE_RATE=0` set?
- **Check Ignore Endpoints**: Is the request path matching an ignore pattern (e.g. `/health*`)?
- **Collector Endpoint Port**: OTLP HTTP uses port **4318**, while gRPC uses port **4317**. If you configure `http://localhost:4317` with an HTTP exporter, it will fail. TraceNest defaults to `http://localhost:4318`.

### 2. Missing Child Spans (Tree is Incomplete)
- **Check Re-entrancy Guards**: If you placed a re-entrancy guard on a class instead of an instance, or didn't reset it on exceptions, subsequent calls will be ignored. Always use `tracenest.tracing.reentrant_guard`.

### 3. Duplicate Spans Appearing
- **Check Driver vs High-Level Wrappers**: If both `CursorWrapper` and `Psycopg2Instrumentor` run without `suppress_db_instrumentation()`, two spans are created. Wrap inner execution in `with suppress_db_instrumentation():`.

### 4. Prometheus Metric High Cardinality
- **Check Route Normalization**: Ensure your spans set `http.route` using normalized patterns (e.g. `/items/<id>/`), never raw query paths (`/items/1293812/`).

---

## Conclusion & Next Steps

TraceNest bridges the gap between OpenTelemetry's vendor-neutral foundation and Datadog's developer-friendly APM visual experience. 

With this guide, you now understand:
- How the configuration and bootstrap pipelines operate.
- How `SafeSpanExporter` protects host applications from telemetry crashes.
- How monkey-patching with `wrapt` guarantees safe function interception and clean teardown.
- How the built-in Django, PostgreSQL, Redis, and Requests integrations work under the hood.
- How to implement and verify any new integration from scratch.

Happy instrumenting!
