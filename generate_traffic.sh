#!/usr/bin/env bash
# ==============================================================================
# generate_traffic.sh - Comprehensive Observability Traffic Generator
# ==============================================================================
# Sends realistic, diverse traffic across all sample app endpoints:
#   - Database primary and read replicas (Postgres default, slave1 via PgBouncer; slave2, slave3 direct)
#   - Individual DB targeting via CLI flags (--primary, --slave1, --slave2, --slave3, --pgbouncer, --db <name>)
#   - PgBouncer connection pooler targeting (--pgbouncer)
#   - Redis caching and key-value operations (--redis)
#   - Outgoing HTTP calls to external services (--http)
#   - Fast & slow requests (p50/p95 latency variations)
#   - 429 Rate Throttling bursts (--throttle)
#   - 500 Server Errors & Template waterfall crash spans (--error)
#   - Multi-DB sync & cross-database transfer workflows (--all-dbs)
# ==============================================================================

set -e

BASE_URL="${BASE_URL:-http://localhost:8001}"
MODE="loop"
DURATION=0
SLEEP_DELAY=0.3
VERBOSE=0
CONCURRENCY=1
REQUEST_COUNT=0
WORKER_DIR=""
WORKER_ID=0

# Service selection flags
SERVICE_PRODUCT=0
SERVICE_REDIS=0
SERVICE_HTTP=0
SERVICE_TEMPLATE=0
SERVICE_SQL=0
SERVICE_THROTTLE=0
SERVICE_ERROR=0
SERVICE_BOTO=0

# Database-specific selection flags
TARGET_DB_PRIMARY=0
TARGET_DB_SLAVE1=0
TARGET_DB_SLAVE2=0
TARGET_DB_SLAVE3=0
TARGET_DB_PGBOUNCER=0
TARGET_DB_ALL=0

# Color codes
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_RED="\033[31m"
C_BLUE="\033[34m"
C_CYAN="\033[36m"
C_MAGENTA="\033[35m"

# Counters
COUNT_TOTAL=0
COUNT_2XX=0
COUNT_4XX=0
COUNT_5XX=0
START_TIME=$(date +%s)

# Parse CLI arguments
while [[ "$#" -gt 0 ]]; do
  case $1 in
  --once) MODE="once" ;;
  --loop) MODE="loop" ;;
  -d | --duration)
    DURATION="$2"
    MODE="duration"
    shift
    ;;
  -s | --sleep)
    SLEEP_DELAY="$2"
    shift
    ;;
  -c | --concurrency)
    CONCURRENCY="$2"
    shift
    ;;
  -n | --count)
    REQUEST_COUNT="$2"
    shift
    ;;
  -u | --url)
    BASE_URL="$2"
    shift
    ;;
  -v | --verbose) VERBOSE=1 ;;

  # Database specific spam flags
  --primary | --default) TARGET_DB_PRIMARY=1 ;;
  --slave1) TARGET_DB_SLAVE1=1 ;;
  --slave2) TARGET_DB_SLAVE2=1 ;;
  --slave3) TARGET_DB_SLAVE3=1 ;;
  --pgbouncer) TARGET_DB_PGBOUNCER=1 ;;
  --all-dbs | --sync) TARGET_DB_ALL=1 ;;
  --db | --database)
    case "$2" in
    default | primary) TARGET_DB_PRIMARY=1 ;;
    slave1) TARGET_DB_SLAVE1=1 ;;
    slave2) TARGET_DB_SLAVE2=1 ;;
    slave3) TARGET_DB_SLAVE3=1 ;;
    pgbouncer) TARGET_DB_PGBOUNCER=1 ;;
    all) TARGET_DB_ALL=1 ;;
    *)
      echo -e "${C_RED}Unknown database: $2 (Available: default, slave1, slave2, slave3, pgbouncer, all)${C_RESET}"
      exit 1
      ;;
    esac
    shift
    ;;

  # General service flags
  -p | --product) SERVICE_PRODUCT=1 ;;
  -r | --redis) SERVICE_REDIS=1 ;;
  -x | --http) SERVICE_HTTP=1 ;;
  -m | --template) SERVICE_TEMPLATE=1 ;;
  -a | --sql) SERVICE_SQL=1 ;;
  -k | --throttle) SERVICE_THROTTLE=1 ;;
  -y | --error) SERVICE_ERROR=1 ;;
  -b | --boto | --s3) SERVICE_BOTO=1 ;;

  -h | --help)
    echo -e "${C_BOLD}Usage: $0 [OPTIONS]${C_RESET}"
    echo ""
    echo "Options:"
    echo "  --loop            Run continuously until Ctrl+C (default)"
    echo "  --once            Run a single sweep through all endpoint scenarios"
    echo "  -d, --duration N  Run continuously for N seconds"
    echo "  -s, --sleep SEC   Delay between request cycles in seconds (default: 0.3)"
    echo "  -c, --concurrency N  Run N parallel workers (default: 1)"
    echo "  -n, --count N        Run exactly N requests then exit (default: unlimited)"
    echo "  -u, --url URL     Base URL (default: http://localhost:8001)"
    echo "  -v, --verbose     Print response body snippets"
    echo "  -h, --help        Show this help message"
    echo ""
    echo "Database-Specific Flags (Spam one DB at a time):"
    echo "  --primary         Spam PostgreSQL Primary (default via PgBouncer:6432) [Reads, Writes, Tx]"
    echo "  --slave1          Spam PostgreSQL Replica 1 (slave1 via PgBouncer:6432) [Reads, Writes, Updates]"
    echo "  --slave2          Spam PostgreSQL Replica 2 (slave2 direct) [Reads, Writes, Updates]"
    echo "  --slave3          Spam PostgreSQL Replica 3 (slave3 direct) [Reads, Writes, Updates]"
    echo "  --pgbouncer       Spam PgBouncer Pooler targets (default & slave1 databases)"
    echo "  --all-dbs         Spam cross-database sync & transfer across all 4 DBs"
    echo "  --db <name>       Target a specific DB (default, slave1, slave2, slave3, pgbouncer, all)"
    echo ""
    echo "Other Service Flags:"
    echo "  -p, --product     Spam product service endpoints"
    echo "  -r, --redis       Spam Redis cache endpoints"
    echo "  -x, --http        Spam HTTP downstream call endpoints"
    echo "  -b, --boto, --s3  Spam Boto / S3 object storage endpoints"
    echo "  -m, --template    Spam HTML template rendering endpoints"
    echo "  -a, --sql         Spam SQL/threaded operation endpoints"
    echo "  -k, --throttle    Spam rate throttling burst endpoints"
    echo "  -y, --error       Spam 500 error endpoint endpoints"
    echo ""
    exit 0
    ;;
  *)
    echo "Unknown parameter: $1"
    exit 1
    ;;
  esac
  shift
done

# Check if any specific database target was selected
HAS_DB_FILTER=0
if [ "$TARGET_DB_PRIMARY" -eq 1 ] || [ "$TARGET_DB_SLAVE1" -eq 1 ] || [ "$TARGET_DB_SLAVE2" -eq 1 ] || [ "$TARGET_DB_SLAVE3" -eq 1 ] || [ "$TARGET_DB_PGBOUNCER" -eq 1 ] || [ "$TARGET_DB_ALL" -eq 1 ]; then
  HAS_DB_FILTER=1
fi

# Print banner
echo -e "${C_CYAN}${C_BOLD}"
echo "================================================================="
echo "   🚀  APM Observability Traffic Generator"
echo "   Target Base URL: ${BASE_URL}"
echo "   Mode: ${MODE} $([ $DURATION -gt 0 ] && echo "(for ${DURATION}s)")"
echo "   Concurrency: ${CONCURRENCY} worker(s)"
if [ "$HAS_DB_FILTER" -eq 1 ]; then
  echo -n "   Target Database(s): "
  [ "$TARGET_DB_PRIMARY" -eq 1 ] && echo -n "[Primary (PgBouncer)] "
  [ "$TARGET_DB_SLAVE1" -eq 1 ] && echo -n "[slave1 (PgBouncer)] "
  [ "$TARGET_DB_SLAVE2" -eq 1 ] && echo -n "[slave2 (Direct)] "
  [ "$TARGET_DB_SLAVE3" -eq 1 ] && echo -n "[slave3 (Direct)] "
  [ "$TARGET_DB_PGBOUNCER" -eq 1 ] && echo -n "[PgBouncer (default + slave1)] "
  [ "$TARGET_DB_ALL" -eq 1 ] && echo -n "[All Databases Synced] "
  echo ""
fi
echo "=================================================================${C_RESET}"

# Cleanup on exit
trap cleanup INT TERM

cleanup() {
  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))
  [ $ELAPSED -eq 0 ] && ELAPSED=1
  RPS=$(awk "BEGIN {printf \"%.2f\", $COUNT_TOTAL / $ELAPSED}")

  echo -e "\n${C_BOLD}${C_CYAN}=================================================================${C_RESET}"
  echo -e "${C_BOLD}📊 Traffic Generation Summary:${C_RESET}"
  echo -e "   Total Requests: ${C_BOLD}${COUNT_TOTAL}${C_RESET} (${RPS} req/s over ${ELAPSED}s)"
  echo -e "   ${C_GREEN}✔ 2xx Successes:${C_RESET}  ${COUNT_2XX}"
  echo -e "   ${C_BLUE}ℹ 4xx Throttled:${C_RESET}  ${COUNT_4XX}"
  echo -e "   ${C_RED}✖ 5xx Errors:${C_RESET}     ${COUNT_5XX}"
  echo -e "${C_BOLD}${C_CYAN}=================================================================${C_RESET}"
  [ -n "$WORKER_DIR" ] && rm -rf "$WORKER_DIR"
  exit 0
}

# Helper function to send an HTTP request
send_req() {
  local method="$1"
  local path="$2"
  local data="$3"
  local desc="$4"
  local url="${BASE_URL}${path}"

  COUNT_TOTAL=$((COUNT_TOTAL + 1))

  local response
  local http_code
  local body

  if [ "$method" == "POST" ]; then
    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
      -H "Content-Type: application/json" \
      -d "$data" --max-time 10 2>/dev/null || echo "000")
  else
    response=$(curl -s -w "\n%{http_code}" -X GET "$url" --max-time 10 2>/dev/null || echo "000")
  fi

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | sed '$d')

  local code_color="${C_GREEN}"
  if [[ "$http_code" =~ ^2 ]]; then
    COUNT_2XX=$((COUNT_2XX + 1))
    code_color="${C_GREEN}"
  elif [[ "$http_code" =~ ^4 ]]; then
    COUNT_4XX=$((COUNT_4XX + 1))
    code_color="${C_BLUE}"
  else
    COUNT_5XX=$((COUNT_5XX + 1))
    code_color="${C_RED}"
  fi

  printf " [%s%04d] %-6s %-36s -> ${code_color}%-3s${C_RESET} (%s)\n" \
    "$([ "$WORKER_ID" -gt 0 ] && echo "W${WORKER_ID}:" || echo "")" "$COUNT_TOTAL" "$method" "$path" "$http_code" "$desc"

  if [ "$VERBOSE" -eq 1 ] && [ -n "$body" ]; then
    echo -e "        ${C_YELLOW}Response:${C_RESET} $(echo "$body" | head -c 120)..."
  fi
}

# Run one full traffic cycle with diverse scenarios
run_traffic_cycle() {
  local cycle_num="$1"
  echo -e "\n${C_BOLD}${C_MAGENTA}--- Cycle #${cycle_num} ---${C_RESET}"

  local rnd=$((RANDOM % 900 + 100))

  # =========================================================================
  # If specific DB filters are passed, only spam the targeted database(s)
  # =========================================================================
  if [ "$HAS_DB_FILTER" -eq 1 ]; then
    if [ "$TARGET_DB_PRIMARY" -eq 1 ]; then
      echo -e "   ${C_BLUE}🔹 Spamming PostgreSQL Primary (default via PgBouncer)...${C_RESET}"
      send_req "GET" "/api/products/" "" "Postgres Primary (PgBouncer): List Products"
      send_req "GET" "/api/products/health/" "" "HEALTH"
      send_req "GET" "/api/products/1/" "" "Postgres Primary (PgBouncer): Product Detail #1"
      send_req "POST" "/api/products/" "{\"name\": \"Primary-Widget-$rnd\", \"price\": \"29.99\", \"stock\": 10}" "Postgres Primary (PgBouncer): Create Product"
      send_req "POST" "/api/products/bulk_create/" "[{\"name\":\"PrimaryA-$rnd\",\"price\":\"15.00\",\"stock\":5}]" "Postgres Primary (PgBouncer): Bulk Create"
      send_req "POST" "/api/products/1/adjust_stock/" "{\"delta\": -1}" "Postgres Primary (PgBouncer): Stock Adjustment"
      send_req "POST" "/api/db-tx/" "{\"operations\": 3}" "Postgres Primary (PgBouncer): Multi-Statement Tx"
      send_req "POST" "/api/multi-db/?db=default&action=update" "" "Postgres Primary (PgBouncer): Raw UPDATE"
      send_req "GET" "/api/multi-db/?db=default" "" "Postgres Primary (PgBouncer): Raw SELECT"
    fi

    if [ "$TARGET_DB_SLAVE1" -eq 1 ]; then
      echo -e "   ${C_BLUE}🔹 Spamming PostgreSQL Replica 1 (slave1 via PgBouncer)...${C_RESET}"
      send_req "GET" "/api/products/read-slave1/" "" "Postgres slave1 (PgBouncer): Read Products"
      send_req "POST" "/api/products/write-slave1/" "{\"name\": \"Item-Slave1-$rnd\", \"price\": \"19.99\", \"stock\": 15}" "Postgres slave1 (PgBouncer): Direct Write"
      send_req "POST" "/api/multi-db/?db=slave1&action=insert" "" "Postgres slave1 (PgBouncer): Raw INSERT"
      send_req "POST" "/api/multi-db/?db=slave1&action=update" "" "Postgres slave1 (PgBouncer): Raw UPDATE"
      send_req "GET" "/api/multi-db/?db=slave1" "" "Postgres slave1 (PgBouncer): Raw SELECT"
    fi

    if [ "$TARGET_DB_PGBOUNCER" -eq 1 ]; then
      echo -e "   ${C_YELLOW}⚡ Spamming PgBouncer Pooler Targets (default & slave1 DBs)...${C_RESET}"
      send_req "GET" "/api/products/" "" "PgBouncer [default]: List Products"
      send_req "GET" "/api/products/read-slave1/" "" "PgBouncer [slave1]: Read Products"
      send_req "POST" "/api/products/write-slave1/" "{\"name\": \"Item-Slave1-$rnd\", \"price\": \"19.99\", \"stock\": 15}" "PgBouncer [slave1]: Direct Write"
      send_req "POST" "/api/products/1/adjust_stock/" "{\"delta\": -1}" "PgBouncer [default]: Stock Adjustment"
      send_req "POST" "/api/multi-db/?db=slave1&action=update" "" "PgBouncer [slave1]: Raw UPDATE"
      send_req "GET" "/api/multi-db/?db=default" "" "PgBouncer [default]: Raw SELECT"
    fi

    if [ "$TARGET_DB_SLAVE2" -eq 1 ]; then
      echo -e "   ${C_BLUE}🔹 Spamming PostgreSQL Replica 2 (slave2 direct)...${C_RESET}"
      send_req "GET" "/api/products/read-slave2/" "" "Postgres slave2 (Direct): Read Products"
      send_req "POST" "/api/products/write-slave2/" "{\"name\": \"Item-Slave2-$rnd\", \"price\": \"39.99\", \"stock\": 30}" "Postgres slave2 (Direct): Direct Write"
      send_req "POST" "/api/multi-db/?db=slave2&action=insert" "" "Postgres slave2 (Direct): Raw INSERT"
      send_req "POST" "/api/multi-db/?db=slave2&action=update" "" "Postgres slave2 (Direct): Raw UPDATE"
      send_req "GET" "/api/multi-db/?db=slave2" "" "Postgres slave2 (Direct): Raw SELECT"
    fi

    if [ "$TARGET_DB_SLAVE3" -eq 1 ]; then
      echo -e "   ${C_BLUE}🔹 Spamming PostgreSQL Replica 3 (slave3 direct)...${C_RESET}"
      send_req "GET" "/api/products/read-slave3/" "" "Postgres slave3 (Direct): Read Products"
      send_req "POST" "/api/products/write-slave3/" "{\"name\": \"Item-Slave3-$rnd\", \"price\": \"89.99\", \"stock\": 45}" "Postgres slave3 (Direct): Direct Write"
      send_req "POST" "/api/multi-db/?db=slave3&action=insert" "" "Postgres slave3 (Direct): Raw INSERT"
      send_req "POST" "/api/multi-db/?db=slave3&action=update" "" "Postgres slave3 (Direct): Raw UPDATE"
      send_req "GET" "/api/multi-db/?db=slave3" "" "Postgres slave3 (Direct): Raw SELECT"
    fi

    if [ "$TARGET_DB_ALL" -eq 1 ]; then
      echo -e "   ${C_MAGENTA}🌀 Spamming Cross-Database Synchronization across All 4 DBs...${C_RESET}"
      send_req "POST" "/api/products/sync-all-dbs/" "{\"name\": \"Synced-$rnd\", \"price\": \"99.00\", \"stock\": 100}" "Multi-DB: Sync Across All 4 Databases"
      send_req "POST" "/api/products/multi-db-transfer/" "{\"amount\": 3}" "Multi-DB: Cross-DB Stock Transfer & Audit"
    fi

    return
  fi

  # =========================================================================
  # Standard Full Traffic Cycle (All services and databases)
  # =========================================================================
  if [ "$SERVICE_PRODUCT" -eq 1 ] || [ "$SERVICE_REDIS" -eq 0 ] && [ "$SERVICE_HTTP" -eq 0 ] && [ "$SERVICE_TEMPLATE" -eq 0 ] && [ "$SERVICE_SQL" -eq 0 ] && [ "$SERVICE_THROTTLE" -eq 0 ] && [ "$SERVICE_ERROR" -eq 0 ]; then
    # 1. Standard Products List & Detail (Postgres default via PgBouncer)
    send_req "GET" "/api/products/" "" "Postgres Primary (PgBouncer): List Products"
    send_req "GET" "/api/products/1/" "" "Postgres Primary (PgBouncer): Product Detail #1"

    # 2. Database Read Replicas (Postgres Slaves 1, 2, 3 - slave1 via PgBouncer)
    send_req "GET" "/api/products/read-slave1/" "" "Postgres Replica 1 (PgBouncer) query"
    send_req "GET" "/api/products/read-slave2/" "" "Postgres Replica 2 (Direct) query"
    send_req "GET" "/api/products/read-slave3/" "" "Postgres Replica 3 (Direct) query"

    # 3. Redis Cache operations & Health Check (Cache hit/miss + DBs & Redis)
    send_req "GET" "/api/products/cache/" "" "Redis: Cache Get/Set"
    send_req "GET" "/api/cache-stats/" "" "Redis: Cache Backend Stats"
    send_req "GET" "/api/products/health/" "" "Full Health Check: All 4 Postgres DBs + Redis"

    # 4. Outgoing HTTP Downstream Call & Cloud Storage (requests & boto tracing)
    send_req "GET" "/api/products/external/" "" "HTTP Client: Downstream Call"
    send_req "GET" "/api/s3-storage/" "" "Cloud Storage (Boto/S3): List Objects"

    # 5. POST Operations & Transactions (Primary + Replicas)
    send_req "POST" "/api/products/" "{\"name\": \"Widget-$rnd\", \"description\": \"Auto-generated product $rnd\", \"price\": \"$((rnd % 50 + 5)).99\", \"stock\": $((rnd % 200 + 1))}" "Postgres Primary: Create Product"
    send_req "POST" "/api/products/bulk_create/" "[{\"name\":\"BulkA-$rnd\",\"price\":\"12.50\",\"stock\":10},{\"name\":\"BulkB-$rnd\",\"price\":\"25.00\",\"stock\":20}]" "Postgres Primary: Bulk Create (Atomic)"
    send_req "POST" "/api/products/1/adjust_stock/" "{\"delta\": -1}" "Postgres Primary: Atomic Stock Adjustment"
    send_req "POST" "/api/db-tx/" "{\"operations\": 3}" "Postgres Primary: Multi-Statement DB Tx"

    # 6. Direct Database Writes to Replicas (Postgres Slave 1, 2, 3)
    send_req "POST" "/api/products/write-slave1/" "{\"name\": \"Item-Slave1-$rnd\", \"price\": \"19.99\", \"stock\": 15}" "Postgres Slave 1: Direct Write"
    send_req "POST" "/api/products/write-slave2/" "{\"name\": \"Item-Slave2-$rnd\", \"price\": \"39.99\", \"stock\": 30}" "Postgres Slave 2: Direct Write"
    send_req "POST" "/api/products/write-slave3/" "{\"name\": \"Item-Slave3-$rnd\", \"price\": \"89.99\", \"stock\": 45}" "Postgres Slave 3: Direct Write"

    # 7. Cross-Database Synchronization & Multi-DB Stock Transfer
    send_req "POST" "/api/products/sync-all-dbs/" "{\"name\": \"Synced-$rnd\", \"price\": \"99.00\", \"stock\": 100}" "Multi-DB: Sync Across All 4 Databases"
    send_req "POST" "/api/products/multi-db-transfer/" "{\"amount\": 3}" "Multi-DB: Cross-DB Stock Transfer & Audit"
    send_req "POST" "/api/multi-db/?db=slave1&action=update" "" "Raw SQL: Direct UPDATE on Slave1"
    send_req "POST" "/api/multi-db/?db=slave2&action=insert" "" "Raw SQL: Direct INSERT on Slave2"

    # 8. Rate Throttling Burst (Send 6 quick requests to trigger 429)
    echo -e "   ${C_BLUE}⚡ Triggering 429 Rate Throttling Burst (6 reqs)...${C_RESET}"
    for i in {1..6}; do
      send_req "GET" "/api/products/throttled/" "" "Throttled Endpoint (Call $i/6)"
    done

    # 9. Deliberate Errors (500 Server Error & Multi-part Template Error)
    send_req "GET" "/api/products/error/" "" "500 Error: RuntimeError for Tracing"
    send_req "GET" "/api/template-error/" "" "500 Error: Multi-part Template Crash"
  fi

  # Template endpoints
  if [ "$SERVICE_TEMPLATE" -eq 1 ]; then
    send_req "GET" "/api/products-tmpl/" "" "HTML: Product List Template"
    send_req "GET" "/api/partial/_header/" "" "HTML Partial: Header"
  fi

  # SQL/Threaded endpoints
  if [ "$SERVICE_SQL" -eq 1 ]; then
    send_req "GET" "/api/threaded/?threads=3&delay=0.05" "" "Threads: 3 Parallel Tasks"
    send_req "GET" "/api/raw-sql/?query=SELECT%20COUNT(*)%20FROM%20api_product" "" "Raw SQL: Direct Query"
    send_req "GET" "/api/manual-span/" "" "OTel: Custom Manual Span"
  fi

  # HTTP downstream endpoints
  if [ "$SERVICE_HTTP" -eq 1 ]; then
    send_req "GET" "/api/products/external/" "" "HTTP Client: Downstream Call"
  fi

  # Redis endpoints
  if [ "$SERVICE_REDIS" -eq 1 ]; then
    send_req "GET" "/api/products/cache/" "" "Redis: Cache Get/Set"
    send_req "GET" "/api/cache-stats/" "" "Redis: Cache Backend Stats"
  fi

  # Boto / S3 Cloud Storage endpoints
  if [ "$SERVICE_BOTO" -eq 1 ]; then
    send_req "GET" "/api/s3-storage/" "" "Cloud Storage (Boto/S3): List Objects"
  fi
}

# Worker loop function (used by concurrent workers)
run_worker_loop() {
  local wid="$1"
  local worker_counts_file="${WORKER_DIR}/counts_${wid}"

  worker_save_counts() {
    echo "${COUNT_TOTAL} ${COUNT_2XX} ${COUNT_4XX} ${COUNT_5XX}" > "$worker_counts_file"
  }

  trap 'worker_save_counts; exit 0' INT TERM
  trap 'worker_save_counts' EXIT

  local cycle=1
  while true; do
    run_traffic_cycle "$cycle" 2>/dev/null

    [ "$MODE" == "once" ] && break

    if [ "$REQUEST_COUNT" -gt 0 ] && [ "$COUNT_TOTAL" -ge "$REQUEST_COUNT" ]; then
      break
    fi

    if [ "$MODE" == "duration" ]; then
      local now
      now=$(date +%s)
      [ $((now - START_TIME)) -ge "$DURATION" ] && break
    fi

    cycle=$((cycle + 1))
    sleep "$SLEEP_DELAY"
  done

  worker_save_counts
}

# Main Execution Loop
if [ "$CONCURRENCY" -gt 1 ]; then
  # Concurrent mode: launch N parallel workers
  WORKER_DIR=$(mktemp -d)
  trap 'kill 0; wait; cleanup' INT TERM

  echo -e "   ${C_YELLOW}⚡ Launching ${CONCURRENCY} concurrent workers...${C_RESET}"

  for i in $(seq 1 "$CONCURRENCY"); do
    WORKER_ID=$i run_worker_loop "$i" &
  done

  wait

  # Aggregate counts from all workers
  COUNT_TOTAL=0
  COUNT_2XX=0
  COUNT_4XX=0
  COUNT_5XX=0
  for f in "${WORKER_DIR}"/counts_*; do
    [ -f "$f" ] || continue
    read -r wt w2 w4 w5 < "$f"
    COUNT_TOTAL=$((COUNT_TOTAL + wt))
    COUNT_2XX=$((COUNT_2XX + w2))
    COUNT_4XX=$((COUNT_4XX + w4))
    COUNT_5XX=$((COUNT_5XX + w5))
  done

  cleanup
else
  # Single worker mode (original behavior)
  cycle=1
  while true; do
    run_traffic_cycle "$cycle"

    if [ "$MODE" == "once" ]; then
      break
    fi

    if [ "$REQUEST_COUNT" -gt 0 ] && [ "$COUNT_TOTAL" -ge "$REQUEST_COUNT" ]; then
      break
    fi

    if [ "$MODE" == "duration" ]; then
      CURRENT_TIME=$(date +%s)
      if [ $((CURRENT_TIME - START_TIME)) -ge "$DURATION" ]; then
        break
      fi
    fi

    cycle=$((cycle + 1))
    sleep "$SLEEP_DELAY"
  done

  cleanup
fi
