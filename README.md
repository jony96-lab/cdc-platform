# Plataforma CDC — MySQL → Kafka (Debezium) → PostgreSQL

Plataforma de **Change Data Capture** lista para levantar con Docker Compose:
captura cambios fila por fila en MySQL (binlog), los publica como eventos en Kafka
(Debezium) y los replica en tiempo real a PostgreSQL (JDBC sink, espejo exacto con
upsert + propagación de deletes + evolución de esquema básica).

Incluye **Portal web propio** para registrar pipelines sin línea de comandos,
**monitoreo completo** (Prometheus + Grafana + exporters) y **alertas por email**
(Gmail/SMTP: critical inmediato, warning agrupado, resoluciones — configurable en
`.env`, histórico en el Portal).

## Motores soportados (origen y destino, cualquier combinación)

| Motor | Origen (source) | Destino (sink JDBC) |
|---|---|---|
| MySQL 8.x / 9.x | ✅ listo (binlog) | ✅ listo (driver incluido) |
| PostgreSQL 17/18 | ✅ listo (pgoutput nativo) | ✅ listo (probado en esta instalación) |
| SQL Server 2017–2022 | ✅ listo — requiere CDC habilitado (`sp_cdc_enable_table`) + SQL Agent; el preflight del Portal lo valida | ✅ listo (driver incluido, upsert vía MERGE) |

- **Portal**: wizard con validación previa real para los 3 motores (alcanzabilidad,
  binlog/wal/CDC, permisos). Plantillas GitOps de todas las combinaciones en
  `connectors/engines/`.
- **Demo opcional de SQL Server**: `COMPOSE_PROFILES=demodb,sqlserver` levanta un
  SQL Server 2022 containerizado con base `inventory`, CDC habilitado y usuario CDC
  ya creado (README del perfil: ver `docker-compose.demodb.yml`).

```
MySQL 8.0 (host) ──binlog──► Debezium MySQL Connector ──► Kafka 4.3 (KRaft)
                                                              │
PostgreSQL 18 (host) ◄──upsert/delete/DDL── Debezium JDBC Sink
                                                              │
Portal :8085 · Kafbat :8080 · Grafana :3000 · Prometheus :9090 · Alertmanager :9093
```

## Quickstart (Windows + Docker Desktop)

Requisitos: Docker Desktop con WSL2, MySQL y PostgreSQL accesibles en el host
(o usar el modo `demodb` 100% containerizado, ver abajo), ~5 GB de RAM para el stack.

```powershell
# 1. Configurar entorno
copy .env.example .env
#    editar .env: MYSQL_ROOT_PASSWORD, PG_SUPERUSER_PASSWORD
#    (las credenciales CDC se generan solas; KAFKA_CLUSTER_ID ya viene generado)

# 2. Validar el host (servicios, puertos, binlog ROW, wal_level, conectividad Docker→host)
scripts\preflight-host.ps1
#    si falla la conectividad contenedor→host: scripts\firewall-fix.ps1 (como admin)

# 3. Crear usuarios CDC, sembrar schema demo `inventory`, crear BD destino `cdc_target`
scripts\provision-host-dbs.ps1

# 4. Levantar TODO (kafka, connect, portal, kafbat, prometheus, grafana, alertmanager, exporters)
#    y registrar el pipeline bootstrap
scripts\up.ps1

# 5. Verificar de punta a punta (snapshot, INSERT/UPDATE/DELETE, DDL, API del portal)
scripts\e2e.ps1
```

### URLs

| Interfaz | URL | Credenciales |
|---|---|---|
| **Portal de Pipelines** | http://localhost:8085 | — |
| Grafana (4 dashboards) | http://localhost:3000 | `admin` / `GRAFANA_ADMIN_PASSWORD` en `.env` |
| Kafbat UI (topics/mensajes/conectores) | http://localhost:8080 | — |
| Prometheus | http://localhost:9090 | — |
| Alertmanager | http://localhost:9093 | — |
| Kafka Connect REST | http://localhost:8083 | — |

### Probar el CDC en vivo

```powershell
scripts\mutate.ps1 -Action all -Times 3     # INSERT/UPDATE/DELETE de ejemplo en MySQL
# Mirar Portal (estado/lag), Grafana → "CDC · Overview" (eventos/s), y PostgreSQL:
#   SELECT * FROM cdc_inventory_customers ORDER BY id DESC LIMIT 5;
```

### Modo 100% containerizado (sin tocar BDs del host)

```powershell
scripts\up.ps1 -WithDemoDb                    # levanta mysql:8.4 y postgres:17 con seed
scripts\register-connectors.ps1 -Demo         # registra el pipeline demo (topics cdcdemo.*)
```

## Estructura del repositorio

```
docker-compose.yml              # núcleo: kafka, connect, portal, kafbat, tester
docker-compose.monitoring.yml   # overlay: prometheus, grafana, alertmanager, exporters
docker-compose.demodb.yml       # overlay: BDs containerizadas (perfil demodb)
docker-compose.scaleout.yml     # overlay: 2º worker de Connect (demo distribuida)
images/          # Dockerfiles: kafka(+JMX), connect(+JDBC sink+JMX), portal, tester
portal/          # Portal de Pipelines (FastAPI + HTMX + SQLite)
connectors/      # JSON de conectores bootstrap (fuente de verdad versionada)
config/          # jmx rules, prometheus, alertmanager, grafana (provisioning+dashboards)
sql/             # plantillas de provisioning (host y demodb)
scripts/         # automatización PowerShell (ver abajo)
tests/           # suite E2E pytest (17 tests)
secrets/         # renderizado local de credenciales (NUNCA se commitea)
docs/            # ARCHITECTURE.md, OPERATIONS.md, RUNBOOK.md
```

## Scripts

| Script | Qué hace |
|---|---|
| `preflight-host.ps1` | Diagnóstico completo del host antes de instalar nada |
| `firewall-fix.ps1` | (admin) reglas inbound 3306/5433 para la subred Docker/WSL |
| `provision-host-dbs.ps1` | Usuarios CDC + seed + BD destino + pg_hba (idempotente) |
| `up.ps1` / `down.ps1` | Levanta/baja todo (`-WithDemoDb`, `-RemoveVolumes`) |
| `status.ps1` | Contenedores + estado de conectores/tasks con un comando |
| `register-connectors.ps1` | Registra/actualiza conectores desde los JSON (`-Demo`) |
| `e2e.ps1` | Suite pytest en contenedor efímero |
| `mutate.ps1` | Genera cambios en el source para ver el CDC en acción |
| `backup-connectors.ps1` | Backup de todas las configs de conectores a `backups/` |
| `scale-out.ps1` / `scale-in.ps1` | Agrega/quita un 2º worker Connect (modo distribuido) |
| `logs.ps1 <servicio>` | Logs de cualquier servicio |
| `gen-secrets.ps1` | Renderiza SQL y `secrets/` desde `.env` (lo llama `up.ps1`) |

## Garantías y diseño

- **Exactamente-una entrega efectiva** en destino: upsert idempotente por PK +
  offsets de Connect persistidos en Kafka. Si el destino ya tiene la fila, la pisa.
- **Sin pérdida ante caídas**: offsets en `_cdc_connect_offsets`; el binlog de MySQL
  retiene 7 días (configurable). Probado con 12+ h de apagón (ver RUNBOOK).
- **Espejo exacto**: `insert.mode=upsert`, `delete.enabled=true`, `primary.key.mode=record_key`.
- **Evolución de esquema**: `schema.evolution=basic` — nuevas columnas se crean solas
  en destino (lazy: al llegar el primer registro con el schema nuevo).
- **Secretos fuera de git**: `.env` + `secrets/` (gitignored). Los conectores los
  referencian con `${file:...}` (FileConfigProvider), nunca en claro en las configs.
- **Dead letter queue**: registros que fallan en el sink van a `_cdc_dlq_jdbc_sink`
  con headers de contexto (`errors.tolerance=all`).

Más detalle: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
operación diaria: [docs/OPERATIONS.md](docs/OPERATIONS.md) ·
fallas y recuperación: [docs/RUNBOOK.md](docs/RUNBOOK.md)

## Llevarla a otra máquina o servidor

```powershell
git clone <repo> && cd cdc-platform
.\install.ps1        # pregunta demo (containerizado) vs host (tus BDs), y hace todo
```

Linux/servidores: `./install.sh` (modo demo, cero dependencias). Publicación,
versionado y ruta air-gapped: [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).

## Versiones (verificadas 2026-09)

Debezium **3.6.2.Final** (Kafka Connect 4.3) · Apache Kafka **4.3.1** (KRaft) ·
Kafbat UI **v1.5.0** · Prometheus **v3.13.3** · Grafana **13.0.8** ·
Alertmanager **v0.34.0** · mysqld-exporter **v0.20.0** · postgres-exporter **v0.20.1**

> Nota: `provectuslabs/kafka-ui` (abandonado en 2024), `quay.io/debezium/debezium-ui`
> (última imagen en 2023) y `bitnami/kafka` (retirado de Docker Hub) fueron evaluados
> y descartados. Detalles en ARCHITECTURE.md.
