# tp_obs_v3

A high-performance, drop-in Python Observability SDK powered by OpenTelemetry with custom instrumentation wrappers.

## Features

- **Standard OpenTelemetry Foundation:** Uses official `opentelemetry-api` and `opentelemetry-sdk` primitives.
- **Deep Waterfall Hierarchy:** Custom instrumentations for Django, Postgres, Redis, Requests, FastAPI, and Flask.
- **Zero Boilerplate:** One-line bootstrap via `tp_obs_v3.init()` and auto-instrumentation via `tp_obs_v3.patch_all()`.
- **W3C Distributed Tracing:** Built-in traceparent header propagation across services.
- **Safe & Sanitized:** Automatic PII/credential stripping in URLs and parameter masking in SQL queries.

## Quick Start

```python
import tp_obs_v3

# Initialize SDK
tp_obs_v3.init(
    service="my-django-app",
    endpoint="http://localhost:4318",
    environment="production",
)

# Auto-instrument all supported libraries
tp_obs_v3.patch_all()
```
