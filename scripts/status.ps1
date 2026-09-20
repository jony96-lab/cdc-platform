# Estado rapido: contenedores + conectores + lag.
# Uso: scripts/status.ps1
param()
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot
$vars = Read-DotEnv

Write-Host "`n=== Contenedores ===" -ForegroundColor Cyan
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml ps --format "table {{.Name}}\t{{.Status}}"

$connect = "http://localhost:$($vars['CONNECT_PORT'])"
Write-Host "`n=== Conectores (Kafka Connect) ===" -ForegroundColor Cyan
try {
    $names = Invoke-RestMethod -Uri "$connect/connectors" -TimeoutSec 10
    if ($names.Count -eq 0) { Write-Host "  (ningun conector registrado)" -ForegroundColor DarkGray }
    foreach ($n in $names) {
        $st = Invoke-RestMethod -Uri "$connect/connectors/$n/status" -TimeoutSec 10
        $connState = $st.connector.state
        $color = switch ($connState) { 'RUNNING' { 'Green' } 'PAUSED' { 'Yellow' } default { 'Red' } }
        Write-Host ("  {0,-28} connector={1}" -f $n, $connState) -ForegroundColor $color
        foreach ($t in $st.tasks) {
            $tcolor = switch ($t.state) { 'RUNNING' { 'Green' } 'PAUSED' { 'Yellow' } default { 'Red' } }
            Write-Host ("      task {0}: {1} @ {2}" -f $t.id, $t.state, $t.worker_id) -ForegroundColor $tcolor
            if ($t.trace) { Write-Host ("        trace: {0}" -f ($t.trace.Substring(0, [Math]::Min(300, $t.trace.Length)))) -ForegroundColor DarkYellow }
        }
    }
} catch {
    Write-Host "  Connect REST no responde en $connect" -ForegroundColor Red
}

Write-Host "`n=== Portal ===" -ForegroundColor Cyan
try {
    $h = Invoke-RestMethod -Uri "http://localhost:$($vars['PORTAL_PORT'])/healthz" -TimeoutSec 5
    Write-Host "  OK portal vivo" -ForegroundColor Green
} catch { Write-Host "  portal no responde" -ForegroundColor Red }
