# Provisiona las BDs del HOST: usuarios CDC, schema demo, rol+DB destino en Postgres, pg_hba.
# Uso: scripts/provision-host-dbs.ps1
param()
. (Join-Path $PSScriptRoot 'common.ps1')
# mysql.exe/psql.exe emiten warnings por stderr; con EAP=Stop PS5.1 los convierte en excepcion.
# Este script valida cada paso con $LASTEXITCODE explicitamente.
$ErrorActionPreference = 'Continue'

function Report-Ok($ok, $name, $detail) {
    if ($ok) { Write-Host ("  OK   {0}  {1}" -f $name, $detail) -ForegroundColor Green }
    else { Write-Host ("  FAIL {0}  {1}" -f $name, $detail) -ForegroundColor Red; throw "Verificacion fallo: $name" }
}

$vars = Read-DotEnv
if (-not $vars['MYSQL_ROOT_PASSWORD']) { throw "Completa MYSQL_ROOT_PASSWORD en .env" }
if (-not $vars['PG_SUPERUSER_PASSWORD']) { throw "Completa PG_SUPERUSER_PASSWORD en .env" }

# Asegurar SQL renderizado
$sqlUser = Join-Path $script:RepoRoot 'build\host-sql\mysql\01-cdc-user.sql'
if (-not (Test-Path $sqlUser)) { & (Join-Path $PSScriptRoot 'gen-secrets.ps1') }

$mysql = Find-MysqlExe
$psql = Find-PsqlExe
$env:PGCLIENTENCODING = 'UTF8'

function Invoke-MysqlFile([string]$Path) {
    # 'source' hace que mysql.exe lea el archivo directo (sin pipes de PowerShell que rompan UTF-8)
    $unixPath = $Path.Replace('\', '/')
    & $mysql --default-character-set=utf8mb4 -h 127.0.0.1 -P $vars['MYSQL_PORT'] -u root "-p$($vars['MYSQL_ROOT_PASSWORD'])" -e "source $unixPath"
    if ($LASTEXITCODE -ne 0) { throw "Fallo la ejecucion de $Path" }
}

Write-Host "`n=== MySQL: usuarios CDC + seed inventory ===" -ForegroundColor Cyan
Invoke-MysqlFile (Join-Path $script:RepoRoot 'build\host-sql\mysql\01-cdc-user.sql')
Write-Host "  OK usuarios $($vars['MYSQL_CDC_USER']) / $($vars['MYSQL_APP_USER'])" -ForegroundColor Green

Invoke-MysqlFile (Join-Path $script:RepoRoot 'build\host-sql\mysql\02-seed-inventory.sql')
Write-Host "  OK schema '$($vars['CDC_SOURCE_DB'])' sembrado" -ForegroundColor Green

Write-Host "`n=== PostgreSQL: rol CDC + base destino ===" -ForegroundColor Cyan
$env:PGPASSWORD = $vars['PG_SUPERUSER_PASSWORD']
$sqlPg = Join-Path $script:RepoRoot 'build\host-sql\postgres\01-cdc-role-db.sql'
& $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_SUPERUSER'] -d postgres -v ON_ERROR_STOP=1 -f $sqlPg
if ($LASTEXITCODE -ne 0) { throw "Fallo el provisioning de PostgreSQL" }
Write-Host "  OK rol $($vars['PG_CDC_ROLE']) + base $($vars['PG_TARGET_DB'])" -ForegroundColor Green

Write-Host "`n=== PostgreSQL: pg_hba.conf ===" -ForegroundColor Cyan
$hbaFile = (& $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_SUPERUSER'] -d postgres -t -A -c "SHOW hba_file") | Out-String
$hbaFile = $hbaFile.Trim()
if (-not (Test-Path $hbaFile)) { throw "No puedo leer $hbaFile (permisos?)" }

$role = $vars['PG_CDC_ROLE']
$rules = @(
    @{ Type = 'all';         Net = '172.16.0.0/12';  Line = "host    all             $role    172.16.0.0/12           scram-sha-256" },
    @{ Type = 'all';         Net = '192.168.0.0/16'; Line = "host    all             $role    192.168.0.0/16          scram-sha-256" },
    @{ Type = 'replication'; Net = '172.16.0.0/12';  Line = "host    replication     $role    172.16.0.0/12           scram-sha-256" },
    @{ Type = 'replication'; Net = '192.168.0.0/16'; Line = "host    replication     $role    192.168.0.0/16          scram-sha-256" }
)
$existing = Get-Content $hbaFile | Where-Object { $_ -notmatch '^\s*#' -and $_ -match "\s$role(\s|$)" }
$toAdd = @()
foreach ($r in $rules) {
    $pattern = "^host\s+$($r.Type)\s+$role\s+$([regex]::Escape($r.Net))"
    if (-not ($existing | Where-Object { $_ -match $pattern })) { $toAdd += $r.Line }
}
if ($toAdd.Count -gt 0) {
    Add-Content -Path $hbaFile -Value ""
    Add-Content -Path $hbaFile -Value "# CDC Platform: $role desde subred Docker/WSL (provision-host-dbs.ps1)"
    Add-Content -Path $hbaFile -Value $toAdd
    Write-Host "  + $($toAdd.Count) regla(s) agregada(s) a $hbaFile" -ForegroundColor Yellow
    & $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_SUPERUSER'] -d postgres -c "SELECT pg_reload_conf()" | Out-Null
    Write-Host "  OK pg_reload_conf() ejecutado (sin reinicio del servicio)" -ForegroundColor Green
} else {
    Write-Host "  OK pg_hba.conf ya contiene las reglas para $role" -ForegroundColor Green
}

Write-Host "`n=== Verificacion con las credenciales CDC ===" -ForegroundColor Cyan
$check = & $mysql -h 127.0.0.1 -P $vars['MYSQL_PORT'] -u $vars['MYSQL_CDC_USER'] "-p$($vars['MYSQL_CDC_PASSWORD'])" -N -B -e "SELECT CONCAT('binlog=',@@binlog_format)" 2>$null
Report-Ok ($LASTEXITCODE -eq 0) "Login MySQL como $($vars['MYSQL_CDC_USER'])" "$check"

$env:PGPASSWORD = $vars['PG_CDC_PASSWORD']
$check2 = & $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_CDC_ROLE'] -d $vars['PG_TARGET_DB'] -t -A -c "SELECT 'target-ok:' || current_database()" 2>&1
Report-Ok ($LASTEXITCODE -eq 0) "Login Postgres como $($vars['PG_CDC_ROLE']) -> $($vars['PG_TARGET_DB'])" ($check2 | Out-String).Trim()

Write-Host "`nPROVISIONING COMPLETO" -ForegroundColor Green
Write-Host "Siguiente paso: scripts\up.ps1" -ForegroundColor Cyan
