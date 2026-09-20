# Logs de un servicio del stack.
# Uso: scripts/logs.ps1 connect [-Tail 500] [-Follow]
param([Parameter(Position = 0)][string]$Service = 'connect', [int]$Tail = 200, [switch]$Follow)
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot
$args = @('logs', '--tail', "$Tail")
if ($Follow) { $args += '-f' }
$args += $Service
& docker compose -f docker-compose.yml -f docker-compose.monitoring.yml @args
