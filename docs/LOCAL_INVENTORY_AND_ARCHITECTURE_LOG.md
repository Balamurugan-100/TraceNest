# TraceNest Local System & Workspace Inventory Log

**Generated At**: 2026-09-21  
**Workspace**: `/Users/bala/workspace/datadog-replacement/sdk-v3`  
**Purpose**: Complete reference log of all local SDK components, integrations, Docker observability infrastructure, dashboards, and test suites.

---

## 1. SDK Architecture (`src/tracenest/`)

### Core SDK Engine
* **`core.py`**:
  * Global initialization entrypoint (`tracenest.init(...)`).
  * Configures OpenTelemetry `TracerProvider`, Resource attributes (`service.name`, `deployment.environment`, `cluster_name`, `version`).
  * Sets up OTLP Span Exporter (gRPC/HTTP) with `BatchSpanProcessor` or `SimpleSpanProcessor`.
  * In-memory test exporter support (`InMemorySpanExporter`).
  * Process exit hooks (`atexit`) and manual `tracenest.flush()` / `tracenest.shutdown()`.
* **`config.py`**:
  * Central configuration class `SDKConfig` with validation.
  * Loads environment variables (`TRACENEST_*`, `OTEL_*`) with graceful fallbacks.
* **`sampling/`**:
  * Head-based rule sampling (`SamplingRuleEngine`, `RuleBasedSampler`).
  * Endpoint regex filtering, percentage rates, and default fallbacks.

### Integrations Ecosystem (`src/tracenest/integrations/`)
| Integration | Module Path | Instrumentation Mechanism | Metric Dimensions Produced |
| :--- | :--- | :--- | :--- |
| **Redis** | `src/tracenest/integrations/redis/integration.py` | Official `opentelemetry.instrumentation.redis.RedisInstrumentor` | `db_system="redis"`, `span_name="GET"\|"SET"\|...`, `db_instance` |
| **PostgreSQL** | `src/tracenest/integrations/postgres/integration.py` | `psycopg2` / DB-API hook with sanitized query normalization | `db_system="postgresql"`, `db_operation="SELECT"\|"INSERT"\|...`, `db_name` |
| **Django** | `src/tracenest/integrations/django/integration.py` | Django middleware, URL resolver, request context propagation | `span_name="django.request"`, `http_route`, `http_method`, `http_status_code` |
| **HTTP Requests** | `src/tracenest/integrations/requests/integration.py` | `requests` Session & urllib3 hook | `span_type="http"`, `http_url`, `http_method`, `http_status_code` |
| **Boto3 / AWS** | `src/tracenest/integrations/boto/integration.py` | AWS SDK / botocore call wrapper | `span_name="s3.*"\|"sqs.*"`, `rpc.system="aws-api"` |
| **Manager** | `src/tracenest/integrations/manager.py` | Auto-discovery and registration lifecycle | Automatic package detection via `importlib` |

---

## 2. Grafana Dashboards (`docker/grafana/dashboards/`)

| Dashboard File | UID | Key Highlights & Capabilities |
| :--- | :--- | :--- |
| **`tracenest_project_catalog.json`** | `tracenest-project-catalog` | Multi-project high level service & cluster grid, total RPS, error rates, and p95 latency rollups. |
| **`tracenest_service_catalog.json`** | `tracenest-service-catalog` | Dynamic component discovery (Django, Postgres, Redis, HTTP) with automated **Active Issues & Anomaly Detection Cards** and 1-click drill-downs. |
| **`tracenest_needs_attention.json`** | `tracenest-needs-attention` | Evidence-based diagnostic cards: Displays **P95 vs Baseline**, **Component contribution %**, affected endpoint counts, and direct action routing. |
| **`tracenest_django_overview.json`** | `tracenest-django-overview` | Requests & error volume, p95/p99 latency, HTTP status code distributions, and interactive endpoint resource table. |
| **`tracenest_django_endpoint_details.json`** | `tracenest-django-endpoint-details` | Latency breakdown across tiers (Django compute, Postgres, Redis, HTTP external) and Tempo flamegraph waterfall viewer. |
| **`tracenest_postgres_overview.json`** | `tracenest-postgres-overview` | Database operations throughput, connection pooler stats, slow query analysis, error rates, and normalized query resource routing table. |
| **`tracenest_postgres_query_details.json`** | `tracenest-postgres-query-details` | Query performance deep-dive (p50/p75/p90/p95/p99 latency), execution count, error breakdown, and Tempo trace waterfall. |
| **`tracenest_redis_overview.json`** | `tracenest-redis-overview` | Total command throughput, Top 5 commands (`GET`, `SET`, `HGETALL`), and interactive command resource routing table with bar gauges. |
| **`tracenest_redis_command_details.json`** | `tracenest-redis-command` | Specific command performance (p50–p99 latency distribution), error rate, and Tempo trace/flamegraph drill-down. |

---

## 3. Observability Pipeline Infrastructure (`docker/`)

* **OpenTelemetry Collector (`otel-collector-config.yaml`)**:
  * **Receivers**: OTLP gRPC (`4317`) and HTTP (`4318`).
  * **Processors**: `batch`, `memory_limiter`, `resource`.
  * **Connectors**: `spanmetrics` generating:
    * `apm_calls_total`
    * `apm_duration_milliseconds_bucket`
    * `apm_duration_milliseconds_sum`
    * `apm_duration_milliseconds_count`
  * **Exporters**: Prometheus (`8889`), Tempo OTLP (`3200`).
* **Prometheus**:
  * Scrapes spanmetrics every 5s for real-time alerting, quantile calculation, and dashboard queries.
* **Grafana**:
  * Pre-provisioned datasources (Prometheus + Tempo) with automatic dashboard loader.
* **Tempo**:
  * Distributed tracing backend with TraceQL and full flamegraph visualization.

---

## 4. Test Suites & Tools

* **Unit Tests (`tests/`)**:
  * `test_init.py`: Core initialization, exporter configuration, batching, clean shutdown.
  * `test_redis.py`: Redis client instrumentation, command naming (`GET`, `SET`, `PING`), exception handling, uninstrument lifecycle.
  * `test_postgres.py`: Query sanitization, connection tracking, slow query timing.
  * `test_django.py` & `test_django_advanced.py`: Middleware execution, route capture, status code tagging, header propagation.
  * `test_requests.py`: Outgoing HTTP calls, client trace header injection.
  * `test_sampling_rules.py`: Head-sampling rules evaluation and rate limit matching.
* **Traffic Simulation**:
  * `generate_anomaly.sh`: Generates synthetic traffic and introduces latency / error spikes for live validation of alerts and cards.
