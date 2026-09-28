# Baja SOLO el modulo Lakehouse (proyecto cdc-lakehouse independiente).
# Los datos (MinIO/parquet, catalogo, offsets) se conservan.
# Uso: scripts/lakehouse-down.ps1
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot
$ErrorActionPreference = 'Continue'
docker compose -f lakehouse/docker-compose.yml --project-directory . down
Write-Host "Lakehouse detenido. Datos conservados (volumenes cdc-minio-data, cdc-trino-data, cdc-lakehouse-offsets)." -ForegroundColor Green
