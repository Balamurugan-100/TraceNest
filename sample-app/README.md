# Django OpenTelemetry POC

A proof-of-concept Django application demonstrating OpenTelemetry instrumentation patterns for distributed tracing, metrics, and logging.

## Architecture Overview

```
                   ┌─────────────┐
                   │    Nginx    │
                   │  (Port 80)  │
                   └──────┬──────┘
                          │
                   ┌──────▼──────┐
                   │    Django   │
                   └──────┬──────┘
        ┌─────────────┼─────────────┬─────────────┬─────────────┐
        │             │             │             │             │
 ┌──────▼──────┐┌─────▼──────┐┌─────▼──────┐┌─────▼──────┐┌─────▼──────┐┌──────────────┐
 │  pgbouncer  ││  slave1db  ││  slave2db  ││  slave3db  ││   redis    ││ external-api │
 │  (default)  ││  (slave1)  ││  (slave2)  ││  (slave3)  ││  (Redis)   ││   (HTTP)     │
 └──────┬──────┘└────────────┘└────────────┘└────────────┘└────────────┘└──────────────┘
 ┌──────▼──────┐
 │  postgres   │
 └─────────────┘
```

## Components

| Service | Technology | Port | Purpose |
|---------|------------|------|---------|
| **Web** | Django 4.2 + Gunicorn | 8000 | Application server |
| **Nginx** | Nginx | 8001 | Reverse proxy, static files |
| **PostgreSQL** | PostgreSQL 16 | 5433 | Primary database (via PgBouncer) |
| **PgBouncer** | PgBouncer | 6432 | Connection pooler for `default` DB |
| **Slave1 DB** | PostgreSQL 16 | 5434 | `slave1` database instance |
| **Slave2 DB** | PostgreSQL 16 | 5435 | `slave2` database instance |
| **Slave3 DB** | PostgreSQL 16 | 5436 | `slave3` database instance |
| **Redis** | Redis 7 | 6379 | Cache & sessions |
| **External API** | go-httpbin | 8080 | Downstream HTTP dependency |

## Frozen Environment Endpoints

All endpoints are prefixed with `/api/`.

| Method | Endpoint | Target Topology Component | Description |
|--------|----------|---------------------------|-------------|
| `POST` | `/api/products/` | `default` (PostgreSQL) | Creates product in primary DB |
| `GET` | `/api/products/read-slave1/` | `slave1` (slave1db) | Reads products from slave1 DB |
| `GET` | `/api/products/read-slave2/` | `slave2` (slave2db) | Reads products from slave2 DB |
| `GET` | `/api/products/read-slave3/` | `slave3` (slave3db) | Reads products from slave3 DB |
| `GET` | `/api/products/cache/` | `Redis` (redis) | Read/write caching using Redis |
| `GET` | `/api/products/external/` | `HTTP` (external-api) | Makes HTTP request to downstream service |
| `GET` | `/api/products/error/` | App Error | Raises 500 error for trace validation |
| `GET` | `/api/products/slow/` | Latency Delay | Introduces 2s delay for trace latency |

### UI Pages
| Endpoint | Description |
|----------|-------------|
| `/` | Interactive API endpoint explorer |
| `/products-tmpl/` | HTML product list |
| `/products-tmpl/{id}/` | HTML product detail |

## Key Features for OTel Testing

### 1. **Request Logging Middleware** (`api/middleware.py`)
- Generates/propagates `X-Request-ID` headers
- Logs request method, path, status, duration as JSON
- Adds `X-Response-Time` header to responses
- Handles exceptions with structured error logging

### 2. **Database Operations**
- Standard ORM queries (auto-instrumented by OTel)
- Raw SQL execution via `connection.cursor()`
- Transaction management with `select_for_update()`
- Bulk operations with `transaction.atomic()`

### 3. **Caching** (`django-redis`)
- Redis-backed cache with `cache.get/set`
- Cache hit/miss demonstration endpoint
- Health check includes Redis connectivity

### 4. **External HTTP Calls**
- `requests.get()` with configurable timeout
- Returns response time for latency analysis

### 5. **Concurrency Patterns**
- Thread pool execution (`threading.Thread`)
- Thread joins for synchronization

### 6. **Manual OTel Spans**
- Programmatic span creation with attributes
- Nested span hierarchy demonstration

## Configuration

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_DB_HOST` | `localhost` | PostgreSQL host |
| `DJANGO_DB_PORT` | `5433` | PostgreSQL port |
| `DJANGO_DB_NAME` | `django_otel` | Database name |
| `DJANGO_DB_USER` | `django` | Database user |
| `DJANGO_DB_PASSWORD` | `django` | Database password |
| `DJANGO_REDIS_URL` | `redis://127.0.0.1:6379/1` | Redis connection URL |
| `GUNICORN_WORKERS` | `CPU*2+1` | Gunicorn worker count |
| `GUNICORN_THREADS` | `4` | Threads per worker |

### PgBouncer Settings (`pgbouncer/pgbouncer.ini`)
- **Pool mode**: `session` (per-client connection)
- **Default pool size**: 500 connections
- **Max client connections**: 1000
- **Server lifetime**: 3600s (rotation)

### Gunicorn Settings (`gunicorn.conf.py`)
- **Worker class**: `gthread` (threaded workers)
- **Workers**: Auto-calculated or `GUNICORN_WORKERS`
- **Threads**: `GUNICORN_THREADS` (default 4)
- **Timeout**: 120s

## Adding OpenTelemetry

The project is structured for easy OTel integration. To add instrumentation:

```bash
# Install OTel packages
pip install opentelemetry-distro opentelemetry-exporter-otlp

# Run auto-instrumentation
opentelemetry-bootstrap -a install
```

Then run with:
```bash
opentelemetry-instrument \
  --traces_exporter otlp \
  --metrics_exporter otlp \
  --logs_exporter otlp \
  --service_name django-otel-poc \
  python manage.py runserver
```

### Recommended Instrumentation Packages
```bash
pip install \
  opentelemetry-instrumentation-django \
  opentelemetry-instrumentation-psycopg2 \
  opentelemetry-instrumentation-redis \
  opentelemetry-instrumentation-requests \
  opentelemetry-instrumentation-threads
```

## Project Structure

```
django-otel-poc/
├── api/                    # Main Django app
│   ├── migrations/         # Database migrations
│   ├── templates/          # HTML templates
│   ├── __init__.py
│   ├── admin.py            # Admin configuration
│   ├── apps.py             # App config
│   ├── middleware.py       # Request logging middleware
│   ├── models.py           # Product model
│   ├── serializers.py      # DRF serializers
│   ├── tests.py            # Test cases
│   ├── urls.py             # API routes
│   └── views.py            # All view logic
├── config/                 # Django project config
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py         # Main settings
│   ├── urls.py             # Root URL config
│   └── wsgi.py
├── nginx/                  # Nginx configuration
│   ├── Dockerfile
│   └── nginx.conf
├── pgbouncer/              # PgBouncer configuration
│   ├── pgbouncer.ini
│   ├── pg_hba.conf
│   └── userlist.txt
├── .gitignore
├── .venv/                  # Virtual environment (local)
├── docker-compose.yml      # Multi-service orchestration
├── Dockerfile              # Web service image
├── gunicorn.conf.py        # Gunicorn configuration
├── manage.py               # Django CLI
├── mise.toml               # Tool version pinning
└── requirements.txt        # Python dependencies
```

## Testing

```bash
# Run Django tests
python manage.py test

# Run with coverage
pip install coverage
coverage run --source='.' manage.py test
coverage report
```

## Health Checks

The `/api/products/health/` endpoint verifies:
- **PostgreSQL**: Executes `Product.objects.first()`
- **Redis**: Sets/gets a test key

Returns:
```json
{
  "status": "healthy|degraded",
  "checks": {
    "postgres": "ok|error: ...",
    "redis": "ok|error: ..."
  }
}
```

## Admin Interface

Create a superuser:
```bash
python manage.py createsuperuser
```

Access at `/admin/` with credentials.

## Production Considerations

1. **Secret Key**: Generate a secure `SECRET_KEY`
2. **Debug**: Set `DEBUG = False`
3. **Allowed Hosts**: Configure specific hosts
4. **Database**: Use connection pooling (PgBouncer configured)
5. **Caching**: Redis configured with django-redis
6. **Static Files**: Collected via `collectstatic` in Dockerfile
7. **Logging**: JSON structured logging via middleware
8. **Workers**: Tune `GUNICORN_WORKERS` and `GUNICORN_THREADS` for your workload

## License

POC project - no license specified.