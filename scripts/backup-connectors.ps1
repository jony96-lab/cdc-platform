# Backup de las configuraciones de TODOS los conectores registrados en Kafka Connect.
# Uso: scripts/backup-connectors.ps1
param([string]$OutDir)
. (Join-Path $PSScriptRoot 'common.ps1')
$vars = Read-DotEnv
if (-not $OutDir) { $OutDir = Join-Path $script:RepoRoot ("backups\connectors-" + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$connect = "http://localhost:$($vars['CONNECT_PORT'])"
$names = Invoke-RestMethod -Uri "$connect/connectors" -TimeoutSec 15
if (-not $names -or $names.Count -eq 0) { Write-Host "No hay conectores para respaldar."; exit 0 }

$all = [ordered]@{}
foreach ($n in $names) {
    $info = Invoke-RestMethod -Uri "$connect/connectors/$n" -TimeoutSec 15
    $path = Join-Path $OutDir "$n.json"
    $info | ConvertTo-Json -Depth 20 | Set-Content -Path $path -Encoding UTF8
    $all[$n] = $info.config
    Write-Host "  OK $n -> $path" -ForegroundColor Green
}
$manifest = Join-Path $OutDir 'manifest.json'
@{ backup_at = (Get-Date -Format 'o'); connectors = $names } | ConvertTo-Json | Set-Content $manifest -Encoding UTF8
Write-Host "`nBackup completo en $OutDir" -ForegroundColor Green
Write-Host "Restore: register-connectors.ps1 usa connectors/*.json; para restaurar un backup puntual:" -ForegroundColor DarkGray
Write-Host '  Invoke-RestMethod -Method Put -Uri "$connect/connectors/<nombre>/config" -ContentType application/json -Body (Get-Content <backup>.json | ConvertFrom-Json).config' -ForegroundColor DarkGray
