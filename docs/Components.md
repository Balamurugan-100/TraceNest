# Supported Components & Integrations

TraceNest automatically detects and instruments key frameworks, databases, and client libraries in your Python application.

When `tracenest.init()` runs, it auto-patches all installed components with zero configuration.

```text
                                DJANGO REQUEST WATERFALL
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ django.request [SERVER]                                                  │
 │  ├── ⚙️ django.middleware.security.SecurityMiddleware.__call__           │
 │  ├── ⚙️ django.contrib.auth.middleware.AuthenticationMiddleware.__call__  │
 │  └── 🐍 django.view.ProductDetailView                                   │
 │       ├── 🔵 SELECT id, name, price FROM products WHERE id = ?          │
 │       ├── 🔴 django_redis.cache.get                                     │
 │       ├── 🌐 HTTP GET api.inventory.internal                            │
 │       └── 🎨 django.template: products/detail.html                       │
 └──────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Django Integration

The Django integration provides complete lifecycle observability for inbound HTTP requests.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`django.request`** | `SERVER` | Root span representing the entire HTTP request | `http.method`, `http.status_code`, `http.route`, `client.address` |
| **`⚙️ <module>.<Class>.<method>`** | `INTERNAL` | Timing for middleware hooks (`__call__`, `process_request`, etc.) | `django.middleware`, `django.middleware.name`, `django.middleware.method` |
| **`🐍 django.view.<Name>`** | `INTERNAL` | View execution (FBVs, CBVs, DRF viewsets, `dispatch`) | `django.view`, `django.view.name`, `django.view.class`, `django.view.action` |
| **`🎨 django.template: <name>`** | `INTERNAL` | Template rendering and nested `{% include %}` | `django.template.name` |
| **`🔴 django_redis.cache.<op>`** | `INTERNAL` | Django cache backend calls (`get`, `set`, `delete`) | `django.cache.operation`, `django.cache.backend`, `django.cache.key`, `django.cache.hit` |
| **`🔐 django.auth.login`** | `INTERNAL` | User login event | `django.auth.action="login"`, `usr.id`, `enduser.id` |
| **`🔐 django.auth.authenticate`** | `INTERNAL` | User authentication attempts | `django.auth.action="authenticate"`, `auth.success` (bool), `usr.id`, `enduser.id` |

### Key Features
- **Route Normalization**: Automatically converts high-cardinality paths like `/api/users/8572/` into normalized templates `/api/users/{id}/` to keep Prometheus metric series clean.
- **Trace Context Propagation**: Extracts incoming W3C `traceparent` headers to connect upstream services to the Django trace.
- **Response Headers**: Injects `X-Trace-ID` and `X-Span-ID` into outgoing HTTP responses for easy frontend-to-backend correlation.

---

## 2. PostgreSQL Integration (`psycopg2` & `psycopg3`)

Wraps database cursor execution to track queries, timing, and connection topology.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`🐘 <sanitized_sql>`** | `CLIENT` | Direct PostgreSQL database query (e.g. `🐘 SELECT * FROM users WHERE id = ?`) | `db.system="postgresql"`, `db.statement`, `db.operation`, `db.role="primary"`, `db.instance` |
| **`🔵 <sanitized_sql>`** | `CLIENT` | Query executed through **PgBouncer** connection pool | `db.system="postgresql"`, `db.statement`, `db.connection.pool="pgbouncer"`, `peer.service="pgbouncer"` |
| **`🐘 postgres.query`** | `CLIENT` | Fallback span name when SQL statement is empty or unavailable | `db.system="postgresql"`, `db.role`, `db.instance` |

### Key Features
- **SQL Sanitization**: Strips literals, IDs, strings, and sensitive values (e.g. `SELECT * FROM users WHERE email = 'bob@example.com'` $\rightarrow$ `SELECT * FROM users WHERE email = ?`).
- **Topology Awareness**: Automatically distinguishes between Primary DB (`db.role="primary"`), Read-Replicas (`db.role="replica"`), and PgBouncer connection pools using host/port metadata.

---

## 3. Redis Integration (`redis-py` & `django_redis`)

Instruments Redis client commands and Django cache operations.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`<COMMAND>`** (e.g. `GET`, `SET`, `HGETALL`) | `CLIENT` | Direct low-level Redis client commands via official `RedisInstrumentor` | `db.system="redis"`, `db.operation`, `db.statement`, `net.peer.name` |
| **`PIPELINE`** | `CLIENT` | Redis batch pipeline execution | `db.system="redis"`, `db.operation="PIPELINE"` |
| **`🔴 django_redis.cache.<op>`** | `INTERNAL` | Django cache operations (`get`, `set`, `delete_many`, etc.) | `django.cache.operation`, `django.cache.backend`, `django.cache.key`, `django.cache.hit` |

### Key Features
- **Sensitive Command Redaction**: Arguments for commands like `AUTH`, `CONFIG`, and `PASSWORD` are automatically scrubbed.
- **Network Safety**: Masks internal IP addresses and formats socket connection targets cleanly.

---

## 4. HTTP Client Integration (`requests`)

Instruments outbound HTTP requests made by your application to external APIs or internal microservices.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`🌐 HTTP <METHOD> <HOST>`** | `CLIENT` | Outbound HTTP request (e.g. `HTTP GET api.stripe.com`) | `http.method`, `http.url`, `http.status_code`, `peer.service` |

### Key Features
- **Distributed Context Injection**: Automatically injects W3C `traceparent` headers into outgoing request headers so downstream services continue the exact same trace.
- **URL Sanitization**: Strips basic-auth credentials and sensitive query parameters from logged URLs.

---

## 5. AWS Boto3 Integration (`boto3`)

Instruments AWS SDK operations (S3, SQS, DynamoDB, or local MinIO/mock services).

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`aws.<service>.<operation>`** | `CLIENT` | AWS SDK operation (e.g. `aws.s3.GetObject`, `aws.sqs.SendMessage`) | `rpc.system="aws-api"`, `rpc.service`, `rpc.method`, `aws.bucket` |

### Key Features
- Captures RPC method, target resource names (e.g., S3 bucket name), and response status codes.

---

## 6. Component Enable / Disable Matrix

All installed integrations are enabled by default (`auto_patch=True`). You can selectively disable any component during initialization:

```python
import tracenest

tracenest.init(
    project_name="my-django-service",
    integrations={
        "django": True,      # Django request/views/middleware
        "postgres": True,    # PostgreSQL queries
        "redis": False,      # Disable Redis tracing
        "requests": True,    # Outbound HTTP calls
        "boto": False,       # Disable AWS SDK tracing
    }
)
```
