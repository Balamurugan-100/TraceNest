# Pending Features & Future Roadmap

This document tracks planned features, roadmap items, and Basecamp todo items for TraceNest SDK v3.

---

## 1. Add Boto/AWS Instrumentation
- **Status**: **IN PROGRESS**
- **Basecamp Todo**: [Add Boto/AWS instrumentation](https://app.basecamp.com/4160028/buckets/48560409/todos/10294134139/edit?replace=true)
- **Goal**: Instrument `botocore` / `boto3` for AWS services (S3, SQS, SNS, DynamoDB, SES, KMS, etc.) with standard OTel RPC attributes (`rpc.system="aws"`, `rpc.service`, `rpc.method`, `aws.region`, `peer.service="aws.<service>"`), visual icon formatting (`☁️`), request/response hooks, and unit tests.

---

## 2. Implement Service-Level Performance Reporting
- **Status**: PENDING
- **Basecamp Todo**: [Implement service-level performance reporting](https://app.basecamp.com/4160028/buckets/48560409/todos/10294134040/edit?replace=true)
- **Goal**: Aggregate request metrics (RPS, error rate, P50/P95/P99 latency) per service and endpoint for executive/summary reporting.

---

## 3. Evaluate SigNoz as the Observability UI
- **Status**: PENDING (Specification Ready)
- **Basecamp Todo**: [Evaluate SigNoz as the observability UI](https://app.basecamp.com/4160028/buckets/48560409/todos/10294134076/edit?replace=true)
- **Specification & Plan**: [`infra/signoz/PLAN_SIGNOZ_POC.md`](file:///Users/bala/workspace/datadog-replacement/sdk-v3/infra/signoz/PLAN_SIGNOZ_POC.md)
- **Goal**: Deploy SigNoz Self-Hosted Community Edition alongside Grafana/Tempo for dual-export visual evaluation.

---

## 4. Implement Baseline vs Current Traffic Comparison
- **Status**: **COMPLETED**
- **Basecamp Todo**: [Implement baseline vs current traffic comparison](https://app.basecamp.com/4160028/buckets/48560409/todos/10294134132/edit?replace=true)
- **Goal**: Compare historical baseline traffic distributions (latency percentiles, throughput) against current live traffic to detect anomalies and regressions.
- **Implementation**: 
  - Added 4 new panels to `docker/grafana/dashboards/tracenest_service_catalog.json`:
    1. **Endpoints with RPS Anomaly (>50%)** (stat) — count of anomalous endpoints across all services
    2. **Endpoints with Severe RPS Anomaly (>200%)** (stat) — count of severe anomalies
    3. **RPS Baseline Anomaly Detection (Global)** (row) — section header
    4. **Global RPS Anomaly List** (table) — detailed list of all anomalous endpoints across services with drill-down links
  - Created Prometheus alert rules in `infra/prometheus/rules/baseline.yml` for anomaly and severe anomaly detection
  - Updated `docker/prometheus/prometheus.yml` to load the new rules
  - Baseline uses rolling 7-day median RPS (quantile_over_time(0.5, ...[7d:1h]))
  - Current RPS computed from last 5m rate
  - Deviation = ((current - baseline) / baseline) × 100

---

## 5. Evaluate Service-Level Flamegraphs
- **Status**: PENDING
- **Basecamp Todo**: [Evaluate service-level flamegraphs](https://app.basecamp.com/4160028/buckets/48560409/todos/10294134145/edit?replace=true)
- **Goal**: Profile function call trees and wall-time execution to generate flamegraphs for service-level performance diagnostics.
