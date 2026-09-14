# Prometheus, PromQL & Grafana Dashboard Guide for TraceNest APM

> **Target Audience**: This document is written for developers who want to understand how trace data is converted into Prometheus metrics, master PromQL from first principles, and build custom, production-grade Grafana dashboards with seamless drill-downs into Tempo traces.

---

## Table of Contents

1. [Understanding the Metrics Pipeline: From Spans to Timeseries](#1-understanding-the-metrics-pipeline-from-spans-to-timeseries)
   - [How the OpenTelemetry Collector SpanMetrics Connector Works](#how-the-opentelemetry-collector-spanmetrics-connector-works)
   - [The Metrics Produced by SpanMetrics](#the-metrics-produced-by-spanmetrics)
   - [Attribute-to-Label Mapping](#attribute-to-label-mapping)
2. [PromQL Foundations from First Principles](#2-promql-foundations-from-first-principles)
   - [The Data Model: Metric Name, Labels, and Samples](#the-data-model-metric-name-labels-and-samples)
   - [Metric Types: Counters, Gauges, and Histograms](#metric-types-counters-gauges-and-histograms)
   - [Instant Vectors vs. Range Vectors](#instant-vectors-vs-range-vectors)
   - [The Golden Rule: Understanding `rate()` and `irate()`](#the-golden-rule-understanding-rate-and-irate)
   - [Aggregations: `sum()`, `by()`, and `without()`](#aggregations-sum-by-and-without)
   - [Calculating Latency Percentiles with `histogram_quantile()`](#calculating-latency-percentiles-with-histogram_quantile)
   - [Mathematical Operators & Ratio Calculations](#mathematical-operators--ratio-calculations)
3. [The Complete APM PromQL Cookbook](#3-the-complete-apm-promql-cookbook)
   - [1. HTTP Request Rate (Throughput / RPS)](#1-http-request-rate-throughput--rps)
   - [2. Error Rate & Error Percentage](#2-error-rate--error-percentage)
   - [3. Latency Percentiles (p50, p95, p99)](#3-latency-percentiles-p50-p95-p99)
   - [4. Top Slowest Endpoints Table](#4-top-slowest-endpoints-table)
   - [5. PostgreSQL Database Query Performance](#5-postgresql-database-query-performance)
   - [6. PgBouncer Pool Saturation & Client Queues](#6-pgbouncer-pool-saturation--client-queues)
   - [7. Redis Command Throughput & Cache Hit Ratio](#7-redis-command-throughput--cache-hit-ratio)
   - [8. Outbound HTTP / External Dependency Latency](#8-outbound-http--external-dependency-latency)
4. [How Grafana Dashboards Are Built](#4-how-grafana-dashboards-are-built)
   - [Grafana Architecture in TraceNest](#grafana-architecture-in-tracenest)
   - [Dashboard Variables (Templating for Dynamic Filtering)](#dashboard-variables-templating-for-dynamic-filtering)
   - [Panel Types and Visual Styling](#panel-types-and-visual-styling)
   - [The Superpower: Linking Metrics to Tempo Traces (TracesToMetrics & Exemplars)](#the-superpower-linking-metrics-to-tempo-traces-tracestometrics--exemplars)
5. [Step-by-Step: How to Build Your Own Dashboard from Scratch](#5-step-by-step-how-to-build-your-own-dashboard-from-scratch)
   - [Method A: Visual Builder in the Grafana UI](#method-a-visual-builder-in-the-grafana-ui)
   - [Method B: Declarative JSON & Provisioning (Infrastructure as Code)](#method-b-declarative-json--provisioning-infrastructure-as-code)
6. [Complete Production-Ready Dashboard JSON Template](#6-complete-production-ready-dashboard-json-template)
7. [Common Pitfalls & PromQL Troubleshooting](#7-common-pitfalls--promql-troubleshooting)

---

## 1. Understanding the Metrics Pipeline: From Spans to Timeseries

Before writing a single PromQL query, you must understand where the metrics come from. TraceNest does **not** maintain internal counter dictionaries or push metrics directly to Prometheus. Instead, it relies on a high-throughput architectural pattern: **Span-Derived Metrics via the OpenTelemetry Collector**.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                          TraceNest Python App                          │
│                                                                        │
│   Generates Traces & Spans:                                            │
│   - django.request (route="/api/products/<id>/", status=200, dur=45ms)  │
│   - 🔵 SELECT * FROM api_product (operation=SELECT, dur=8ms)          │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ OTLP Protobuf over HTTP (:4318)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   OpenTelemetry Collector Contrib                      │
│                                                                        │
│   Receives Trace Spans ───────────────────┐                            │
│                                           │                            │
│                                           ▼                            │
│                        ┌─────────────────────────────────────┐         │
│                        │       SpanMetrics Connector         │         │
│                        │                                     │         │
│                        │ Aggregates Spans into Histograms    │         │
│                        │ and Counters in Memory Every Second │         │
│                        └──────────────────┬──────────────────┘         │
│                                           │                            │
│                                           ▼                            │
│                        ┌─────────────────────────────────────┐         │
│                        │         Prometheus Exporter         │         │
│                        │       (Namespace: "apm", :8889)     │         │
│                        └──────────────────┬──────────────────┘         │
└───────────────────────────────────────────┼────────────────────────────┘
                                            │ Prometheus Scrapes (:8889)
                                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          Prometheus Database                           │
│                                                                        │
│   Stores Timeseries:                                                   │
│   - apm_calls_total{http_route="/api/products/<id>/", status_code="200"}│
│   - apm_duration_milliseconds_bucket{http_route="...", le="50"}        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ PromQL Queries
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          Grafana Dashboards                            │
│                                                                        │
│   Visualizes Throughput (RPS), Error Rates (%), Latency P95 (ms)       │
└────────────────────────────────────────────────────────────────────────┘
```

### How the OpenTelemetry Collector SpanMetrics Connector Works

In `docker/otel-collector/otel-collector-config.yaml`, we define the `spanmetrics` connector:

```yaml
connectors:
  spanmetrics:
    histogram:
      explicit:
        # Predefined latency buckets in milliseconds
        buckets: [2ms, 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s]
    dimensions:
      - name: http.method
      - name: http.status_code
      - name: http.route
      - name: db.system
      - name: db.operation
      - name: db.query.summary
      - name: db.connection.pool
      - name: peer.service
      - name: error
      - name: django.view
      - name: django.cache.hit

exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    namespace: apm
```

Whenever a span finishes in the Python app, the SDK sends it to the collector. The `spanmetrics` connector inspects each span:
1. Reads the configured `dimensions` (span attributes).
2. Increments the request counter (`apm_calls_total`).
3. Sorts the span's duration into the appropriate histogram bucket (`apm_duration_milliseconds_bucket`).

### The Metrics Produced by SpanMetrics

With `namespace: apm`, the collector generates three core metrics:

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `apm_calls_total` | Counter | Total number of spans completed, partitioned by service and dimensions. |
| `apm_duration_milliseconds_bucket` | Histogram Bucket | Cumulative count of spans whose duration was less than or equal to `le` (Upper bound in milliseconds). |
| `apm_duration_milliseconds_count` | Counter | Total number of duration observations (identical to `apm_calls_total`). |
| `apm_duration_milliseconds_sum` | Counter | Total cumulative duration of all observed spans in milliseconds. |

### Attribute-to-Label Mapping

OpenTelemetry span attributes use **dots** (`http.route`, `db.operation`, `peer.service`).
Prometheus metric labels use **underscores** (`http_route`, `db_operation`, `peer_service`).

The collector automatically converts all dots to underscores:
- `http.route` $\rightarrow$ `http_route`
- `http.status_code` $\rightarrow$ `http_status_code`
- `db.operation` $\rightarrow$ `db_operation`
- `db.connection.pool` $\rightarrow$ `db_connection_pool`
- `peer.service` $\rightarrow$ `peer_service`
- `service.name` (from Resource) $\rightarrow$ `service_name`

---

## 2. PromQL Foundations from First Principles

PromQL (Prometheus Query Language) is not SQL. It is a functional query language designed specifically for querying time-stamped numeric arrays called **time series**.

### The Data Model: Metric Name, Labels, and Samples

A single time series in Prometheus is identified by a **Metric Name** and a set of **Labels** (key-value pairs):

```text
apm_calls_total{service_name="tracenest-sample-django", http_route="/api/products/", http_status_code="200"}
```

At any given second, this time series holds a **Sample**: `(timestamp, numeric_value)`. For example:
- `12:00:00 -> 10,450`
- `12:00:05 -> 10,480`
- `12:00:10 -> 10,515`

### Metric Types: Counters, Gauges, and Histograms

1. **Counter**:
   - A number that **only goes up** (or resets to 0 if the service restarts).
   - Example: Total requests served (`apm_calls_total`), total bytes transmitted.
   - *Golden Rule*: Never display a raw counter directly on a graph! A counter that says "1,450,200 requests" is useless. You want to see the **rate of change per second** (`rate()`).

2. **Gauge**:
   - A number that can **go up or down**.
   - Example: Memory usage, active database connections in PgBouncer (`postgresql_pool_connection_count`), CPU temperature.
   - You can graph gauges directly.

3. **Histogram**:
   - Samples observations into configurable buckets.
   - Used to compute statistical percentiles (e.g., P95 latency).
   - Each bucket has a label `le` ("less than or equal to").
   - Example:
     ```text
     apm_duration_milliseconds_bucket{le="10"}  50   (50 requests took <= 10ms)
     apm_duration_milliseconds_bucket{le="25"}  85   (85 requests took <= 25ms)
     apm_duration_milliseconds_bucket{le="100"} 99   (99 requests took <= 100ms)
     apm_duration_milliseconds_bucket{le="+Inf"} 100 (All 100 requests took <= infinity)
     ```

### Instant Vectors vs. Range Vectors

Understanding this distinction is the key to mastering PromQL:

#### 1. Instant Vector
Returns the single most recent value for each matching time series:
```promql
apm_calls_total{http_route="/api/products/"}
```
*Result*: A list of single values right now.

#### 2. Range Vector
Appends a duration selector `[5m]` or `[1m]` to return a buffer of all historical values recorded over that window:
```promql
apm_calls_total{http_route="/api/products/"}[5m]
```
*Result*: An array of data points recorded during the last 5 minutes.
*Important*: You cannot graph a range vector directly; range vectors exist to be fed into rate functions like `rate()`.

---

### The Golden Rule: Understanding `rate()` and `irate()`

To compute **Requests Per Second (RPS)** from a counter, we use `rate()`:

```promql
rate(apm_calls_total[1m])
```

#### How `rate()` works internally:
1. Takes the range vector of counter values over the last 1 minute: `[v1, v2, v3, ..., vn]`.
2. Calculates the delta: `(vn - v1) / 60 seconds`.
3. **Automatically handles counter resets**: If your application restarts and the counter drops from 50,000 to 0, `rate()` detects the drop and adjusts calculation seamlessly.

#### `rate()` vs `irate()`:
- `rate(v[5m])`: Calculates the average per-second rate over the whole 5-minute window. **Use this for alerts, dashboard trends, and stable graphs.**
- `irate(v[1m])`: "Instant rate" — calculates the rate using only the last two data points in the window. Highly volatile; use only for debugging sharp, micro-second spikes.

---

### Aggregations: `sum()`, `by()`, and `without()`

When you run `rate(apm_calls_total[1m])`, Prometheus returns separate series for every combination of routes, status codes, and services.

If your service has 50 routes, that query returns 50 separate lines on your graph. To combine them:

#### 1. Total System RPS (`sum`)
```promql
sum(rate(apm_calls_total[1m]))
```
Combines all series into **one single number** representing total throughput across the entire application.

#### 2. Grouping by Specific Dimensions (`by`)
This is equivalent to `GROUP BY` in SQL:
```promql
sum(rate(apm_calls_total[1m])) by (http_route)
```
Produces one time series per `http_route`.

```promql
sum(rate(apm_calls_total[1m])) by (http_status_code)
```
Produces one time series per status code (e.g. 200, 404, 500).

---

### Calculating Latency Percentiles with `histogram_quantile()`

Why can't we just use the average latency?
> **The Flaw of Averages**: If 99 users experience 10ms latency and 1 user experiences 10,000ms latency, the average is ~110ms. The average looks acceptable, but your 99th percentile user is waiting 10 seconds!

In production observability, we use **percentiles**:
- **p50 (Median)**: 50% of users experience latency faster than this.
- **p95**: 95% of users experience latency faster than this (5% are slower).
- **p99**: 99% of users experience latency faster than this (the worst 1% of outliers).

#### The Formula for Percentiles in PromQL:
```promql
histogram_quantile(
  0.95,
  sum(rate(apm_duration_milliseconds_bucket[1m])) by (le)
)
```

**Step-by-step breakdown of how Prometheus computes this**:
1. `rate(apm_duration_milliseconds_bucket[1m])`: Computes the per-second rate of spans entering each bucket.
2. `sum(...) by (le)`: **CRITICAL**: You MUST preserve the `le` label in the `by()` clause! `histogram_quantile` requires the bucket boundaries (`le`) to estimate the curve.
3. `histogram_quantile(0.95, ...)`: Interpolates within the buckets to find the exact millisecond threshold where 95% of requests fall.

If you want p95 latency **per route**:
```promql
histogram_quantile(
  0.95,
  sum(rate(apm_duration_milliseconds_bucket[1m])) by (le, http_route)
)
```

---

### Mathematical Operators & Ratio Calculations

In PromQL, you can perform basic arithmetic between queries (`+`, `-`, `*`, `/`).

#### Calculating Error Rate Percentage:
$$\text{Error Rate \%} = \frac{\text{Failed Requests per Second}}{\text{Total Requests per Second}} \times 100$$

In PromQL:
```promql
(
  sum(rate(apm_calls_total{http_status_code=~"5.."}[1m]))
  /
  sum(rate(apm_calls_total[1m]))
) * 100
```
- `{http_status_code=~"5.."}` uses regex to match all 5xx HTTP codes (`500`, `502`, `503`, `504`).
- If there are zero errors, the numerator is 0, so the result is `0.0%`.

---

## 3. The Complete APM PromQL Cookbook

Below are the exact, copy-pasteable PromQL queries used in professional Grafana APM dashboards for TraceNest.

> [!TIP]
> In Grafana, replace fixed intervals like `[1m]` with `$__rate_interval`. Grafana automatically calculates the optimal interval based on the dashboard time range and scrape frequency.

---

### 1. HTTP Request Rate (Throughput / RPS)

#### Total Application Throughput:
```promql
sum(rate(apm_calls_total{span_name="django.request"}[$__rate_interval]))
```

#### Throughput Broken Down by Route:
```promql
sum(rate(apm_calls_total{span_name="django.request"}[$__rate_interval])) by (http_route)
```

#### Throughput Broken Down by HTTP Method (GET, POST):
```promql
sum(rate(apm_calls_total{span_name="django.request"}[$__rate_interval])) by (http_request_method)
```

---

### 2. Error Rate & Error Percentage

#### Overall Error Percentage (%):
```promql
(
  sum(rate(apm_calls_total{span_name="django.request", http_status_code=~"5.."}[$__rate_interval]))
  /
  sum(rate(apm_calls_total{span_name="django.request"}[$__rate_interval]))
) * 100
```

#### 4xx Client Errors vs 5xx Server Errors (RPS):
```promql
sum(rate(apm_calls_total{span_name="django.request", http_status_code=~"[45].."}[$__rate_interval])) by (http_status_code)
```

---

### 3. Latency Percentiles (p50, p95, p99)

#### Overall Application Latency (Three Lines on One Graph):
- **p50 (Median)**:
  ```promql
  histogram_quantile(0.50, sum(rate(apm_duration_milliseconds_bucket{span_name="django.request"}[$__rate_interval])) by (le))
  ```
- **p95**:
  ```promql
  histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{span_name="django.request"}[$__rate_interval])) by (le))
  ```
- **p99**:
  ```promql
  histogram_quantile(0.99, sum(rate(apm_duration_milliseconds_bucket{span_name="django.request"}[$__rate_interval])) by (le))
  ```

#### p95 Latency by Endpoint:
```promql
histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{span_name="django.request"}[$__rate_interval])) by (le, http_route))
```

---

### 4. Top Slowest Endpoints Table

In Grafana, configure a **Table Panel** with this query to immediately see your worst-performing routes:

```promql
topk(10, histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{span_name="django.request"}[$__rate_interval])) by (le, http_route)))
```

---

### 5. PostgreSQL Database Query Performance

TraceNest tags database spans with `db_system="postgresql"` and query summaries.

#### Total Database Query Throughput (QPS - Queries Per Second):
```promql
sum(rate(apm_calls_total{db_system="postgresql"}[$__rate_interval]))
```

#### QPS by SQL Operation (SELECT, INSERT, UPDATE, DELETE):
```promql
sum(rate(apm_calls_total{db_system="postgresql"}[$__rate_interval])) by (db_operation)
```

#### p95 Latency of Database Queries by Table / Summary:
```promql
histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{db_system="postgresql"}[$__rate_interval])) by (le, db_query_summary))
```

#### Primary vs. Read-Replica Query Distribution:
```promql
sum(rate(apm_calls_total{db_system="postgresql"}[$__rate_interval])) by (db_role)
```

---

### 6. PgBouncer Pool Saturation & Client Queues

When queries pass through PgBouncer, TraceNest tags `peer_service="pgbouncer"`. Additionally, the OTel collector scrapes PgBouncer's internal stats.

#### Queries Routed Through PgBouncer vs Direct Postgres:
```promql
sum(rate(apm_calls_total{db_system="postgresql"}[$__rate_interval])) by (peer_service)
```

#### PgBouncer Pool Latency:
```promql
histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{peer_service="pgbouncer"}[$__rate_interval])) by (le))
```

---

### 7. Redis Command Throughput & Cache Hit Ratio

TraceNest tags Redis calls with `db_system="redis"` and cache queries with `django_cache_hit`.

#### Total Redis Commands Per Second:
```promql
sum(rate(apm_calls_total{db_system="redis"}[$__rate_interval])) by (db_operation)
```

#### Django Cache Hit Ratio (%):
$$\text{Hit Ratio} = \frac{\text{Hits}}{\text{Hits} + \text{Misses}} \times 100$$

```promql
(
  sum(rate(apm_calls_total{django_cache_hit="true"}[$__rate_interval]))
  /
  (
    sum(rate(apm_calls_total{django_cache_hit="true"}[$__rate_interval]))
    +
    sum(rate(apm_calls_total{django_cache_hit="false"}[$__rate_interval]))
  )
) * 100
```

---

### 8. Outbound HTTP / External Dependency Latency

TraceNest instruments outgoing `requests` calls with `server_address`.

#### Outbound HTTP Requests by Third-Party Host:
```promql
sum(rate(apm_calls_total{span_kind="SPAN_KIND_CLIENT", server_address=~".+"}[$__rate_interval])) by (server_address)
```

#### External API Latency (p95):
```promql
histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{span_kind="SPAN_KIND_CLIENT", server_address=~".+"}[$__rate_interval])) by (le, server_address))
```

---

## 4. How Grafana Dashboards Are Built

A dashboard is an interactive visual grid of **Panels** powered by underlying **Data Sources**.

### Grafana Architecture in TraceNest

TraceNest uses two provisioned data sources in `docker/grafana/provisioning/datasources/datasources.yaml`:
1. **Prometheus (`uid: prometheus`)**: Evaluates PromQL queries for graphs, gauges, and tables.
2. **Tempo (`uid: tempo`)**: Stores distributed traces for waterfall exploration.

```text
┌────────────────────────────────────────────────────────┐
│                   Grafana Dashboard                    │
│                                                        │
│  [Variable: Service = tracenest-sample-django ▼]       │
│                                                        │
│  ┌────────────────────────┐  ┌──────────────────────┐  │
│  │ Panel 1: Throughput    │  │ Panel 2: Error Rate  │  │
│  │ (Time Series)          │  │ (Stat / Gauge)       │  │
│  └────────────────────────┘  └──────────────────────┘  │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Panel 3: p95 Latency by Route                    │  │
│  │ (Click on spike -> "Query in Tempo")             │  │
│  └────────────────────────┬─────────────────────────┘  │
└───────────────────────────┼────────────────────────────┘
                            │ Click Trace ID Exemplar
                            ▼
┌────────────────────────────────────────────────────────┐
│                 Tempo Trace Waterfall                  │
│                                                        │
│ django.request [240ms]                                 │
│   ├── SecurityMiddleware [2ms]                         │
│   └── 🐍 ProductViewSet.list [235ms]                   │
│         └── 🔵 SELECT * FROM api_product [220ms]       │
└────────────────────────────────────────────────────────┘
```

---

### Dashboard Variables (Templating for Dynamic Filtering)

Hardcoding service names or routes into every query is bad practice. Instead, we define **Dashboard Variables**:

In Grafana: **Dashboard Settings $\rightarrow$ Variables $\rightarrow$ Add variable**:
- **Name**: `service`
- **Type**: `Query`
- **Data Source**: `Prometheus`
- **Query**: `label_values(apm_calls_total, service_name)`

Now, any PromQL query can use `$service`:
```promql
sum(rate(apm_calls_total{service_name="$service"}[$__rate_interval]))
```
When a developer changes the dropdown at the top of the dashboard, all panels instantly update!

---

### Panel Types and Visual Styling

For Datadog-grade visual aesthetics, choose the right panel type for the right metric:

1. **Stat Panel**:
   - Best for: Key Performance Indicators (KPIs) like Total RPS, Current Error Rate %, and p95 Latency.
   - Settings:
     - Color mode: `Background` or `Value`
     - Thresholds: Green (< 1%), Yellow (1%–5%), Red (> 5%)
     - Unit: `reqps` (for RPS), `percent` (0–100), `ms` (milliseconds)

2. **Time Series Panel**:
   - Best for: Trend lines over time (Latency percentiles, QPS).
   - Settings:
     - Fill opacity: `15%` (creates modern gradient fill under lines)
     - Line interpolation: `Smooth`
     - Tooltip mode: `All`

3. **Table Panel**:
   - Best for: Top 10 slowest endpoints, Top database queries.
   - Settings:
     - Cell type: `Colored background` for latency columns.

---

### The Superpower: Linking Metrics to Tempo Traces (TracesToMetrics & Exemplars)

The single greatest feature of Grafana + OpenTelemetry is jumping **from a metric spike directly into the slow trace waterfall**.

In `datasources.yaml`, this is configured via `tracesToMetrics`:

```yaml
  - name: Tempo
    uid: tempo
    type: tempo
    jsonData:
      tracesToMetrics:
        datasourceUid: prometheus
        queries:
          - name: 'Throughput'
            query: 'sum(rate(apm_calls_total{$$__tags}[$$__rate_interval]))'
          - name: 'Latency (p95)'
            query: 'histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{$$__tags}[$$__rate_interval])) by (le))'
```

And in Prometheus:
```yaml
  - name: Prometheus
    uid: prometheus
    type: prometheus
    jsonData:
      exemplarTraceIdDestinations:
        - name: trace_id
          datasourceUid: tempo
```

#### What this achieves:
When viewing a Prometheus latency graph, tiny dots appear on the line called **Exemplars**. Hovering over a dot reveals:
> *Trace ID: `4bf92f3577b34da6a3ce929d0e0e4736` (Duration: 850ms)*

Clicking that dot immediately opens the Tempo trace waterfall view side-by-side, displaying the exact SQL queries and middleware that caused that specific spike!

---

## 5. Step-by-Step: How to Build Your Own Dashboard from Scratch

### Method A: Visual Builder in the Grafana UI

1. Open Grafana at [http://localhost:3000](http://localhost:3000).
2. Click **Dashboards** in the left sidebar $\rightarrow$ **New** $\rightarrow$ **New Dashboard**.
3. Click **Add visualization**.
4. Select **Prometheus** as your data source.
5. In the query box, paste a PromQL query (e.g., `sum(rate(apm_calls_total{span_name="django.request"}[$__rate_interval])) by (http_route)`).
6. In the right sidebar:
   - **Title**: `HTTP Request Rate by Route`
   - **Unit**: Under *Standard options* $\rightarrow$ select `Throughput` $\rightarrow$ `requests/sec (rps)`.
7. Click **Apply**.
8. Click the Save icon (top right) and name your dashboard `TraceNest APM Overview`.

---

### Method B: Declarative JSON & Provisioning (Infrastructure as Code)

In production, you should never create dashboards manually via click-ops. Instead, store the dashboard as a JSON file in your git repository. Grafana will automatically load it on startup.

#### Step 1: Create the Provisioning YAML
Create `docker/grafana/provisioning/dashboards/dashboards.yaml`:

```yaml
apiVersion: 1

providers:
  - name: 'TraceNest Dashboards'
    orgId: 1
    folder: 'APM'
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/dashboards
```

#### Step 2: Place the Dashboard JSON File
Save your dashboard JSON into `docker/grafana/dashboards/tracenest-apm.json`.

Whenever the Grafana Docker container starts, it mounts that file and automatically publishes the dashboard.

---

## 6. Complete Production-Ready Dashboard JSON Template

Below is a complete, standalone Grafana Dashboard JSON containing **5 production-grade panels**:
1. **Total Requests / Sec (Stat Panel)**
2. **Error Rate % (Stat Panel with Color Thresholds)**
3. **P95 Latency (Stat Panel)**
4. **Latency Percentiles Graph (p50, p95, p99 Time Series)**
5. **Database Queries by Operation (Time Series)**

You can copy this JSON block and import it directly into Grafana via **Dashboards $\rightarrow$ New $\rightarrow$ Import**:

```json
{
  "annotations": {
    "list": []
  },
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 1,
  "id": null,
  "links": [],
  "liveNow": false,
  "panels": [
    {
      "collapsed": false,
      "gridPos": { "h": 1, "w": 24, "x": 0, "y": 0 },
      "id": 100,
      "title": "Application Performance Overview (RED)",
      "type": "row"
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 500 },
              { "color": "red", "value": 2000 }
            ]
          },
          "unit": "reqps"
        },
        "overrides": []
      },
      "gridPos": { "h": 5, "w": 8, "x": 0, "y": 1 },
      "id": 1,
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "auto"
      },
      "pluginVersion": "10.4.1",
      "targets": [
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "editorMode": "code",
          "expr": "sum(rate(apm_calls_total{span_name=\"django.request\"}[$__rate_interval]))",
          "legendFormat": "Throughput",
          "range": true,
          "refId": "A"
        }
      ],
      "title": "Throughput (RPS)",
      "type": "stat"
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 1 },
              { "color": "red", "value": 5 }
            ]
          },
          "unit": "percent"
        },
        "overrides": []
      },
      "gridPos": { "h": 5, "w": 8, "x": 8, "y": 1 },
      "id": 2,
      "options": {
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "auto"
      },
      "pluginVersion": "10.4.1",
      "targets": [
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "editorMode": "code",
          "expr": "(sum(rate(apm_calls_total{span_name=\"django.request\", http_status_code=~\"5..\"}[$__rate_interval])) or vector(0)) / sum(rate(apm_calls_total{span_name=\"django.request\"}[$__rate_interval])) * 100",
          "legendFormat": "Error Rate",
          "range": true,
          "refId": "A"
        }
      ],
      "title": "Error Rate (%)",
      "type": "stat"
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 100 },
              { "color": "red", "value": 500 }
            ]
          },
          "unit": "ms"
        },
        "overrides": []
      },
      "gridPos": { "h": 5, "w": 8, "x": 16, "y": 1 },
      "id": 3,
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "auto"
      },
      "pluginVersion": "10.4.1",
      "targets": [
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "editorMode": "code",
          "expr": "histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{span_name=\"django.request\"}[$__rate_interval])) by (le))",
          "legendFormat": "P95 Latency",
          "range": true,
          "refId": "A"
        }
      ],
      "title": "p95 Latency",
      "type": "stat"
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "custom": {
            "drawStyle": "line",
            "fillOpacity": 15,
            "gradientMode": "opacity",
            "lineInterpolation": "smooth",
            "lineWidth": 2
          },
          "unit": "ms"
        },
        "overrides": []
      },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 6 },
      "id": 4,
      "options": {
        "legend": { "displayMode": "list", "placement": "bottom" },
        "tooltip": { "mode": "all", "sort": "desc" }
      },
      "pluginVersion": "10.4.1",
      "targets": [
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "expr": "histogram_quantile(0.50, sum(rate(apm_duration_milliseconds_bucket{span_name=\"django.request\"}[$__rate_interval])) by (le))",
          "legendFormat": "p50 (Median)",
          "refId": "A"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "expr": "histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket{span_name=\"django.request\"}[$__rate_interval])) by (le))",
          "legendFormat": "p95",
          "refId": "B"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "expr": "histogram_quantile(0.99, sum(rate(apm_duration_milliseconds_bucket{span_name=\"django.request\"}[$__rate_interval])) by (le))",
          "legendFormat": "p99",
          "refId": "C"
        }
      ],
      "title": "Request Duration Percentiles (p50, p95, p99)",
      "type": "timeseries"
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "custom": {
            "drawStyle": "line",
            "fillOpacity": 15,
            "gradientMode": "opacity",
            "lineInterpolation": "smooth",
            "lineWidth": 2
          },
          "unit": "reqps"
        },
        "overrides": []
      },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 6 },
      "id": 5,
      "options": {
        "legend": { "displayMode": "list", "placement": "bottom" },
        "tooltip": { "mode": "all", "sort": "desc" }
      },
      "pluginVersion": "10.4.1",
      "targets": [
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "expr": "sum(rate(apm_calls_total{db_system=\"postgresql\"}[$__rate_interval])) by (db_operation)",
          "legendFormat": "{{db_operation}}",
          "refId": "A"
        }
      ],
      "title": "Database Queries by Operation (QPS)",
      "type": "timeseries"
    }
  ],
  "refresh": "5s",
  "schemaVersion": 39,
  "tags": ["apm", "tracenest", "django"],
  "time": { "from": "now-15m", "to": "now" },
  "timepicker": {
    "refresh_intervals": ["5s", "10s", "30s", "1m"]
  },
  "timezone": "browser",
  "title": "TraceNest APM Overview"
}
```

---

## 7. Common Pitfalls & PromQL Troubleshooting

### 1. "My histogram query returns NaN or empty lines!"
- **Cause**: You forgot `by (le)` in your sum aggregation!
- **Wrong**: `histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket[1m])))`
- **Correct**: `histogram_quantile(0.95, sum(rate(apm_duration_milliseconds_bucket[1m])) by (le))`

### 2. "The rate query drops to 0 or gives strange spikes on reload"
- **Cause**: Using a fixed interval like `[5s]` that is equal to or smaller than your Prometheus scrape interval.
- **Solution**: Always use `$__rate_interval` in Grafana, or a minimum of $4 \times$ your scrape interval (e.g. `[20s]` or `[1m]`).

### 3. "Division by zero when calculating error rate"
- If your app received 0 requests in the last minute, `0 / 0` evaluates to `NaN`.
- **Solution**: Protect with `or vector(0)` or handle denominator nulls:
  ```promql
  (sum(rate(apm_calls_total{http_status_code=~"5.."}[1m])) or vector(0))
  /
  (sum(rate(apm_calls_total[1m])) > 0) * 100
  ```

### 4. "Metric names have dots, but Prometheus doesn't find them"
- Prometheus does not allow `.` in metric names or label names.
- OpenTelemetry's `spanmetrics` exporter converts all dots to underscores:
  `http.route` $\rightarrow$ `http_route`
  `db.system` $\rightarrow$ `db_system`

---

## Summary Checklist for Developers

When you add a new instrumentation to the SDK (e.g. Celery or Mongo):
1. Make sure your span sets standard attributes (e.g. `messaging.system`, `messaging.operation`).
2. Add the attribute name to `dimensions` in `docker/otel-collector/otel-collector-config.yaml` so `spanmetrics` exposes it.
3. Open Grafana and write a PromQL query using `sum(rate(apm_calls_total{...}[$__rate_interval])) by (messaging_operation)`.
4. Add a Stat or Time Series panel to your dashboard.
5. You're done! You now have full metrics, rate graphs, and latency histograms for your new instrumentation.
