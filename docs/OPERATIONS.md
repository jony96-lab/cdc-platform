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

### Alertas por email (módulo)

**Configuración desde el Portal** (recomendado): `http://localhost:8085/notifications`
→ preset Gmail / Outlook-M365 / SMTP genérico → **Guardar y aplicar** (el Portal
renderiza `alertmanager.yml` y recarga Alertmanager al instante) →
**📨 Enviar notificación de prueba** para validar el canal. Funciona con cualquier
SMTP estándar (Gmail: app-password de 16 chars con 2FA; Outlook/M365:
smtp.office365.com:587 — M365 puede exigir SMTP AUTH en el tenant).

**Alternativa por `.env`** (bootstrap sin UI): variables `ALERT_*` en `.env` →
`scripts\gen-secrets.ps1` renderiza. Precedencia: cuando el Portal guardó una vez,
la config que manda es la del Portal (marca `build/alertmanager/portal-notif.json`;
`gen-secrets` la respeta y no la pisa).

| Variable | Qué es |
|---|---|
| `ALERT_EMAIL_ENABLED` | `true`/`false` — deshabilita el email (quedan solo alertas en el Portal) |

Política implementada:
- **critical** → email inmediato (`group_wait=0s`, repeat cada 4 h mientras persista)
- **warning** → email agrupado (`group_wait=10m`, `group_interval=1h`) — anti-spam
- **resoluciones** → también se notifican (`send_resolved=true`)
- El **Portal siempre** recibe el webhook (historial completo), con o sin email

**Aplicar un cambio de configuración:**
```powershell
# via UI: Guardar en /notifications ya recarga solo.
# via .env:
scripts\gen-secrets.ps1
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d --force-recreate alertmanager
```

**Probar el canal sin tocar el pipeline** (alerta sintética, auto-expira en 5 min):
```powershell
$now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body ('[{"labels":{"alertname":"AlertaDePrueba","severity":"critical","instance":"test-manual","job":"manual"},"annotations":{"summary":"Notificacion de prueba","description":"Canal de email funcionando."},"startsAt":"'+$now+'"}]') "http://localhost:9093/api/v2/alerts"
```

**Teams/Slack/Telegram**: Alertmanager los soporta nativamente (salvo Teams: desde
mayo 2026 Microsoft retiró los webhooks entrantes — vía Power Automate Workflows,
puede requerir licencia Premium). Agregar otro canal = sumar un bloque `*_configs`
en el template `config/alertmanager/alertmanager.yml.tmpl`.

### Otros mantenimientos

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

## Agregar SQL Server como fuente o destino

El driver `mssql-jdbc` y el conector **ya están en la imagen de Connect**, y el
Portal valida todo antes de crear nada (CDC habilitado por BD y por tabla,
permisos `db_owner`/`VIEW SERVER STATE`, `SQL Agent` corriendo).

**Fuente SQL Server (requisitos):**
1. SQL Server Agent corriendo: `Start-Service SQLSERVERAGENT` (el capture job lo lee).
2. CDC por base: `USE <bd>; EXEC sys.sp_cdc_enable_db;` (requiere sysadmin).
3. CDC por tabla: `EXEC sys.sp_cdc_enable_table @source_schema='dbo', @source_name='<tabla>', @role_name=NULL, @supports_net_changes=0;`
4. Usuario Debezium: `db_owner` + `db_datareader` + `GRANT VIEW SERVER STATE` (o sysadmin directo).
5. Wizard del Portal: motor *SQL Server*, puerto 1433, tablas como `dbo.tabla`.

**Destino SQL Server:** el rol necesita `CREATE TABLE` en la BD destino
(`ALTER ROLE db_owner ADD MEMBER [usuario];`). El upsert usa MERGE nativo.

**Demo containerizada sin instalar nada:**
```powershell
$env:COMPOSE_PROFILES = 'demodb,sqlserver'
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.demodb.yml up -d
# levanta SQL Server 2022 (acepta EULA al hacer up) con base `inventory`,
# tablas, CDC habilitado y login cdc_user listos (sqlserver-init)
```
Nota: SQL Server demo agrega ~2 GB RAM (limitado con `MSSQL_MEMORY_LIMIT_MB`) y el
puerto 1433 al host.

**Plantillas GitOps de los 3 motores (origen y destino):** `connectors/engines/` —
copiar a `connectors/`, reemplazar `<slug>`/`<base_datos>`/`<tabla>` y ejecutar
`register-connectors.ps1`.

## Modo demodb (portabilidad total)

`up.ps1 -WithDemoDb` levanta `mysql:8.4` (GTID ON, binlog ROW) y `postgres:17`
(wal_level=logical) con el mismo seed `inventory`; `register-connectors.ps1 -Demo`
registra el pipeline sobre ellos (topics `cdcdemo.*`). Útil para demostrar la
plataforma en cualquier máquina sin BDs instaladas. Las credenciales se
renderizan desde `.env` a `build/demodb-init/` (gitignored).
