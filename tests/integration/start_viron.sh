#!/usr/bin/env bash
# Start a throwaway local Viron (PostgreSQL + the Spring Boot service) for the integration
# suite and the playback demo, built from Preponderous-Software/viron at a pinned commit.
#
#   tests/integration/start_viron.sh          # clone (once), build, start, wait for health
#   tests/integration/start_viron.sh down     # stop and remove the containers and volumes
#
# Environment: JWT_SECRET (default: a fixed test secret), JWT_ISSUER (default viron-usd-test),
# VIRON_PORT (default 9999), VIRON_SRC (default .viron-src in the repository root).
set -euo pipefail

VIRON_COMMIT=c0a6fc54b66ddab63a9c90f4858fced55ea68e82
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export VIRON_SRC="${VIRON_SRC:-$ROOT/.viron-src}"
export JWT_SECRET="${JWT_SECRET:-viron-usd-integration-test-secret-0123456789}"
export JWT_ISSUER="${JWT_ISSUER:-viron-usd-test}"
export VIRON_PORT="${VIRON_PORT:-9999}"
COMPOSE=(docker compose -f "$HERE/compose.yml")

if [ "${1:-up}" = "down" ]; then
  "${COMPOSE[@]}" down -v
  exit 0
fi

if [ ! -d "$VIRON_SRC/.git" ]; then
  git clone --quiet https://github.com/Preponderous-Software/viron.git "$VIRON_SRC"
fi
git -C "$VIRON_SRC" fetch --quiet origin "$VIRON_COMMIT" || true
git -C "$VIRON_SRC" checkout --quiet --detach "$VIRON_COMMIT"
echo "Viron source: $VIRON_SRC @ $(git -C "$VIRON_SRC" rev-parse HEAD)"

"${COMPOSE[@]}" up -d --build

echo -n "waiting for Viron on http://localhost:$VIRON_PORT "
for _ in $(seq 1 120); do
  if curl -fsS "http://localhost:$VIRON_PORT/actuator/health" >/dev/null 2>&1; then
    echo " up"
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo " timed out"
"${COMPOSE[@]}" logs --tail 100 viron
exit 1
