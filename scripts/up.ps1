# Levanta toda la plataforma CDC.
# Uso:
#   scripts/up.ps1               # stack contra las BDs del host
#   scripts/up.ps1 -WithDemoDb   # agrega MySQL+Postgres containerizados (perfil demodb)
#   scripts/up.ps1 -SkipConnectors
param([switch]$WithDemoDb, [switch]$SkipConnectors, [switch]$NoBuild)
. (Join-Path $PSScriptRoot 'common.ps1')

Set-Location $script:RepoRoot

Write-Host "`n=== 0. Docker daemon ===" -ForegroundColor Cyan
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker no esta corriendo. Arranca Docker Desktop y reintenta." }
Write-Host "  OK" -ForegroundColor Green

Write-Host "`n=== 1. Secrets y SQL renderizados ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'gen-secrets.ps1')

Write-Host "`n=== 2. Build + up ===" -ForegroundColor Cyan
$files = @('-f', 'docker-compose.yml', '-f', 'docker-compose.monitoring.yml')
if ($WithDemoDb) {
    $files += '-f'; $files += 'docker-compose.demodb.yml'
    $env:COMPOSE_PROFILES = 'demodb'
}
$upArgs = @('up', '-d')
if (-not $NoBuild) { $upArgs += '--build' }
& docker compose @files @upArgs
if ($LASTEXITCODE -ne 0) { throw "docker compose up fallo" }

Write-Host "`n=== 3. Esperando healthchecks ===" -ForegroundColor Cyan
Wait-ContainerHealthy 'cdc-kafka' 240
Write-Host "  OK kafka healthy" -ForegroundColor Green
Wait-ContainerHealthy 'cdc-connect' 300
Write-Host "  OK connect healthy" -ForegroundColor Green
Wait-ContainerHealthy 'cdc-portal' 120
Write-Host "  OK portal healthy" -ForegroundColor Green

if (-not $SkipConnectors) {
    Write-Host "`n=== 4. Registro de conectores bootstrap ===" -ForegroundColor Cyan
    if ($WithDemoDb) { & (Join-Path $PSScriptRoot 'register-connectors.ps1') -Demo }
    else { & (Join-Path $PSScriptRoot 'register-connectors.ps1') }
}

$vars = Read-DotEnv
Write-Host "`n================ LISTO ================" -ForegroundColor Green
Write-Host ("  Portal de Pipelines : http://localhost:{0}" -f $vars['PORTAL_PORT'])
Write-Host ("  Kafbat UI           : http://localhost:{0}" -f $vars['KAFBAT_PORT'])
Write-Host ("  Grafana             : http://localhost:{0}  (admin / ver GRAFANA_ADMIN_PASSWORD en .env)" -f $vars['GRAFANA_PORT'])
Write-Host ("  Prometheus          : http://localhost:{0}" -f $vars['PROM_PORT'])
Write-Host ("  Alertmanager        : http://localhost:{0}" -f $vars['ALERTMANAGER_PORT'])
Write-Host ("  Connect REST        : http://localhost:{0}" -f $vars['CONNECT_PORT'])
Write-Host "========================================" -ForegroundColor Green
Write-Host " Tests E2E: scripts\e2e.ps1" -ForegroundColor Cyan
