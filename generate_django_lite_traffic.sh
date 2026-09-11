#!/bin/bash
# Traffic generator for pure Django lite app (zero Postgres, zero Redis)

BASE_URL="${DJANGO_LITE_URL:-http://localhost:8003}"

echo "============================================================"
echo "Generating traffic for pure Django Lite app (no DB, no Redis)"
echo "Target: $BASE_URL"
echo "============================================================"

for i in $(seq 1 30); do
  # 1. Root health check
  curl -s "$BASE_URL/" > /dev/null
  
  # 2. Users endpoint
  curl -s "$BASE_URL/api/users/" > /dev/null
  
  # 3. Orders endpoint
  curl -s "$BASE_URL/api/orders/" > /dev/null
  
  # 4. Slow endpoint
  if [ $((i % 5)) -eq 0 ]; then
    curl -s "$BASE_URL/api/slow/" > /dev/null
  fi

  # 5. Occasional error (10% error rate simulation)
  if [ $((i % 10)) -eq 0 ]; then
    curl -s "$BASE_URL/api/error/" > /dev/null
  fi
  
  printf "."
  sleep 0.2
done

echo ""
echo "Done! Traffic sent to Django Lite app."
