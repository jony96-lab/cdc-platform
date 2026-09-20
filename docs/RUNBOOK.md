# Runbook — Diagnóstico y recuperación

Síntoma → diagnóstico → acción. Basado en incidentes **reales** ocurridos durante
el despliegue de esta plataforma.

## 1. Conector/task en FAILED o pipeline sin datos

**Diagnóstico**
```powershell
scripts\status.ps1                          # estado + traza corta
# traza completa:
Invoke-RestMethod http://localhost:8083/connectors/<nombre>/status | fl
docker logs cdc-connect --tail 100
```

**Causas típicas y arreglo**

| Traza | Causa | Arreglo |
|---|---|---|
| `Communications link failure` / `Failed to establish connection` | BD del host caída o inalcanzable | Arrancar servicio MySQL80/PostgreSQL_Data_D; luego **reiniciar la task** (Portal ↻ o `POST /connectors/<n>/restart?includeTasks=true`). Las tasks FAILED NO reintentan solas. |
| `Access denied for user` | Credenciales mal rotadas | Actualizar `secrets/*.properties` (FileConfigProvider recarga solo) o re-registrar config |
| `Cannot replicate because the master purged required binary logs` | Conector caído más que la retención del binlog (7d) | Re-snapshot: borrar offsets del conector y reiniciar, o recrearlo |
| `Could not initialize dead letter queue` + `InvalidReplicationFactorException` | DLQ con RF=3 en broker único | `errors.deadletterqueue.topic.replication.factor=1` (ya está en los JSON) |
| Sink: `Unable to determine Dialect without JDBC metadata` | No alcanza PostgreSQL | Igual que fila 1: levantar PG y reiniciar task |

> **Caso real (2026-09-14)**: reboot del host → servicios de BD `Manual` no
> arrancaron → source y sink en FAILED. Se dispararon solas `MysqlSourceDown`,
> `PostgresTargetDown`, `CdcTaskFailed` y `CdcHeartbeatStalled` (todas llegaron al
> Portal vía webhook). Recuperación: arrancar servicios + ↻ Reiniciar en el Portal.
> **Integridad verificada**: tras 12+ h de apagón, source y destino quedaron en
> 22|11|21 filas (customers|products|orders) — cero pérdida, el conector retomó
> desde el offset del binlog.

## 2. Conectores UNASSIGNED tras scale-in

**Síntoma**: `connector=UNASSIGNED` y task apuntando a un worker ya eliminado;
restart/PUT config no lo resuelve.

**Arreglo** (ya automatizado en `scale-in.ps1`):
```powershell
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml restart connect
```
Las configs viven en `_cdc_connect_configs` — no se pierde nada.

> **Caso real**: al retirar `connect-worker2`, el rebalance quedó atascado con el
> source en UNASSIGNED. El restart del worker restante lo limpió en ~40 s.

## 3. Lag alto (CdcSourceLagHigh)

1. Grafana → CDC Overview: ¿lag del source o lag del sink?
   - **Lag sink** (`records_lag_max` del consumer del sink alto): destino lento →
     revisar PostgreSQL (locks, checkpoint), subir batch del sink, o `tasks.max`
     con más particiones.
   - **Lag source** (`millisecondsbehindsource` alto): MySQL produciendo más que
     la captura → revisar CPU del worker Connect, `max.batch.size`, red.
2. ¿Batch grande en curso? (`scripts\mutate.ps1 -Action batch` genera picos sanos).

## 4. Registros en el DLQ (`_cdc_dlq_jdbc_sink`)

```powershell
# Ver cuántos:
docker exec -e KAFKA_OPTS= cdc-kafka /opt/kafka/bin/kafka-get-offsets.sh `
  --bootstrap-server localhost:19092 --topic _cdc_dlq_jdbc_sink
```
Inspeccionar mensajes en Kafbat → topic → Messages (los headers
`__debezium_*` traen el error exacto). Causa común: tipo de dato incompatible o
tabla destino modificada a mano. Tras arreglar la causa, los registros del DLQ se
pueden re-producir al topic original (reproceso manual).

## 5. CdcHeartbeatStalled con pipeline corriendo

- ¿Pipeline pausado a propósito? → ignorar (la alerta lo dice).
- ¿Source RUNNING pero sin heartbeats? → revisar `docker logs cdc-connect` y
  conectividad a MySQL. El heartbeat viaja cada 10 s.

## 6. PostgreSQL: slot de replicación reteniendo WAL (si PG se usa como fuente)

```sql
SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retenido
FROM pg_replication_slots;
-- Slot huérfano de un pipeline borrado:
SELECT pg_drop_replication_slot('debezium_<slug>');
```
> Con MySQL como fuente no aplica (el binlog expira por tiempo). Vigilar disco C:
> (`binlog_expire_logs_seconds=604800` = 7 días de binlogs en `C:\ProgramData\MySQL\...`).

## 7. Kafka: disco del volumen

Grafana → Kafka & Connect → "Tamaño de logs por topic". Umbral de alerta: 5 GB.
- Crecen los topics de datos → bajar `retention.ms` por topic o dejar correr (7d).
- `_cdc_schema_history` es compacto: no debería crecer mucho.

## 8. Portal caído / sin datos

- El pipeline **sigue corriendo** (Portal es solo observabilidad/gestión).
- `docker logs cdc-portal`; healthcheck: `curl http://localhost:8085/healthz`.
- Sin estado en el dashboard → Connect REST caído (ver §9).
- SQLite del Portal: volumen `cdc-portal-data` (solo metadatos y alertas — se
  puede recrear sin riesgo).

## 9. Kafka Connect no responde

```powershell
docker logs cdc-connect --tail 100
docker compose -f docker-compose.yml restart connect
```
Si el broker está caído: `docker logs cdc-kafka`. Ojo: herramientas CLI dentro del
contenedor kafka necesitan `-e KAFKA_OPTS=` (el javaagent JMX ya usa el puerto).

## 10. Contenedor→host no conecta (preflight FAIL en TCP)

```powershell
# como ADMIN:
scripts\firewall-fix.ps1      # reglas inbound 3306/5433 desde 172.16.0.0/12 y 192.168.0.0/16
```
Si persiste: ¿servicio escuchando en `0.0.0.0`? (`netstat -ano | findstr :3306`).
PostgreSQL: ¿reglas en `pg_hba.conf` + `SELECT pg_reload_conf()`?

## 11. Healthcheck de kafka falla pero el broker funciona

Síntoma histórico: el healthcheck heredaba `KAFKA_OPTS` con el `-javaagent` JMX y
chocaba con el puerto 7071 ya ocupado por el broker. Ya está corregido en el
compose (`KAFKA_OPTS=` vacío en el test). Si se modifica el healthcheck, mantener
ese prefijo.

## 12. Reset completo (último recurso)

```powershell
scripts\down.ps1 -RemoveVolumes        # borra Kafka, Grafana, Portal (SQLite)
# Opcional: limpiar destino
#   DROP TABLE cdc_inventory_*; en cdc_target
scripts\up.ps1                         # re-snapshot completo desde MySQL
scripts\e2e.ps1                        # verificar
```
Las BDs del host NO se tocan: MySQL es la fuente de verdad; el destino se
reconstruye con el snapshot inicial.

## Escalado de severidad

- **critical** (conector caído, BD inaccesible, broker/worker caído): actuar ya;
  el pipeline está detenido (pero no se pierden datos mientras el binlog aguante).
- **warning** (lag, DLQ, heartbeat, disco): actuar en el día.
- Contacto: configurar el receptor `email` en `config/alertmanager/alertmanager.yml`
  (está comentado con plantilla) y recargar Alertmanager.
