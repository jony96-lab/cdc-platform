# Integración con Microsoft Fabric (guía)

Este documento describe cómo llevar los datos CDC de esta plataforma a **Microsoft
Fabric** (Lakehouse). No requiere cambios en el stack local; es una guía para cuando
exista un tenant con capacidad Fabric (hay trial gratuito).

## Camino A — Conectores CDC nativos de Fabric Eventstream (sin Kafka local)

Fabric Eventstream trae **conectores CDC propios** (Debezium embebido en el servicio):

| Origen soportado por Fabric | Nota |
|---|---|
| PostgreSQL Database CDC | requiere `wal_level=logical` (ya lo tenés) |
| MySQL Database CDC | requiere binlog ROW (ya lo tenés) |
| SQL Server (VM/on-prem) CDC | requiere CDC habilitado + gateway |
| Kafka (Apache Kafka, preview) | consume topics existentes |

El **destino Lakehouse** de Eventstream convierte los eventos a **Delta Lake**
automáticamente y los guarda en tablas del lakehouse.

**Requisitos**: capacidad Fabric o trial · la BD debe ser **accesible públicamente**
(o usar On-premises Data Gateway) · workspace con permisos Contributor+.

**Pasos**: Fabric → workspace → Real-Time Intelligence → Eventstream →
*Connect data sources* → elegir el conector CDC del motor → configurar conexión →
destino *Lakehouse* → Publicar.

## Camino B — Consumir los topics de Kafka de esta plataforma

Reutiliza la infraestructura que ya corre: Fabric Eventstream consume los topics
`cdc.inventory.*` (fuente "Apache Kafka", preview).

**Requisitos**: Kafka **públicamente alcanzable** (nube no puede alcanzar un
localhost). Opciones: túnel (ngrok/Cloudflare Tunnel), exponer el puerto con
seguridad (SASL/TLS — habría que habilitar autenticación en el broker), o
On-premises Data Gateway con VNet injection.

**Checklist**:
1. Exponer el broker (túnel o gateway) con las dos IPs anunciadas correctas
   (`advertised.listeners` — el host publicado debe resolver para Fabric).
2. Fabric → Eventstream → fuente *Apache Kafka* → bootstrap server + credenciales.
3. Destino *Lakehouse* → tablas Delta.
4. Solo mensajes JSON son previsualizables (nuestro formato es JSON ✓).

## Elección

| Criterio | Camino A (CDC nativo Fabric) | Camino B (nuestro Kafka) |
|---|---|---|
| Infra local extra | Ninguna | Ninguna (ya corre) |
| Duplica captura | Sí (Fabric lee el log de nuevo) | No (reconsume topics) |
| Control del formato | Menos (fabric decide) | Topics ya normalizados |
| Exposición de red | BD accesible pública | Kafka accesible pública |

Para clientes que ya usan Fabric y quieren simplicidad: **Camino A**.
Si ya invirtieron en este stack y quieren reusar eventos/historia: **Camino B**.
