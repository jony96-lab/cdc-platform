# Registra (idempotente: POST o PUT) los conectores JSON de connectors/ contra Kafka Connect.
# Uso:
#   scripts/register-connectors.ps1         # pipeline contra BDs del host
#   scripts/register-connectors.ps1 -Demo   # pipeline contra BDs containerizadas (demodb)
param([switch]$Demo)
. (Join-Path $PSScriptRoot 'common.ps1')
$vars = Read-DotEnv

$connect = "http://localhost:$($vars['CONNECT_PORT'])"
$null = Wait-Http -Url $connect -TimeoutSec 120 -Label 'Kafka Connect REST'

$dir = if ($Demo) { Join-Path $script:RepoRoot 'connectors\demo' } else { Join-Path $script:RepoRoot 'connectors' }
$jsons = Get-ChildItem $dir -Filter *.json | Sort-Object Name   # source antes que sink por nombre? no: orden alfabetico j < m -> jdbc primero
# Asegurar orden: sources primero, sinks despues
$jsons = @( $jsons | Where-Object { $_.Name -match 'source' } ) + @( $jsons | Where-Object { $_.Name -notmatch 'source' } )

foreach ($f in $jsons) {
    $content = Get-Content -Raw $f.FullName
    foreach ($k in $vars.Keys) { $content = $content.Replace('{{' + $k + '}}', $vars[$k]) }
    $payload = $content | ConvertFrom-Json
    $name = $payload.name
    Write-Host "`n-> Registrando $name ($($f.Name))" -ForegroundColor Cyan

    $body = $content
    try {
        $r = Invoke-WebRequest -Uri "$connect/connectors" -Method Post -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 30
        Write-Host "   creado (HTTP $($r.StatusCode))" -ForegroundColor Green
    } catch {
        $resp = $_.Exception.Response
        if ($resp -and [int]$resp.StatusCode -eq 409) {
            $cfg = ($payload.config | ConvertTo-Json -Depth 10)
            $r = Invoke-WebRequest -Uri "$connect/connectors/$name/config" -Method Put -ContentType 'application/json' -Body $cfg -UseBasicParsing -TimeoutSec 30
            Write-Host "   ya existia -> config actualizada (HTTP $($r.StatusCode))" -ForegroundColor Yellow
        } else {
            Write-Host "   ERROR: $($_.Exception.Message)" -ForegroundColor Red
            if ($_.ErrorDetails) { Write-Host "   $($_.ErrorDetails.Message)" -ForegroundColor Red }
            throw
        }
    }
}

Write-Host "`n=== Esperando estado RUNNING ===" -ForegroundColor Cyan
Start-Sleep -Seconds 5
foreach ($f in $jsons) {
    $payload = (Get-Content -Raw $f.FullName) | ConvertFrom-Json
    $name = $payload.name
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        try {
            $st = Invoke-RestMethod -Uri "$connect/connectors/$name/status" -TimeoutSec 10
            $state = $st.connector.state
            $tasksBad = @($st.tasks | Where-Object { $_.state -eq 'FAILED' })
            if ($state -eq 'RUNNING' -and $tasksBad.Count -eq 0) {
                Write-Host ("  OK   {0}: RUNNING ({1} task(s))" -f $name, $st.tasks.Count) -ForegroundColor Green
                break
            }
            if ($state -eq 'FAILED' -or $tasksBad.Count -gt 0) {
                $trace = if ($tasksBad.Count) { $tasksBad[0].trace } else { $st.connector.trace }
                Write-Host ("  FAIL {0}: {1}" -f $name, $state) -ForegroundColor Red
                if ($trace) { Write-Host ("       {0}" -f $trace.Substring(0, [Math]::Min(500, $trace.Length))) -ForegroundColor DarkYellow }
                break
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
}
