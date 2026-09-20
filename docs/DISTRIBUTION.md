# Distribuir la solución — llevarla a otra máquina o servidor

La plataforma está pensada como **producto portable**: repo + instalador + docs.
Este documento explica cómo publicarla y cómo la va a implementar otra persona.

## Qué recibe quien la adopta

| Pieza | Qué es |
|---|---|
| Repo (GitHub/GitLab) | Todo el código: compose, imágenes, portal, conectores, tests, docs |
| `install.ps1` | Instalador interactivo Windows: modo **demo** o **host**, genera `.env` con credenciales, preflight, provisioning, stack y tests |
| `install.sh` | Instalador Linux/servidor: modo demo 100% containerizado |
| `.env.example` | Único archivo que el usuario completa (contraseñas de sus BDs si usa modo host) |
| `docs/` | README (quickstart), ARCHITECTURE (diseño), OPERATIONS (operación diaria), RUNBOOK (fallas) |

## Requisitos del que adopta

- Docker Desktop (Windows/macOS) o Docker Engine + Compose v2 (Linux)
- ~5 GB de RAM libres para el stack (SQL Server demo: +2 GB si se activa)
- **Modo demo**: nada más. **Modo host**: sus MySQL/PostgreSQL/SQL Server con
  credenciales de admin para el provisioning

## Cómo la publica el dueño (vos)

```powershell
# 1. Crear repositorio remoto (GitHub/GitLab, privado o público)
git remote add origin https://github.com/<tu-usuario>/cdc-platform.git
git push -u origin master

# 2. Etiquetar versiones
git tag -a v1.0.0 -m "Primera version: MySQL->Kafka->PostgreSQL, portal, monitoreo, alertas, 3 motores listos"
git push --tags
```

Opcional para clientes sin acceso a internet (air-gapped) o para instalaciones
sin build: publicar las imágenes construidas al registry.

### Imágenes pre-buildeadas en ghcr.io (recomendado)

El repo publica las imágenes a **GitHub Container Registry** (mismo login que
GitHub). Quien adopta la plataforma elige:

```powershell
# Modo A - build local (por defecto, requiere compilar):
.\install.ps1

# Modo B - imágenes pre-buildeadas (sin compilar, rápido y bits idénticos):
#   en .env descomentar las CDC_*_IMAGE apuntando a ghcr.io/jony96-lab/... y:
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml pull
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

Variables que controlan las imágenes: `CDC_KAFKA_IMAGE`, `CDC_CONNECT_IMAGE`,
`CDC_PORTAL_IMAGE`, `CDC_TESTER_IMAGE` (ver `.env.example`). Cada cliente puede
apuntarlas a SU registry interno si lo necesita.

Para el dueño del proyecto (publicación):

```powershell
# login una vez (token PAT con scope write:packages):
docker login ghcr.io -u jony96-lab

# tag + push de las 4 imágenes:
docker tag cdc-platform/kafka:4.3.1    ghcr.io/jony96-lab/cdc-kafka:4.3.1
docker tag cdc-platform/connect:3.6.2  ghcr.io/jony96-lab/cdc-connect:3.6.2
docker tag cdc-platform/portal:1.0     ghcr.io/jony96-lab/cdc-portal:1.0
docker tag cdc-platform/tester:1.0     ghcr.io/jony96-lab/cdc-tester:1.0
docker push ghcr.io/jony96-lab/cdc-kafka:4.3.1
docker push ghcr.io/jony96-lab/cdc-connect:3.6.2
docker push ghcr.io/jony96-lab/cdc-portal:1.0
docker push ghcr.io/jony96-lab/cdc-tester:1.0
```

## Cómo la implementa otra persona

```powershell
git clone https://github.com/<tu-usuario>/cdc-platform.git
cd cdc-platform
.\install.ps1          # modo auto: pregunta demo vs host y hace todo
```

Linux/servidor:
```bash
git clone https://github.com/<tu-usuario>/cdc-platform.git
cd cdc-platform
./install.sh           # modo demo: cero dependencias de BDs del host
```

El instalador:
1. Valida Docker.
2. Crea `.env` desde `.env.example` **generando** las credenciales CDC
   (postgres/mysql/sqlserver/grafana/kafka cluster id) — el usuario solo completa
   las contraseñas de admin si elige modo host.
3. Modo demo: levanta MySQL+PostgreSQL containerizados con seed, registra el
   pipeline demo y corre la suite E2E (18 tests) como prueba de instalación.
4. Modo host: preflight (binlog/wal/conectividad), provisioning idempotente
   (usuarios CDC, BD destino, pg_hba), stack + pipeline + tests.

## Qué NO se versiona (ya en `.gitignore`)

- `.env` (credenciales reales) — cada instalación genera la suya
- `secrets/*` (renderizado local de credenciales para FileConfigProvider)
- `build/*` (SQLs renderizados) y `backups/`

## Personalización por cliente

Todo el comportamiento se parametriza por `.env`: puertos, motores, credenciales,
**canal de alertas por email (cada cliente pone su Gmail + app-password)**,
versiones de imágenes (`KAFKA_IMAGE`, `DEBEZIUM_CONNECT_IMAGE`, …). Los conectores
bootstrap viven en `connectors/*.json` con placeholders `{{VARS}}` resueltos desde
`.env` — cada cliente adapta sin tocar código.

## Roadmap sugerido para producto

1. **Autenticación en el Portal** (OAuth/simple login) si se expone más allá de LAN.
2. **Receptor de e-mail/Slack** en Alertmanager (plantilla ya incluida).
3. **Imagen de Connect pre-buildeada en registry** para acelerar installs.
4. Empaquetar como **Strimzi/K8s Helm chart** para clientes con Kubernetes
   (la ruta está documentada en ARCHITECTURE.md).
