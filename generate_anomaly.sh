#!/usr/bin/env bash
# ==============================================================================
# generate_anomaly.sh - TraceNest APM Anomaly & Incident Generator
# ==============================================================================
# Injects targeted anomalies to test and demonstrate the Exception-Driven
# "Needs Attention" / Operational System Overview dashboard.
#
# Available Scenarios:
#   1. postgres   - PostgreSQL latency bottleneck (>40% downstream time, elevated P95)
#   2. redis      - Redis latency bottleneck (>25% downstream time, elevated P95)
#   3. internal   - Django internal compute latency (P95 > 1s, downstream < 40%)
#   4. error      - Error rate spike (>5% 5xx errors on endpoints)
#   5. traffic    - Traffic surge anomaly (>200% RPS baseline deviation, healthy latency)
#   6. chaos      - Multiple simultaneous issues (PostgreSQL + Redis + Errors)
#   7. healthy    - Normal baseline fast traffic (restores 0 active issues)
# ==============================================================================

set -e

BASE_URL="${BASE_URL:-http://localhost:8001}"
SCENARIO=""
DURATION=60
CONCURRENCY=3
SLEEP_DELAY=0.02
VERBOSE=0

# Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_RED="\033[31m"
C_BLUE="\033[34m"
C_CYAN="\033[36m"
C_MAGENTA="\033[35m"

# Print usage
usage() {
  echo -e "${C_BOLD}Usage: $0 <scenario> [OPTIONS]${C_RESET}"
  echo ""
  echo -e "${C_BOLD}Scenarios:${C_RESET}"
  echo -e "  ${C_RED}postgres${C_RESET} | ${C_RED}--postgres${C_RESET}     Inject PostgreSQL latency bottleneck (pg_sleep 1.5s - 2.5s)"
  echo -e "  ${C_RED}redis${C_RESET}    | ${C_RED}--redis${C_RESET}        Inject Redis operations latency bottleneck (delay 2.0s)"
  echo -e "  ${C_YELLOW}internal${C_RESET} | ${C_YELLOW}--internal${C_RESET}     Inject Django internal processing latency (delay 1.5s)"
  echo -e "  ${C_RED}error${C_RESET}    | ${C_RED}--error${C_RESET}        Inject 500 error rate spike on endpoints"
  echo -e "  ${C_BLUE}traffic${C_RESET}  | ${C_BLUE}--traffic${C_RESET}      Inject traffic surge anomaly (>200% baseline, healthy latency)"
  echo -e "  ${C_MAGENTA}chaos${C_RESET}    | ${C_MAGENTA}--chaos${C_RESET}        Trigger multi-issue chaos (Postgres + Redis + Errors)"
  echo -e "  ${C_GREEN}healthy${C_RESET}  | ${C_GREEN}--healthy${C_RESET}      Send clean baseline traffic (0 issues in Needs Attention)"
  echo ""
  echo -e "${C_BOLD}Options:${C_RESET}"
  echo "  -d, --duration N     Duration in seconds to run injection (default: 60)"
  echo "  -c, --concurrency N  Number of concurrent workers (default: 2)"
  echo "  -s, --sleep SEC      Sleep between requests in seconds (default: 0.1)"
  echo "  -u, --url URL        Base URL (default: http://localhost:8001)"
  echo "  -v, --verbose        Print response details"
  echo "  -h, --help           Show this help message"
  echo ""
  echo -e "${C_BOLD}Examples:${C_RESET}"
  echo "  $0 postgres"
  echo "  $0 redis -d 120"
  echo "  $0 error -c 5"
  echo "  $0 traffic -c 8 -s 0"
  echo "  $0 healthy"
  exit 0
}

# Parse positional and flagged arguments
while [[ "$#" -gt 0 ]]; do
  case $1 in
  postgres | --postgres | --slow-postgres) SCENARIO="postgres" ;;
  redis | --redis | --slow-redis) SCENARIO="redis" ;;
  internal | django | --internal | --django | --slow-django) SCENARIO="internal" ;;
  error | errors | --error | --errors | --error-spike) SCENARIO="error" ;;
  traffic | surge | --traffic | --surge) SCENARIO="traffic" ;;
  chaos | all | --chaos | --all) SCENARIO="chaos" ;;
  healthy | normal | clean | --healthy | --normal | --clean) SCENARIO="healthy" ;;
  -d | --duration)
    DURATION="$2"
    shift
    ;;
  -c | --concurrency)
    CONCURRENCY="$2"
    shift
    ;;
  -s | --sleep)
    SLEEP_DELAY="$2"
    shift
    ;;
  -u | --url)
    BASE_URL="$2"
    shift
    ;;
  -v | --verbose) VERBOSE=1 ;;
  -h | --help) usage ;;
  *)
    echo -e "${C_RED}Unknown option or scenario: $1${C_RESET}"
    echo "Run '$0 --help' for usage."
    exit 1
    ;;
  esac
  shift
done

if [ -z "$SCENARIO" ]; then
  echo -e "${C_YELLOW}No scenario specified. Defaulting to: postgres${C_RESET}"
  SCENARIO="postgres"
fi

# Banner
echo -e "${C_CYAN}${C_BOLD}"
echo "================================================================="
echo "   ⚡ TraceNest APM Anomaly Generator"
echo "   Target URL:     ${BASE_URL}"
echo "   Scenario:       ${SCENARIO}"
echo "   Duration:       ${DURATION}s"
echo "   Concurrency:    ${CONCURRENCY} worker(s)"
echo "   Sleep Delay:    ${SLEEP_DELAY}s"
echo "=================================================================${C_RESET}"

# Send single HTTP request helper
send_req() {
  local method="$1"
  local path="$2"
  local data="$3"
  local label="$4"

  local url="${BASE_URL}${path}"
  local http_code
  local start_ts end_ts duration_ms

  start_ts=$(date +%s%N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1e9))')

  if [ "$method" = "POST" ]; then
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "$data" "$url" --max-time 15 2>/dev/null || echo "000")
  else
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$url" --max-time 15 2>/dev/null || echo "000")
  fi

  end_ts=$(date +%s%N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1e9))')
  duration_ms=$(( (end_ts - start_ts) / 1000000 ))

  local color="$C_GREEN"
  if [[ "$http_code" =~ ^5 ]]; then
    color="$C_RED"
  elif [[ "$http_code" =~ ^4 ]]; then
    color="$C_YELLOW"
  elif [ "$duration_ms" -gt 1000 ]; then
    color="$C_MAGENTA"
  fi

  echo -e "   [${color}${http_code}${C_RESET}] (${duration_ms}ms) ${label:-$path}"
}

# Worker loop function
run_scenario_worker() {
  local wid="$1"
  local end_time=$(( $(date +%s) + DURATION ))

  while [ $(date +%s) -lt $end_time ]; do
    case "$SCENARIO" in
    postgres)
      # Injects slow SQL queries across DBs to trigger high Postgres P95 and >40% downstream duration
      send_req "GET" "/api/raw-sql/?query=SELECT%20pg_sleep(1.8)%2C%20COUNT(*)%20FROM%20api_product" "" "PostgreSQL (default): pg_sleep(1.8s) Slow Query"
      send_req "GET" "/api/products/read-slave1/" "" "PostgreSQL (slave1): Read Products"
      send_req "GET" "/api/products/" "" "Django: Product Catalog View"
      ;;

    redis)
      # Injects slow Redis cache operations to trigger Redis P95 > 200ms and >25% downstream duration
      send_req "GET" "/api/products/redis-slow/?delay=2.0" "" "Redis: Heavy Key Scan / Iteration Delay (2.0s)"
      send_req "GET" "/api/cache-stats/" "" "Redis: Cache Stats & Memory Info"
      send_req "GET" "/api/products/" "" "Django: Product Catalog View"
      ;;

    internal)
      # Injects pure internal Django execution latency without slow DB or Redis
      send_req "GET" "/api/products/slow/?delay=1.5" "" "Django Internal: Simulated Compute / Handler Delay (1.5s)"
      send_req "GET" "/api/products-tmpl/" "" "Django Internal: Template Rendering Loop"
      ;;

    error)
      # Injects 500 error spikes on endpoints to trigger error rate > 2% / 5%
      send_req "GET" "/api/products/error/" "" "Django: 500 Internal Server Error Spike"
      send_req "GET" "/api/template-error/" "" "Django: Multi-Part Template Rendering Crash"
      send_req "POST" "/api/products/" '{"invalid":"payload"}' "Django: 400/500 Validation Failure"
      ;;

    traffic)
      # Floods fast endpoint with high request rate to trigger >200% RPS anomaly while latency remains low
      send_req "GET" "/api/products/" "" "Django: High Throughput Traffic Surge"
      send_req "GET" "/api/products/health/" "" "Health: Multi-Service Health Ping"
      ;;

    chaos)
      # Multi-issue simultaneous chaos
      send_req "GET" "/api/raw-sql/?query=SELECT%20pg_sleep(1.5)%2C%20COUNT(*)%20FROM%20api_product" "" "PostgreSQL: Slow Query (Chaos)"
      send_req "GET" "/api/products/redis-slow/?delay=1.5" "" "Redis: Slow Cache (Chaos)"
      send_req "GET" "/api/products/error/" "" "Django: 500 Error (Chaos)"
      ;;

    healthy)
      # Fast, standard healthy traffic with normal latency and 0 errors
      send_req "GET" "/api/products/" "" "Django: Products List (Healthy)"
      send_req "GET" "/api/products/read-slave1/" "" "Postgres: Slave1 Read (Healthy)"
      send_req "GET" "/api/cache-stats/" "" "Redis: Cache Stats (Healthy)"
      ;;
    esac

    [ "$(echo "$SLEEP_DELAY > 0" | bc 2>/dev/null || echo 1)" = "1" ] && sleep "$SLEEP_DELAY"
  done
}

echo -e "${C_YELLOW}🚀 Starting anomaly injection for ${DURATION}s... (Press Ctrl+C to abort)${C_RESET}"
echo ""

# Launch concurrent workers
PIDS=()
for ((w = 1; w <= CONCURRENCY; w++)); do
  run_scenario_worker "$w" &
  PIDS+=($!)
done

# Wait for all workers to complete
trap 'kill "${PIDS[@]}" 2>/dev/null || true; exit 1' INT TERM
wait "${PIDS[@]}" 2>/dev/null || true

echo ""
echo -e "${C_GREEN}${C_BOLD}✔ Anomaly generation cycle complete!${C_RESET}"
echo -e "Open the Grafana dashboard to view the updated overview:"
echo -e "  ${C_CYAN}http://localhost:3000/d/tracenest-project-catalog${C_RESET}"
