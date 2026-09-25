# Levanta el modulo Lakehouse: MinIO + Debezium Server (Iceberg sink) + Trino.
# Asegura: render de configs, DB catalogo iceberg en PostgreSQL, bucket en MinIO.
# Uso: scripts/lakehouse-up.ps1
param([switch]$NoBuild)
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot
$ErrorActionPreference = 'Continue'
$vars = Read-DotEnv

if (-not $vars['LH_SOURCE_ENGINE']) { throw "Define LH_SOURCE_ENGINE (mysql|postgres) en .env" }
Write-Host "=== Lakehouse: origen = $($vars['LH_SOURCE_ENGINE']) ($($vars['LH_SOURCE_DB'])) ===" -ForegroundColor Cyan

docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker no esta corriendo." }

# 1. Render configs (gen-secrets renderiza lakehouse + el resto)
& (Join-Path $PSScriptRoot 'gen-secrets.ps1')

# 2. DB catalogo Iceberg en PostgreSQL del host (idempotente)
$sqlCat = Join-Path $script:RepoRoot 'build\host-sql\postgres\03-iceberg-catalog.sql'
if (Test-Path $sqlCat) {
    $psql = Find-PsqlExe
    $env:PGPASSWORD = $vars['PG_SUPERUSER_PASSWORD']
    $env:PGCLIENTENCODING = 'UTF8'
    & $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_SUPERUSER'] -d postgres -v ON_ERROR_STOP=1 -f $sqlCat
    if ($LASTEXITCODE -ne 0) { throw "Fallo la creacion de la DB catalogo $($vars['LH_CATALOG_DB'])" }
    Write-Host "  OK catalogo Iceberg ($($vars['LH_CATALOG_DB']) en PostgreSQL)" -ForegroundColor Green
}

# 3. Stack
$files = @('-f', 'docker-compose.yml', '-f', 'docker-compose.monitoring.yml', '-f', 'docker-compose.lakehouse.yml')
& docker compose @files --profile lakehouse-init up -d minio minio-init debezium-lakehouse trino
if ($LASTEXITCODE -ne 0) { throw "docker compose up (lakehouse) fallo" }

Wait-ContainerHealthy 'cdc-minio' 120
Write-Host "  OK minio healthy" -ForegroundColor Green
Wait-ContainerHealthy 'cdc-trino' 180
Write-Host "  OK trino healthy" -ForegroundColor Green

# 4. Esperar primer snapshot CDC en el lakehouse
Write-Host "  Esperando snapshot inicial en el lakehouse (puede tardar 1-3 min)..." -ForegroundColor Cyan
Start-Sleep 60
docker logs cdc-debezium-lakehouse --tail 5 2>&1 | Select-Object -Last 5

Write-Host ""
Write-Host "=== Lakehouse listo ===" -ForegroundColor Green
Write-Host "  Consola MinIO   : http://localhost:9001 ($($vars['LH_MINIO_ROOT_USER']))"
Write-Host "  SQL (Trino)     : scripts\lakehouse-sql.ps1   |   http://localhost:8086"
Write-Host "  Ejemplo         : SELECT * FROM iceberg.cdc.lh_inventory_customers LIMIT 10;"
