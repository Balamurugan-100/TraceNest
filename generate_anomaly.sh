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
#   4. error      - Error rate spike (configurable error rate % via -e / --error-rate)
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
ERROR_RATE=100
DELAY=1.8
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
  echo -e "  ${C_RED}postgres${C_RESET} | ${C_RED}--postgres${C_RESET}     Inject PostgreSQL latency bottleneck"
  echo -e "  ${C_RED}redis${C_RESET}    | ${C_RED}--redis${C_RESET}        Inject Redis operations latency bottleneck"
  echo -e "  ${C_YELLOW}internal${C_RESET} | ${C_YELLOW}--internal${C_RESET}     Inject Django internal processing latency"
  echo -e "  ${C_RED}error${C_RESET}    | ${C_RED}--error${C_RESET}        Inject 500 error rate spike on endpoints"
  echo -e "  ${C_BLUE}traffic${C_RESET}  | ${C_BLUE}--traffic${C_RESET}      Inject traffic surge anomaly (>200% baseline, healthy latency)"
  echo -e "  ${C_MAGENTA}chaos${C_RESET}    | ${C_MAGENTA}--chaos${C_RESET}        Trigger multi-issue chaos (Postgres + Redis + Errors)"
  echo -e "  ${C_GREEN}healthy${C_RESET}  | ${C_GREEN}--healthy${C_RESET}      Send clean baseline traffic (0 issues in Needs Attention)"
  echo ""
  echo -e "${C_BOLD}Options:${C_RESET}"
  echo "  -e, --error-rate PCT Target error rate percentage (1-100, default: 100)"
  echo "  --delay SEC          Artificial delay in seconds (default: 1.8s)"
  echo "  -d, --duration N     Duration in seconds to run injection (default: 60)"
  echo "  -c, --concurrency N  Number of concurrent workers (default: 3)"
  echo "  -s, --sleep SEC      Sleep between requests in seconds (default: 0.02)"
  echo "  -u, --url URL        Base URL (default: http://localhost:8001)"
  echo "  -v, --verbose        Print response details"
  echo "  -h, --help           Show this help message"
  echo ""
  echo -e "${C_BOLD}Examples:${C_RESET}"
  echo "  $0 error -e 15                  # Generate exact 15% error rate"
  echo "  $0 error -e 50 -c 10            # Generate 50% error rate with 10 workers"
  echo "  $0 postgres --delay 3.0         # Inject 3.0s PostgreSQL queries"
  echo "  $0 redis --delay 2.5            # Inject 2.5s Redis operations"
  echo "  $0 traffic -c 8 -s 0            # Inject traffic surge"
  echo "  $0 healthy                      # Restore healthy baseline"
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
  -e | --error-rate | --percent)
    ERROR_RATE="$2"
    shift
    ;;
  --delay)
    DELAY="$2"
    shift
    ;;
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
if [ "$SCENARIO" = "error" ] || [ "$ERROR_RATE" -ne 100 ]; then
  echo "   Target Error %: ${ERROR_RATE}%"
fi
if [ "$SCENARIO" = "postgres" ] || [ "$SCENARIO" = "redis" ] || [ "$SCENARIO" = "internal" ]; then
  echo "   Latency Delay:  ${DELAY}s"
fi
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
      # Injects real slow PostgreSQL queries (pg_sleep) across DBs to trigger high Postgres P95 and >30% downstream duration
      send_req "GET" "/api/products/postgres-slow/?delay=${DELAY}" "" "PostgreSQL (default): Slow Query (${DELAY}s delay)"
      send_req "GET" "/api/products/postgres-slow/?db=slave1&delay=${DELAY}" "" "PostgreSQL (slave1): Slow Query (${DELAY}s delay)"
      send_req "GET" "/api/raw-sql/?query=SELECT%20pg_sleep(${DELAY})" "" "PostgreSQL: Raw SQL pg_sleep(${DELAY}s)"
      send_req "GET" "/api/products/" "" "Django: Product Catalog View"
      ;;

    redis)
      # Injects slow Redis cache operations to trigger Redis P95 > 200ms and >25% downstream duration
      send_req "GET" "/api/products/redis-slow/?delay=${DELAY}" "" "Redis: Heavy Key Scan / Iteration Delay (${DELAY}s)"
      send_req "GET" "/api/cache-stats/" "" "Redis: Cache Stats & Memory Info"
      send_req "GET" "/api/products/" "" "Django: Product Catalog View"
      ;;

    internal)
      # Injects pure internal Django execution latency without slow DB or Redis
      send_req "GET" "/api/products/slow/?delay=${DELAY}" "" "Django Internal: Simulated Compute / Handler Delay (${DELAY}s)"
      send_req "GET" "/api/products-tmpl/" "" "Django Internal: Template Rendering Loop"
      ;;

    error)
      # Generates exact target error rate percentage based on -e / --error-rate
      local roll=$(( RANDOM % 100 + 1 ))
      if [ "$roll" -le "$ERROR_RATE" ]; then
        # Send 500 error
        local err_type=$(( RANDOM % 2 ))
        if [ "$err_type" -eq 0 ]; then
          send_req "GET" "/api/products/error/" "" "Django: 500 Internal Server Error Spike"
        else
          send_req "GET" "/api/template-error/" "" "Django: Multi-Part Template Rendering Crash"
        fi
      else
        # Send normal 200 OK request
        send_req "GET" "/api/products/" "" "Django: Products List (200 OK)"
      fi
      ;;

    traffic)
      # Floods fast endpoint with high request rate to trigger >200% RPS anomaly while latency remains low
      send_req "GET" "/api/products/" "" "Django: High Throughput Traffic Surge"
      send_req "GET" "/api/products/health/" "" "Health: Multi-Service Health Ping"
      ;;

    chaos)
      # Multi-issue simultaneous chaos
      send_req "GET" "/api/products/postgres-slow/?delay=${DELAY}" "" "PostgreSQL: Slow Query (Chaos)"
      send_req "GET" "/api/products/redis-slow/?delay=${DELAY}" "" "Redis: Slow Cache (Chaos)"
      local roll=$(( RANDOM % 100 + 1 ))
      if [ "$roll" -le "$ERROR_RATE" ]; then
        send_req "GET" "/api/products/error/" "" "Django: 500 Error (Chaos)"
      else
        send_req "GET" "/api/products/" "" "Django: Product List (Chaos)"
      fi
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
