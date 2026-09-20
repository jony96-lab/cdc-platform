#!/bin/bash
# Genera connect-distributed.properties desde variables de entorno
# (control total sobre la config del worker, sin depender del wrapper de la imagen)
set -euo pipefail

CONFIG_FILE=/kafka/config/connect-distributed.properties
mkdir -p /kafka/config /kafka/logs

cat > "$CONFIG_FILE" <<EOF
bootstrap.servers=${BOOTSTRAP_SERVERS}
group.id=${GROUP_ID:-cdc-connect}

key.converter=${KEY_CONVERTER:-org.apache.kafka.connect.json.JsonConverter}
value.converter=${VALUE_CONVERTER:-org.apache.kafka.connect.json.JsonConverter}

offset.storage.topic=${OFFSET_STORAGE_TOPIC:-_cdc_connect_offsets}
offset.storage.replication.factor=${OFFSET_STORAGE_REPLICATION_FACTOR:-1}
offset.storage.partitions=${OFFSET_STORAGE_PARTITIONS:-5}
offset.flush.interval.ms=${OFFSET_FLUSH_INTERVAL_MS:-10000}

config.storage.topic=${CONFIG_STORAGE_TOPIC:-_cdc_connect_configs}
config.storage.replication.factor=${CONFIG_STORAGE_REPLICATION_FACTOR:-1}

status.storage.topic=${STATUS_STORAGE_TOPIC:-_cdc_connect_status}
status.storage.replication.factor=${STATUS_STORAGE_REPLICATION_FACTOR:-1}

rest.advertised.host.name=${REST_ADVERTISED_HOST_NAME:-connect}
rest.port=8083

plugin.path=/kafka/connect
plugin.discovery.mode=only_scan

config.providers=file
config.providers.file.class=org.apache.kafka.common.config.provider.FileConfigProvider

topic.creation.enable=true

producer.max.request.size=${PRODUCER_MAX_REQUEST_SIZE:-10485760}
EOF

echo "[cdc-entrypoint] Worker config generado en $CONFIG_FILE"
exec /kafka/bin/connect-distributed.sh "$CONFIG_FILE"
