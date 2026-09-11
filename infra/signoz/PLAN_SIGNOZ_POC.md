# Pending Feature: SigNoz Self-Hosted Observability PoC

**Status**: PENDING / BACKLOG  
**Target Architecture**: SigNoz Self-Hosted Community Edition alongside OTel Collector Contrib & Grafana/Tempo.

---

## Overview

This feature will deploy **SigNoz Self-Hosted Community Edition** alongside the existing **Grafana + Tempo + Prometheus** stack.

The TraceNest SDK will **not** be tied to SigNoz or modified. Telemetry will be dual-exported by the custom `otel-collector` (`otlp/tempo` + `otlp/signoz`), enabling side-by-side APM comparison between Grafana/Tempo and SigNoz.

```text
                    ┌─────────────────────┐
                    │    Sample Django    │
                    │       App           │
                    └──────────┬──────────┘
                               │
                         TraceNest SDK
                               │
                     OpenTelemetry SDK
                               │
                               ▼
                    ┌─────────────────────┐
                    │ OTel Collector      │
                    │ Contrib (0.160.0)   │
                    └──────┬───────────┬──┘
                           │           │
                     OTLP  │           │ OTLP
                           ▼           ▼
             ┌───────────────────┐   ┌─────────────────────┐
             │      SigNoz       │   │   Grafana + Tempo   │
             │ (Community v0.135)│   │    + Prometheus     │
             └───────────────────┘   └─────────────────────┘
```

---

## Action Plan For Implementation

### 1. SigNoz Deployment Config (`infra/signoz/`)

- Create `infra/signoz/casting.yaml` (Foundry configuration with `flavor: compose`).
- Create `infra/signoz/docker-compose.signoz.yml`:
  - SigNoz UI on port `8080`
  - SigNoz OTel Collector (`signoz-otel-collector:4317`)
  - ClickHouse & ClickHouse Keeper storage
  - Join network `tp-observability-net`

### 2. Collector & Port Configuration

- Update [`docker-compose.yml`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/docker-compose.yml):
  - Change `otel-collector` image to `otel/opentelemetry-collector-contrib:0.160.0`
  - Remap `external-api` port binding to `8082:8080` (freeing port `8080` for SigNoz UI)
- Update [`docker/otel-collector/otel-collector-config.yaml`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/docker/otel-collector/otel-collector-config.yaml):
  - Add `otlp/signoz` exporter pointing to `signoz-otel-collector:4317`
  - Add `otlp/signoz` to `traces`, `metrics`, and `logs` pipelines alongside `otlp/tempo` and `prometheus`

### 3. Verification & UI Checks

- Run `.venv/bin/pytest` to ensure 100% backend independence.
- Run `./generate_traffic.sh` and compare APM metrics, waterfall traces, service maps, and exceptions at `http://localhost:8080` (SigNoz UI) vs `http://localhost:3000` (Grafana UI).

---

## Documentation

Upon execution, document setup & teardown instructions in `infra/signoz/README.md`.
