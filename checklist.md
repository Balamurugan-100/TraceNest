# TraceNest SDK v3 — Observability Requirements Checklist

This document tracks the status of all application-level, service-level, and request-level observability requirements specified for **TraceNest SDK v3**.

## Summary Table

| Requirement Category | Total Items | Done | Status |
| :--- | :---: | :---: | :---: |
| [1. Core Metrics and Visibility](#1-core-metrics-and-visibility) | 7 | 7 | ✅ Complete |
| [2. Application and Service Dashboard](#2-application-and-service-dashboard) | 3 | 3 | ✅ Complete |
| [3. Service-Level Details](#3-service-level-details) | 4 | 4 | ✅ Complete |
| [4. Endpoint and Operation Instances](#4-endpoint-and-operation-instances) | 2 | 2 | ✅ Complete |
| [5. Request Trace and Waterfall](#5-request-trace-and-waterfall) | 2 | 2 | ✅ Complete |
| [6. Service Performance Over Time](#6-service-performance-over-time) | 2 | 2 | ✅ Complete |
| [7. Time-Period Comparison](#7-time-period-comparison) | 2 | 2 | ✅ Complete |
| [8. Sampling and Retention](#8-sampling-and-retention) | 2 | 2 | ℹ️ Out of PoC Scope (SDK Implemented) |
| [9. Overall User Flow](#9-overall-user-flow) | 1 | 1 | ✅ Complete |

---

## Detailed Requirements Breakdown

### 1. Core Metrics and Visibility

- [x] **Application & service-level latency, throughput, and error metrics**
  - *Details*: OpenTelemetry metrics (`http.server.requests`, `http.server.errors`, `http.server.request.duration`, `apm_calls_total`, `apm_calls_duration_seconds_bucket`) exported via SDK OTLP HTTP exporter and OpenTelemetry Collector `spanmetrics` connector.
- [x] **Endpoint-level request volume, throughput, latency, and error analysis**
  - *Details*: Low-cardinality URL route normalization (`_normalize_route`) in Django integration (`src/tracenest/integrations/django/request.py`), aggregated per endpoint path in Prometheus and displayed on `tracenest_django_overview` and `tracenest_django_endpoint_details`.
- [x] **Visibility into key services**:
  - [x] **Django application server**: Request handlers (`django.request`), middleware (`⚙️ django.middleware.*`), views (`🐍 django.view.*`), templates (`🎨 django.template:*`), cache operations (`django.cache.*`), and auth events (`🔐 django.auth.*`).
  - [x] **PostgreSQL primary database**: Driver cursor wrapping, sanitized SQL queries, `db.role="primary"`, PgBouncer connection pool topology detection (`🔵`).
  - [x] **PostgreSQL replica databases**: Replica server topology detection (`postgres-replica1`, `postgres-replica2`, `slave1`, `slave2`) with `db.role="replica"` tagging (`🐘`).
  - [x] **Redis**: Command formatting (`GET`, `SET`, `INCR`), pipeline execution tracing, sensitive argument redacting (`AUTH`, `CONFIG`), and IP address masking (`🔴`).
  - [x] **External APIs**: Outgoing HTTP request tracing (`requests` library integration) producing `🌐 HTTP <METHOD> <HOST>` client spans with full URL and status codes.
- [x] **Distributed tracing for individual requests**
  - *Details*: W3C `traceparent` header propagation across incoming HTTP headers, root `SERVER` span `django.request`, child span context propagation, and `X-Trace-ID`/`X-Span-ID` response headers.
- [x] **Span waterfall visualization for individual requests**
  - *Details*: Grafana Tempo datasource rendering complete nested execution trees (middleware, views, templates, SQL queries, Redis ops, HTTP calls).
- [x] **Filtering by application, service, endpoint, environment, status, and time range**
  - *Details*: Grafana dashboard template variables (`$project`, `$cluster`, `$server_location`, `$environment`, `$service`, `$endpoint`, `$query`, `$command`) + Grafana native time-range picker.
- [x] **Performance comparison across different time periods**
  - *Details*: 7-day median rolling baseline queries (`quantile_over_time(0.5, apm_calls_total[7d:1h])`), current 5m rate/latency comparison, deviation percentage calculation, and anomaly detection rules in `infra/prometheus/rules/baseline.yml`.

---

### 2. Application and Service Dashboard

- [x] **Main dashboard providing an overview of overall application & per-service performance**
  - *Details*: `docker/grafana/dashboards/tracenest_service_catalog.json` ("TraceNest APM — Service Catalog").
- [x] **Per-service metrics summary table** (Service | Request Rate | Latency | Error Rate)
  - *Details*: "Installed Components Matrix" and "Throughput Across Components" panels in `tracenest_service_catalog.json` covering Django, Postgres Primary, Postgres Replicas, Redis, and External APIs.
- [x] **Easy identification of bottleneck / service causing performance degradation**
  - *Details*: "% Time Spent by Downstream Service" breakdown panel, RPS anomaly alerts, and cross-service latency metrics.

---

### 3. Service-Level Details

- [x] **Dedicated service pages accessible via service selection**
  - *Details*: Interactive links from Service Catalog dashboard (`tracenest_service_catalog`) linking directly to `/d/tracenest-django-overview`, `/d/tracenest-postgres-overview`, and `/d/tracenest-redis-overview`.
- [x] **Django Service Page**
  - *Details*: Displays URL endpoints table with Requests, Error Rate, Throughput, P50/P95/P99 Latency (`tracenest_django_overview.json`).
- [x] **PostgreSQL Service Page**
  - *Details*: Displays database queries, query volume, query latency (P50/P95/P99), error rate, database instance (Primary vs Replicas / PgBouncer), and performance over time (`tracenest_postgres_overview.json`).
- [x] **Redis Service Page**
  - *Details*: Displays Redis operations (`GET`, `SET`, `DELETE`, etc.), operation volume, latency, error rate, and command performance over time (`tracenest_redis_overview.json`).

---

### 4. Endpoint and Operation Instances

- [x] **Instance list for selected endpoint, query, or Redis operation**
  - *Details*: Dedicated details dashboards (`tracenest_django_endpoint_details.json`, `tracenest_postgres_query_details.json`, `tracenest_redis_command_details.json`) featuring embedded Tempo trace search panels ("Recent Traces & Flamegraph Waterfall for $endpoint / $query / $command").
- [x] **Information to identify and investigate specific request/operation instances**
  - *Details*: Tempo trace list displaying trace IDs, duration, start timestamp, status code, and direct drill-down links ("🔥 Open Flamegraph Waterfall in Tempo").

---

### 5. Request Trace and Waterfall

- [x] **Complete trace and span waterfall for selected request instance**
  - *Details*: Tempo trace waterfall in Grafana rendering parent-child relationships and operation durations.
- [x] **Detailed breakdown of operations in waterfall**:
  - [x] **Django middleware**: `⚙️ django.middleware.<name>` spans showing exact middleware duration.
  - [x] **View & application functions**: `🐍 django.view.<ViewClass>.<method>` spans.
  - [x] **Template rendering**: `🎨 django.template: <name>` spans.
  - [x] **PostgreSQL queries**: `🐘 SELECT/INSERT` or `🔵` PgBouncer spans with sanitized SQL.
  - [x] **Redis operations**: `🔴 GET/SET/PIPELINE` spans with command parameters.
  - [x] **External API calls**: `🌐 HTTP GET/POST <host>` spans.
  - [x] **Execution duration**: Inclusive/exclusive execution duration recorded for every span.

---

### 6. Service Performance Over Time

- [x] **Time-series graphs for performance over selected time ranges**
  - *Details*: Time-series panels for request volume, P50/P75/P90/P95/P99 latency percentiles, and error rate over time on Django, Postgres, and Redis dashboards.
- [x] **Answering "Why is the application slow?" (Root Cause Isolation)**
  - *Details*: Downstream service latency breakdown panels ("% Time Spent by Downstream Service"), throughput comparison across components, and RPS baseline anomaly detection rules.

---

### 7. Time-Period Comparison

- [x] **Compare performance across different time periods** (Today vs. previous day, This week vs. previous week, Before vs. after deployment)
  - *Details*: Prometheus 7-day median rolling baseline rules (`quantile_over_time(0.5, apm_calls_total[7d:1h])`) compared against 5m live rate, deviation percentage calculation, and anomaly panels in `tracenest_service_catalog.json`.
- [x] **Identify changes in Throughput, Latency, Error Rate, Request Volume**
  - *Details*: Global RPS anomaly list and anomaly threshold alerts (>50% anomaly, >200% severe anomaly).

---

### 8. Sampling and Retention

- [x] **Configurable trace sampling**
  - *Details*: Configurable `sample_rate` parameter in `tracenest.init()` and `TRACENEST_SAMPLE_RATE` / `OTEL_TRACES_SAMPLER_ARG` env vars using OpenTelemetry `TraceIdRatioBased` sampler. *(Full policy management deferred beyond PoC scope per requirement #8)*.
- [x] **Data retention**
  - *Details*: Managed at backend storage tier (Tempo block retention & Prometheus TSDB retention config). *(Deferred beyond PoC scope per requirement #8)*.

---

### 9. Overall User Flow

- [x] **End-to-End Investigation Flow**: Application Dashboard → Select Service → Service Dashboard → Select Endpoint / Query / Operation → Instance List → Select Instance → Trace Waterfall → Root Cause.
  - *Details*: Fully wired navigation path across Grafana dashboards:
    1. `tracenest_service_catalog` (Application & Service Catalog)
    2. Click component link → `tracenest_django_overview` / `tracenest_postgres_overview` / `tracenest_redis_overview`
    3. Click endpoint/query/command → `tracenest_django_endpoint_details` / `tracenest_postgres_query_details` / `tracenest_redis_command_details`
    4. Click trace instance → Tempo Trace Waterfall in Grafana.
