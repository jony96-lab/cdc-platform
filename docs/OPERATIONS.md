# Operaciones

Guía de operación diaria de la plataforma CDC.

## Ciclo de vida del stack

```powershell
scripts\up.ps1                  # levanta todo + registra pipeline bootstrap
scripts\up.ps1 -WithDemoDb      # idem + BDs containerizadas (perfil demodb)
scripts\up.ps1 -SkipConnectors  # solo infraestructura
scripts\down.ps1                # baja todo (conserva datos)
scripts\down.ps1 -RemoveVolumes # baja todo y BORRA volúmenes (Kafka, Grafana…)
scripts\status.ps1              # contenedores + conectores + tasks
scripts\logs.ps1 connect -Follow
```

> Tras un **reboot del host**: el stack Docker se recupera solo
> (`restart: unless-stopped`), pero los servicios de BD son `Manual` —
> arrancarlos y reiniciar tasks fallidas (RUNBOOK §1).

## Gestión de pipelines

### Desde el Portal (recomendado, sin CLI)

1. `http://localhost:8085` → **+ Nuevo pipeline**
2. Completar origen (motor, host, puerto, BD, usuario, tablas) y destino.
3. **▶ Ejecutar pre-flight**: valida reachability, binlog/wal, privilegios y
   plugins. Muestra exactamente qué falla y cómo arreglarlo.
4. **🚀 Crear pipeline**: crea el par source+sink, espera RUNNING y lo registra.
5. Detalle del pipeline: estado, tasks, traza de errores, topics, configuración
   (sin secretos), y acciones ⏸ ▶ ↻ 🗑.

El pipeline bootstrap (`mysql-source-inventory` + `jdbc-sink-postgres`) se gestiona
por JSON versionado en `connectors/` y aparece en el Portal como "no gestionado".

### Desde JSON (GitOps)

```powershell
# editar connectors/*.json (placeholders {{VAR}} se resuelven desde .env)
scripts\register-connectors.ps1        # POST o PUT idempotente + espera RUNNING
scripts\backup-connectors.ps1          # backup de TODAS las configs a backups/
```

Restore de un backup:

```powershell
$cfg = (Get-Content backups\connectors-<ts>\<nombre>.json | ConvertFrom-Json).config
Invoke-RestMethod -Method Put -Uri "http://localhost:8083/connectors/<nombre>/config" `
  -ContentType application/json -Body ($cfg | ConvertTo-Json -Depth 10)
```

### Desde Kafbat UI

`http://localhost:8080` → Kafka Connect: ver/editar configs, reiniciar
conectores y tasks, gestionar topics y consumer groups.

## Operaciones comunes

| Tarea | Cómo |
|---|---|
| Pausar/reanudar un pipeline | Portal → detalle → ⏸ / ▶ (o `PUT /connectors/<n>/pause`) |
| Reintentar task FALLADA | Portal → ↻ Reiniciar (o `POST /connectors/<n>/restart?includeTasks=true`) |
| Ver eventos en vivo | Kafbat → Topics → `cdc.inventory.<tabla>` → Messages |
| Ver DLQ | Kafbat → topic `_cdc_dlq_jdbc_sink` |
| Generar carga de prueba | `scripts\mutate.ps1 -Action all -Times 5` / `-Action batch` (50 orders) |
| Lag del pipeline | Grafana → CDC Overview, o Portal (columna Lag fuente) |
| Agregar tabla al pipeline | El source usa `database.include.list=inventory` → toda tabla nueva de `inventory` se captura sola (topics y tablas destino se crean automáticamente) |
| Re-snapshot completo | Borrar offsets del conector (`DELETE /connectors/<n>/offsets`) y reiniciar, o recrear el conector con `snapshot.mode=initial` |

## Scale-out / scale-in

```powershell
scripts\scale-out.ps1   # 2º worker Connect + muestra distribución de tasks
scripts\scale-in.ps1    # retira worker 2 + reinicia worker 1 (rebalance limpio)
```

Más paralelismo por pipeline: subir `tasks.max` en el source (hasta #tablas) y
particiones de los topics. El sink JDBC conviene en `tasks.max=1` por tabla si se
requiere orden estricto.

## Mantenimiento

- **Retención de eventos**: 7 días por topic (`topic.creation.default.retention.ms`).
  Ajustable por conector. Los topics internos de Connect son compactos.
- **Binlog MySQL**: expira a los 7 días (`binlog_expire_logs_seconds=604800`).
  Si el conector estará caído más que eso, al volver habrá que re-snapshotear.
- **Backup de configs**: `scripts\backup-connectors.ps1` (programable en Task
  Scheduler). Los DATOS no requieren backup de Kafka: la fuente de verdad es MySQL.
- **Limpieza de topics huérfanos** (pipelines borrados):
  ```powershell
  docker exec -e KAFKA_OPTS= cdc-kafka /opt/kafka/bin/kafka-topics.sh `
    --bootstrap-server localhost:19092 --delete --topic p_<slug>.inventory.customers,...
  ```
  (el borrado de un pipeline en el Portal NO borra topics ni tablas destino — a propósito)

## Actualizar versiones

- **Debezium**: cambiar `DEBEZIUM_CONNECT_IMAGE` y `DBZ_JDBC_PLUGIN_VERSION` en
  `.env` → `scripts\up.ps1` (rebuild). Leer antes las release notes por breaking
  changes; los offsets y configs sobreviven (viven en Kafka).
- **Kafka broker**: cambiar `KAFKA_IMAGE` → rebuild. Single-node: parada breve,
  datos persisten en el volumen `cdc-kafka-data`.
- **Portal/Kafbat/Grafana**: cambiar tag en `.env` → `up.ps1`.

## Agregar SQL Server como fuente (cuando haya instancia)

El plugin y el driver mssql-jdbc **ya están en la imagen**. Pasos:
1. Habilitar CDC en la BD: `EXEC sys.sp_cdc_enable_db` y por tabla
   `EXEC sys.sp_cdc_enable_table @source_schema=..., @source_table=..., @capture_instance=...`
2. Preflight del Portal con motor `sqlserver` (puerto 1433).
3. Wizard del Portal → crear pipeline. (El conector usa
   `io.debezium.connector.sqlserver.SqlServerConnector`.)

## Modo demodb (portabilidad total)

`up.ps1 -WithDemoDb` levanta `mysql:8.4` (GTID ON, binlog ROW) y `postgres:17`
(wal_level=logical) con el mismo seed `inventory`; `register-connectors.ps1 -Demo`
registra el pipeline sobre ellos (topics `cdcdemo.*`). Útil para demostrar la
plataforma en cualquier máquina sin BDs instaladas. Las credenciales se
renderizan desde `.env` a `build/demodb-init/` (gitignored).
