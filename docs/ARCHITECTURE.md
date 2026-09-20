# Arquitectura

## Diagrama

```
┌─ HOST WINDOWS ─────────────────────────────────────────────────────────────┐
│  MySQL 8.0.44 (:3306)                     PostgreSQL 18.0 (:5433)          │
│  servicio MySQL80                         servicio PostgreSQL_Data_D       │
│  binlog ROW/FULL, expira 7d               wal_level=logical                │
│  └─ schema `inventory` (fuente)           └─ DB `cdc_target` (destino)     │
└──────────▲───────────────────────────────────────────▲─────────────────────┘
           │ host.docker.internal (extra_hosts: host-gateway)
┌─ DOCKER (red cdc-net) ──────┼───────────────────────────┼───────────────────┐
│          │ binlog (protocolo de replicación)            │ JDBC upsert/delete│
│  ┌───────┴────────────────────────────────────────────── ┴───────────────┐  │
│  │ Kafka Connect cluster (GROUP_ID=cdc-connect, modo distribuido)        │  │
│  │  · Debezium MySqlConnector      → topics cdc.inventory.*              │  │
│  │  · Debezium JdbcSinkConnector   ← topics.regex cdc\..*                │  │
│  │  · FileConfigProvider (secretos) · JMX exporter :7072                 │  │
│  └───────────────▲───────────────────────────────────────────────────────┘  │
│                  │                                                          │
│  ┌───────────────┴───────────────┐   ┌──────────────────────────────────┐  │
│  │ Kafka 4.3.1 KRaft single-node │   │ Portal de Pipelines :8085        │  │
│  │  · interno kafka:19092        │   │  FastAPI + HTMX + SQLite         │  │
│  │  · externo localhost:9092     │   │  · wizard + preflight real       │  │
│  │  · JMX exporter :7071         │   │  · estado/lag/acciones           │  │
│  └───────────────────────────────┘   │  · webhook de Alertmanager       │  │
│                                      │  · expone /metrics (estado)      │  │
│  Kafbat UI :8080                     └──────────────────────────────────┘  │
│  Prometheus :9090 ── reglas ──► Alertmanager :9093 ──webhook──► Portal      │
│  Grafana :3000 (4 dashboards provisionados)                                 │
│  mysqld-exporter · postgres-exporter                                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Flujo de datos

1. **Captura**: Debezium se conecta a MySQL como réplica (`server.id` propio), hace
   un *snapshot inicial* consistente y luego lee el binlog continuamente.
2. **Eventos**: cada INSERT/UPDATE/DELETE se publica en `cdc.inventory.<tabla>`
   (envelope Debezium: `before`/`after`/`op`/`source`), clave = PK de la fila →
   particionado consistente por PK (orden por fila garantizado, 3 particiones).
3. **Aplicación**: el JDBC sink consume `cdc\..*` y hace **upsert por PK**,
   **delete** con tombstone y **schema evolution básica** (columnas nuevas).
   Los errores van al DLQ `_cdc_dlq_jdbc_sink` con headers de contexto.
4. **Heartbeats**: el source emite un heartbeat cada 10 s a
   `__debezium-heartbeat.cdc` — mantiene fresco el offset del binlog y sirve de
   señal de vida para la alerta `CdcHeartbeatStalled`.

## Topics de Kafka

| Topic | Propósito | Retención |
|---|---|---|
| `cdc.inventory.*` | Eventos CDC de datos (3 particiones) | 7 días |
| `cdc` | Eventos de cambio de schema (DDL) | 7 días |
| `__debezium-heartbeat.cdc` | Heartbeats | default |
| `_cdc_schema_history` | Historia de schemas del source (compact) | compact |
| `_cdc_connect_configs/offsets/status` | Estado interno del cluster Connect | compact/RF1 |
| `_cdc_dlq_jdbc_sink` | Dead letter queue del sink | 7 días |

Nomenclatura destino: topic `cdc.inventory.customers` → tabla
`cdc_inventory_customers` en `cdc_target` (DefaultTableNamingStrategy: `.`→`_`).

## Decisiones de diseño

### Por qué Kafka + Kafka Connect (y no alternativas)

| Alternativa | Veredicto (verificado 2026-09) |
|---|---|
| **Debezium Server (sin Kafka)** | La doc oficial advierte que el sink JDBC no ofrece las mismas garantías: offset management, exactly-once y retries "may behave differently or may not be available". Sin log replayeable ni fan-out. |
| **Airbyte OSS** | Ya no soporta docker-compose: exige `abctl`, que levanta Kubernetes dentro de Docker (8 GB RAM, ~30 min de instalación). CDC = Debezium empaquetado con menos control. |
| **Flink CDC** | Válido y escalable, pero complejidad de estado/checkpoints desproporcionada para este caso. |
| **Canal / Maxwell** | Solo MySQL, sin sink JDBC genérico, mantenimiento limitado. |

Kafka Connect da: offsets duraderos, exactly-once del framework, rebalanceo de
tasks entre workers (scale-out real), REST API de gestión, DLQ, tolerancia a
fallos configurable y métricas JMX completas.

### Componentes de UI elegidos (y los descartados)

- **Portal propio (FastAPI + HTMX + SQLite)**: registro *guiado* de pipelines con
  **preflight real** (reachability, `binlog_format`, `wal_level`, privilegios,
  plugins) antes de crear nada. Sin npm ni build de frontend: un solo contenedor
  Python, mantenible por un equipo de datos.
- **Kafbat UI v1.5.0**: fork mantenido de provectus/kafka-ui para operación de
  Kafka (topics, mensajes, consumer groups, configs de conectores).
- **Grafana 13**: portal de monitoreo con dashboards provisionados como código.
- Descartados con evidencia: `provectuslabs/kafka-ui` (último release v0.7.2,
  **abril 2024**), `quay.io/debezium/debezium-ui` (última imagen **dic 2023**,
  aunque la doc estable de Debezium 3.6 aún la referencia), `bitnami/kafka`
  (retirado de Docker Hub).

### Imagen custom de Connect

`FROM quay.io/debezium/connect:3.6.2.Final` + plugin `debezium-connector-jdbc`
3.6.2.Final desde Maven Central (38.6 MB; drivers PostgreSQL/MySQL/MariaDB/MSSQL
incluidos — Oracle/Db2 no) + `jmx_prometheus_javaagent` 1.0.1. Entrypoint propio
que genera `connect-distributed.properties` desde env vars (control total, sin
depender del wrapper de la imagen). Los 13 conectores Debezium source quedan
disponibles para futuros pipelines (postgres, sqlserver, mongodb, oracle…).

### Modelo de secretos

- `.env` (gitignored) → `gen-secrets.ps1` → `secrets/*.properties` (gitignored).
- Los conectores referencian `${file:/opt/connect-secrets/<file>.properties:<key>}`
  (FileConfigProvider): **las contraseñas nunca viajan en las configs de Connect**
  ni quedan en los topics internos.
- Pipelines creados por el Portal: el password tipeado en el wizard se escribe en
  `secrets/pipeline_<slug>.properties` (chmod 600) y la config referencia el archivo.
- El Portal almacena solo metadatos (SQLite); las credenciales jamás tocan su DB.

### Multi-pipeline y escalabilidad

- Cada pipeline del Portal usa su propio `topic.prefix=p_<slug>`, `server.id`
  derivado, schema-history y DLQ independientes → pipelines aislados entre sí.
- **Scale-out horizontal**: `scale-out.ps1` agrega workers al mismo `GROUP_ID`;
  Connect rebalancea conectores/tasks automáticamente (probado: source en
  worker2, sink en worker1). Más paralelismo por pipeline: subir `tasks.max` +
  particiones (el orden por PK se preserva; el orden global no).
- **Ruta a producción**: las imágenes y configs son portables; el camino natural
  es Strimzi (Kafka + Connect en Kubernetes con CRDs) o MSK/Confluent Cloud +
  Debezium. El formato de connector JSON no cambia.

### Monitoreo y alertas

Cadena: JMX (broker/Connect/Debezium) + exporters (MySQL/PG) + Portal (estado de
conectores vía REST) → **Prometheus** (reglas en `config/prometheus/rules/cdc.yml`)
→ **Alertmanager** → webhook → **Portal** (`/alerts`, badge en nav) y/o e-mail
(receptor comentado, listo para configurar).

Alertas: `CdcConnectorDown`, `CdcTaskFailed` (critical); `CdcSourceLagHigh`,
`CdcHeartbeatStalled`, `CdcSinkErrors`/DLQ, `KafkaDiskGrowingFast` (warning);
`KafkaBrokerDown`, `ConnectWorkerDown`, `MysqlSourceDown`, `PostgresTargetDown`,
`PortalDown` (infra). Todas fueron probadas con incidentes reales (ver RUNBOOK).

Métricas Debezium clave (nombres reales vía jmx_exporter 1.0.1, lowercase sin
underscores): `debezium_mysql_streaming_millisecondsbehindsource`,
`debezium_mysql_streaming_totalnumberofeventsseen`,
`kafka_connect_task_error_deadletterqueue_produce_requests_total`, etc.

## Topología de red

- Los contenedores alcanzan las BDs del host vía `host.docker.internal`
  (`extra_hosts: host-gateway`, portable a Linux).
- Origen del tráfico visto por las BDs: subred NAT de WSL/Docker
  (`172.16.0.0/12` o `192.168.0.0/16`) → reglas en `pg_hba.conf` y usuario
  MySQL `'cdc_user'@'%'`.
- Kafka publica dos listeners: `kafka:19092` (interno) y `localhost:9092`
  (debug desde host).
- WSL2 está limitado a 10 GB / 4 CPU (`.wslconfig`); el stack completo usa ~4-5 GB.
