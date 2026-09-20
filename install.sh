#!/usr/bin/env bash
# ==============================================================
# Instalador de la Plataforma CDC (Linux / servidores)
#   ./install.sh            -> modo demo (100% containerizado)
#   ./install.sh --host     -> usa BDs del host (requiere .env completo)
#   ./install.sh --skip-tests
#
# Requisitos: docker + docker compose v2. Sin MySQL/Postgres instalados
# es el modo demo el que permite correr todo igualmente.
# ==============================================================
set -euo pipefail
cd "$(dirname "$0")"
MODE="${1:-demo}"

command -v docker >/dev/null || { echo "[X] docker no instalado"; exit 1; }
docker info >/dev/null 2>&1 || { echo "[X] Docker no esta corriendo"; exit 1; }
echo "[OK] Docker"

if [ ! -f .env ]; then
  cp .env.example .env
  # Credenciales CDC generadas
  gen() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24; }
  sed -i "s/^MYSQL_CDC_PASSWORD=.*/MYSQL_CDC_PASSWORD=$(gen)/" .env
  sed -i "s/^MYSQL_APP_PASSWORD=.*/MYSQL_APP_PASSWORD=$(gen)/" .env
  sed -i "s/^PG_CDC_PASSWORD=.*/PG_CDC_PASSWORD=$(gen)/" .env
  sed -i "s/^DEMO_DB_ROOT_PASSWORD=.*/DEMO_DB_ROOT_PASSWORD=$(gen)/" .env
  sed -i "s/^SQLSERVER_CDC_PASSWORD=.*/SQLSERVER_CDC_PASSWORD=$(gen)/" .env
  sed -i "s/^SQLSERVER_SA_PASSWORD=.*/SQLSERVER_SA_PASSWORD=$(gen)9!aA/" .env
  sed -i "s/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=$(gen)/" .env
  UUID=$(docker run --rm apache/kafka:4.3.1 /opt/kafka/bin/kafka-storage.sh random-uuid 2>/dev/null | tr -d '\r\n')
  [ -n "$UUID" ] && sed -i "s/^KAFKA_CLUSTER_ID=.*/KAFKA_CLUSTER_ID=$UUID/" .env
  echo "[OK] .env creado con credenciales CDC generadas"
fi

if [ "$MODE" = "--host" ]; then
  echo "[i] Modo host: ejecuta manualmente:"
  echo "      scripts/preflight-host.ps1   (PowerShell en Windows) o valida binlog/wal_level"
  echo "      scripts/provision-host-dbs.ps1"
  echo "    y luego:  docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d"
  exit 0
fi

echo "[i] Modo demo: todo containerizado"
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.demodb.yml up -d --build
COMPOSE_PROFILES=demodb docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.demodb.yml up -d

echo "[i] Esperando salud del stack..."
for i in $(seq 1 60); do
  ok=$(docker inspect --format '{{.State.Health.Status}}' cdc-kafka 2>/dev/null)
  [ "$ok" = "healthy" ] && break
  sleep 5
done

# Registrar pipeline demo (renderiza {{VARS}} desde .env y hace POST/PUT idempotente)
export $(grep -E '^(MYSQL_CDC_USER|PG_CDC_ROLE|CDC_SOURCE_DB|PG_TARGET_DB)=' .env | xargs)
CONNECT=http://localhost:8083
until curl -sf "$CONNECT/" >/dev/null 2>&1; do sleep 3; done
for f in connectors/demo/*.json; do
  rendered=$(sed -e "s/{{MYSQL_CDC_USER}}/$MYSQL_CDC_USER/g" \
                 -e "s/{{PG_CDC_ROLE}}/$PG_CDC_ROLE/g" \
                 -e "s/{{CDC_SOURCE_DB}}/$CDC_SOURCE_DB/g" \
                 -e "s/{{PG_TARGET_DB}}/$PG_TARGET_DB/g" "$f")
  name=$(echo "$rendered" | grep -o '"name"[^,]*' | head -1 | cut -d'"' -f4)
  code=$(curl -s -o /tmp/cdc-resp.json -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data "$rendered" "$CONNECT/connectors")
  if [ "$code" = "409" ]; then
    cfg=$(echo "$rendered" | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin)["config"]))')
    curl -sf -X PUT -H 'Content-Type: application/json' --data "$cfg" "$CONNECT/connectors/$name/config" >/dev/null
    echo "[OK] conector actualizado: $name"
  else
    echo "[OK] conector registrado: $name"
  fi
done

if [ "${2:-}" != "--skip-tests" ] && [ "${SKIP_TESTS:-}" != "1" ]; then
  docker compose --profile tools -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.demodb.yml run --rm tester
fi

cat <<EOF

================================================
 Instalacion completa
   Portal     : http://localhost:8085
   Grafana    : http://localhost:3000
   Kafbat     : http://localhost:8080
================================================
EOF
