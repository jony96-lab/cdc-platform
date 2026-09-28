# Consumir el lakehouse con Power BI

El lakehouse se expone por **Trino** (`localhost:8086`), y Power BI Desktop se
conecta a Trino por **ODBC**. Flujo: Power BI → ODBC DSN → Trino → Iceberg/Parquet
en MinIO.

## Paso 1 — Instalar el driver ODBC de Trino

1. Ir a https://github.com/trino-io/trino-odbc → **Releases** → descargar el
   instalador `.msi` de Windows (x64) más reciente.
2. Instalar (siguiente-siguiente). Es el driver open source mantenido por la
   comunidad de Trino.
   - Alternativa comercial con soporte: Simba ODBC de insightsoftware.
   - Alternativa sin driver: conector personalizado de Power BI (ver abajo).

## Paso 2 — Crear el DSN (Data Source)

1. `Win+R` → `odbcad32.exe` (usar la de **64 bits**) → pestaña **DSN de sistema** →
   **Agregar** → elegir **Trino ODBC Driver** (o el nombre que figure).
2. Configurar:
   - **Server**: `localhost`
   - **Port**: `8086`
   - **Catalog**: `iceberg`
   - **Schema**: `cdc`
   - **User**: `analista` (cualquiera; no hay password en este stack)
3. **Test connection** (el driver incluye test) → OK.

## Paso 3 — Power BI Desktop

1. **Obtener datos → ODBC** → elegir el DSN creado (o "Cadena de conexión")
2. Navegador: aparecen las tablas del lakehouse:
   - `lh_cdclh_public_ventas`
   - `lh_cdclh_public_inventario_bodega`
   - (todas las `lh_*`)
3. Elegir **Cargar** (import) o **Transformar datos** si querés modelar antes.

**Import vs DirectQuery**:
- **Import** (recomendado para empezar): Power BI copia los datos; el botón
  **Actualizar** vuelve a leer el lakehouse (siempre fresco al refrescar).
- **DirectQuery**: cada visual consulta Trino en vivo (pushdown de SQL). Más
  "real-time", depende de que Trino esté arriba.

## Paso 4 — Actualización programada (publicar al servicio)

Si publicás el informe a Power BI Service (app.powerbi.com), el refresh programado
necesita un **On-premises Data Gateway** instalado en esta máquina, con el mismo
DSN registrado dentro del gateway (Gateway → Conexiones → ODBC).

## Alternativa sin ODBC — conector personalizado

Existe un conector personalizado de Power BI para Trino (import mode) del proyecto
open source `trino-odbc` (carpeta `powerbi-connector-setup` en su documentación):
se copia el `.mez` a `Documentos\Power BI Desktop\Custom Connectors` y habilitar
"Allow custom connectors" en Power BI.

## Alternativa programática (ya incluida)

`examples/consumir_lakehouse.py` — Python habla directo con Trino por HTTP (sin
drivers): útil para notebooks, ETL en pandas, o integraciones propias.

## Notas

- **Seguridad**: Trino aquí corre sin autenticación en localhost. Si Power BI va a
  consultarlo desde otras máquinas, habilitar autenticación en Trino antes.
- **Tablas**: el lakehouse expone lo capturado; cada tabla origen =
  `lh_<prefijo>_<tabla>` con las columnas `__op/__table/__source_ts_ns/__db` de
  auditoría CDC (útiles para informes de "qué cambió").
- **Rendimiento**: Parquet columnar + pushdown: los filtros de Power BI
  (Import/DirectQuery) viajan a Trino y solo se leen los archivos necesarios.
