# Lakehouse — CDC → Parquet en MinIO, con SQL vía Trino

*(Módulo independiente: se levanta y baja sin tocar el pipeline JDBC.)*

## Conceptos (guía rápida)

| Pieza | Qué es | En nuestro stack |
|---|---|---|
| **Data lake** | Repositorio de archivos crudos en storage barato | MinIO (S3 API) |
| **Lakehouse** | Lake + gestión transaccional: archivos + esquema + ACID + evolución | MinIO + Apache Iceberg |
| **Parquet** | Formato de archivo **columnar** y comprimido. Leer solo las columnas necesarias + compresión = órdenes de magnitud más rápido que CSV/JSON para analítica | `data/*.parquet` |
| **Apache Iceberg** | Formato de **tabla** sobre los archivos: esquema versionado, snapshots ACID, upserts/deletes (reescritura de archivos), evolución de esquema, time-travel | Catálogo JDBC (metadata en PostgreSQL) |
| **Catálogo** | El "directorio" que sabe qué tablas existen, su esquema y dónde están sus archivos | `iceberg_catalog` DB en PostgreSQL |
| **Motor de consulta** | Traduce SQL a lecturas de Parquet (pushdown de filtros) | Trino (open source, `trinodb/trino`) |

**La cadena completa:**

```
MySQL/PostgreSQL (OLTP)
   │  WAL / binlog  ← el conector NO consulta tablas: lee el LOG de replicación
   ▼
Debezium Server (sink iceberg)
   │  eventos {op, before, after} → aplanados a filas
   │  upsert=true: UPDATE/DELETE reescriben archivos (ACID)
   ▼
MinIO (s3://lakehouse/warehouse)
   ├── cdc/lh_<tabla>/data/*.parquet      ← los datos, columnar
   ├── cdc/lh_offset_storage              ← offsets CDC (durabilidad: al reiniciar
   │                                         retoma donde quedó, sin re-snapshot)
   └── cdc/lh_schema_history              ← esquemas versionados del origen
   ▲
   └── metadata en PostgreSQL (iceberg_catalog): qué tablas existen,
       qué archivos componen cada snapshot

Trino  ──SELECT──►  lee catálogo → planifica → lee solo los Parquet necesarios
```

**Por qué Parquet/Iceberg y no tablas relacionales para analítica:** consultas
que leen pocas columnas son mucho más rápidas (columnar + pushdown), compresión
~5-10x, diseño orientado a escaneos masivos, y Iceberg permite evolucionar el
esquema sin reescribir historia.

## Uso

```powershell
# .env: LH_SOURCE_ENGINE = mysql | postgres  (origen parametrizable)
scripts\lakehouse-up.ps1          # renderiza, asegura catálogo, levanta, espera snapshot
scripts\lakehouse-sql.ps1 "SELECT * FROM iceberg.cdc.lh_cdclh_inventory_customers LIMIT 10"
scripts\lakehouse-sql.ps1         # consola SQL interactiva
```

- **Consola MinIO**: http://localhost:9001 (usuario: `LH_MINIO_ROOT_USER` de `.env`)
- **Trino**: http://localhost:8086

## Esquema de las tablas lakehouse

Cada tabla origen aterriza como `lh_<prefijo>_<tabla>` con:
- **todas las columnas del origen** (aplanadas del campo `after` del evento)
- columnas de linaje agregadas por el SMT: `__op` (c/u/r/d), `__table`,
  `__source_ts_ns`, `__db` y `__deleted`
- clave primaria = la del origen (para el upsert)

> Ojo con la nomenclatura: `lh_cdclh_inventory_customers` = prefijo `lh_` +
> topic `cdclh` + tabla origen `inventory.customers`.

## Reset del lakehouse (recuperación)

El snapshot en modo upsert es idempotente: si el historial de esquemas queda
desincronizado con el binlog (p.ej. tras un reboot con historial viejo), el
reset es seguro:

```powershell
# via Trino, DROP de las tablas lh_*  y luego:
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.lakehouse.yml up -d --force-recreate debezium-lakehouse
# re-snapshot completo y el espejo vuelve a cuadrar (verificado: 59|71 == 59|71)
```

Síntoma característico: `"Encountered change event for table X whose schema isn't
known to this connector"` en los logs del contenedor.

## Limitaciones conocidas

- **Micro-batches**: los cambios aterrizan en batches (segundos), no evento a evento.
- **Compaction**: Iceberg acumula archivos de cambios; en producción se programa
  `OPTIMIZE`/compaction (Trino lo soporta: `ALTER TABLE ... EXECUTE optimize`).
- Los tests E2E de lakehouse viven en `tests/test_lakehouse.py` (4 tests, vía API
  HTTP de Trino).
