# ==============================================================
# Instalador de la Plataforma CDC
#   .\install.ps1              -> pregunta modo y hace todo
#   .\install.ps1 -Mode demo   -> 100% containerizado (sin BDs del host)
#   .\install.ps1 -Mode host   -> usa tus MySQL/PostgreSQL instalados
#   .\install.ps1 -SkipTests   -> omite la suite E2E
# ==============================================================
param(
    [ValidateSet('auto', 'demo', 'host')][string]$Mode = 'auto',
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host ""
Write-Host "  Plataforma CDC  -  MySQL/PostgreSQL/SQL Server -> Kafka (Debezium) -> Destino" -ForegroundColor Cyan
Write-Host "  ================================================================================" -ForegroundColor Cyan

# ---------- 0. Prerrequisitos ----------
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [X] Docker no esta corriendo. Arranca Docker Desktop e intenta de nuevo." -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Docker" -ForegroundColor Green

# ---------- 1. .env ----------
if (-not (Test-Path "$RepoRoot\.env")) {
    Copy-Item "$RepoRoot\.env.example" "$RepoRoot\.env"
    # Credenciales CDC generadas
    function New-RandomPassword { param($n = 24) $c = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'; $b = New-Object byte[] $n; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $c[$_ % $c.Length] }) }
    $envRaw = Get-Content "$RepoRoot\.env" -Raw
    $envRaw = $envRaw -replace '(?m)^MYSQL_CDC_PASSWORD=\s*$', ("MYSQL_CDC_PASSWORD=" + (New-RandomPassword))
    $envRaw = $envRaw -replace '(?m)^MYSQL_APP_PASSWORD=\s*$', ("MYSQL_APP_PASSWORD=" + (New-RandomPassword))
    $envRaw = $envRaw -replace '(?m)^PG_CDC_PASSWORD=\s*$', ("PG_CDC_PASSWORD=" + (New-RandomPassword))
    $envRaw = $envRaw -replace '(?m)^DEMO_DB_ROOT_PASSWORD=\s*$', ("DEMO_DB_ROOT_PASSWORD=" + (New-RandomPassword))
    $envRaw = $envRaw -replace '(?m)^SQLSERVER_CDC_PASSWORD=\s*$', ("SQLSERVER_CDC_PASSWORD=" + (New-RandomPassword))
    $envRaw = $envRaw -replace '(?m)^SQLSERVER_SA_PASSWORD=\s*$', ("SQLSERVER_SA_PASSWORD=" + (New-RandomPassword 18) + "9!aA")
    $envRaw = $envRaw -replace '(?m)^GRAFANA_ADMIN_PASSWORD=\s*$', ("GRAFANA_ADMIN_PASSWORD=" + (New-RandomPassword 20))
    $clusterId = (& docker run --rm ${KAFKA_IMAGE:-apache/kafka:4.3.1} /opt/kafka/bin/kafka-storage.sh random-uuid) 2>$null
    if ($clusterId) { $envRaw = $envRaw -replace '(?m)^KAFKA_CLUSTER_ID=\s*$', ("KAFKA_CLUSTER_ID=" + "$clusterId".Trim()) }
    [System.IO.File]::WriteAllText("$RepoRoot\.env", $envRaw, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  [OK] .env creado con credenciales CDC generadas" -ForegroundColor Green
}
$vars = @{}
Get-Content "$RepoRoot\.env" | ForEach-Object { if ($_ -match '^([A-Z_0-9]+)=(.*)$') { $vars[$matches[1].Trim()] = $matches[2].Trim() } }

# ---------- 2. Modo ----------
if ($Mode -eq 'auto') {
    Write-Host ""
    Write-Host "  Como quieres correr las bases de datos?" -ForegroundColor Yellow
    Write-Host "   1) demo  - 100% containerizado, no toca nada del host (recomendado para probar)"
    Write-Host "   2) host  - usa tus MySQL/PostgreSQL ya instalados (requiere credenciales admin en .env)"
    $ans = Read-Host "  Elegi 1 o 2"
    $Mode = if ($ans -eq '2') { 'host' } else { 'demo' }
}

if ($Mode -eq 'host') {
    if (-not $vars['MYSQL_ROOT_PASSWORD'] -or -not $vars['PG_SUPERUSER_PASSWORD']) {
        Write-Host ""
        Write-Host "  [X] Modo host: completa en .env las contrasenas de administrador:" -ForegroundColor Red
        Write-Host "      MYSQL_ROOT_PASSWORD=  y  PG_SUPERUSER_PASSWORD=" -ForegroundColor Red
        Write-Host "      (y despues vuelve a ejecutar .\install.ps1 -Mode host)" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [OK] Modo: HOST (tus BDs instaladas)" -ForegroundColor Green
} else {
    Write-Host "  [OK] Modo: DEMO (todo containerizado)" -ForegroundColor Green
}

# ---------- 3. Preflight (host) ----------
if ($Mode -eq 'host') {
    Write-Host ""
    & "$RepoRoot\scripts\preflight-host.ps1"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [X] Preflight fallo. Corrige y reintenta (detalles arriba)." -ForegroundColor Red
        exit 1
    }
    Write-Host ""
    & "$RepoRoot\scripts\provision-host-dbs.ps1"
    if ($LASTEXITCODE -ne 0) { Write-Host "  [X] Fallo el provisioning." -ForegroundColor Red; exit 1 }
}

# ---------- 4. Stack ----------
Write-Host ""
if ($Mode -eq 'host') { & "$RepoRoot\scripts\up.ps1" }
else { & "$RepoRoot\scripts\up.ps1" -WithDemoDb; & "$RepoRoot\scripts\register-connectors.ps1" -Demo }

# ---------- 5. E2E ----------
if (-not $SkipTests) {
    Write-Host ""
    Write-Host "  Ejecutando verificacion E2E (17 tests)..." -ForegroundColor Cyan
    & "$RepoRoot\scripts\e2e.ps1"
}

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Green
Write-Host "   Instalacion completa. Abri el Portal: http://localhost:8085" -ForegroundColor Green
Write-Host "  ================================================================" -ForegroundColor Green
