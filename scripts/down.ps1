# Baja la plataforma.
# Uso: scripts/down.ps1 [-RemoveVolumes]  (RemoveVolumes borra datos de Kafka/Grafana/etc.)
param([switch]$RemoveVolumes, [switch]$WithDemoDb)
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot

$files = @('-f', 'docker-compose.yml', '-f', 'docker-compose.monitoring.yml')
if ($WithDemoDb) {
    $files += '-f'; $files += 'docker-compose.demodb.yml'
    $env:COMPOSE_PROFILES = 'demodb'
}
$args = @('down')
if ($RemoveVolumes) { $args += '-v' }
& docker compose @files @args
Write-Host "Stack detenido." -ForegroundColor Green
if ($RemoveVolumes) { Write-Host "Volumenes eliminados." -ForegroundColor Yellow }
