# Scale-in: retira el segundo worker de Kafka Connect (las tasks vuelven al worker 1).
# Uso: scripts/scale-in.ps1
param()
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot
$ErrorActionPreference = 'Continue'

docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.scaleout.yml stop connect-worker2
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.scaleout.yml rm -f connect-worker2

# Tras retirar un worker, el rebalance puede quedar atascado (conectores UNASSIGNED
# apuntando al worker muerto). Reiniciar el worker restante lo limpia de forma determinista
# (las configs de conectores viven en topics de Kafka, no se pierden).
Write-Host "Reiniciando worker principal para forzar rebalance limpio..." -ForegroundColor Cyan
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml restart connect
Start-Sleep 25
$st = Invoke-RestMethod "http://localhost:8083/connectors" -TimeoutSec 15
foreach ($n in $st) {
    $s = Invoke-RestMethod "http://localhost:8083/connectors/$n/status" -TimeoutSec 15
    Write-Host ("  {0}: {1}/{2}" -f $n, $s.connector.state, $s.tasks[0].state) -ForegroundColor Green
}
Write-Host "`nWorker 2 retirado y cluster rebalanceado." -ForegroundColor Green
