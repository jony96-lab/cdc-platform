# Scale-out: agrega un segundo worker de Kafka Connect y muestra el rebalanceo.
# Uso: scripts/scale-out.ps1
param()
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot
$vars = Read-DotEnv
$ErrorActionPreference = 'Continue'

docker compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.scaleout.yml up -d connect-worker2
if ($LASTEXITCODE -ne 0) { throw "No se pudo arrancar connect-worker2 (¿ejecutaste up.ps1 primero?)" }

Write-Host "`nEsperando el rebalance del cluster Connect..." -ForegroundColor Cyan
Start-Sleep 20

$connect = "http://localhost:$($vars['CONNECT_PORT'])"
foreach ($n in (Invoke-RestMethod "$connect/connectors")) {
    $st = Invoke-RestMethod "$connect/connectors/$n/status"
    Write-Host ("  {0}: connector={1}" -f $n, $st.connector.state) -ForegroundColor Green
    foreach ($t in $st.tasks) {
        Write-Host ("      task {0} -> {1} @ {2}" -f $t.id, $t.state, $t.worker_id)
    }
}
Write-Host "`nSi las tasks estan en distintos workers (connect:8083 vs connect-worker2:8083), el balanceo funciona." -ForegroundColor Cyan
Write-Host "REST del worker 2: http://localhost:8084  |  Volver a 1 worker: scripts\scale-in.ps1"
