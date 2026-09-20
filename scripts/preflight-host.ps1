# Pre-flight del host: valida todo lo necesario ANTES de provisionar/levantar.
# Uso: scripts/preflight-host.ps1
param([switch]$SkipDbAuth)
. (Join-Path $PSScriptRoot 'common.ps1')

$vars = Read-DotEnv
$failures = 0
function Report($ok, $name, $detail, $fix) {
    if ($ok) { Write-Host ("  OK   {0}  {1}" -f $name, $detail) -ForegroundColor Green }
    else {
        $script:failures++
        Write-Host ("  FAIL {0}  {1}" -f $name, $detail) -ForegroundColor Red
        if ($fix) { Write-Host ("       -> {0}" -f $fix) -ForegroundColor Yellow }
    }
}

Write-Host "`n=== 1. Servicios y puertos del host ==="
foreach ($svc in @('MySQL80', 'PostgreSQL_Data_D')) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    Report ($s -and $s.Status -eq 'Running') "Servicio $svc" $(if ($s) { $s.Status } else { 'no existe' }) "Start-Service $svc"
}
foreach ($p in @($vars['MYSQL_PORT'], $vars['PG_PORT'])) {
    $r = Test-NetConnection -ComputerName 127.0.0.1 -Port $p -WarningAction SilentlyContinue -InformationLevel Quiet
    Report $r "Puerto $p (localhost)" '' "El servicio debe estar corriendo."
}

Write-Host "`n=== 2. Docker ==="
$dockerOk = $true
try { docker info 2>&1 | Out-Null; if ($LASTEXITCODE -ne 0) { $dockerOk = $false } } catch { $dockerOk = $false }
Report $dockerOk "Docker daemon" '' "Arranca Docker Desktop y vuelve a ejecutar."

Write-Host "`n=== 3. Conectividad contenedor -> BDs del host ==="
if ($dockerOk) {
    docker pull busybox:1.37 2>$null | Out-Null
    $ErrorActionPreference = 'Continue'   # docker escribe progreso a stderr; con 'Stop' PS5.1 lo vuelve excepcion
    foreach ($t in @(@('MySQL', $vars['MYSQL_PORT']), @('PostgreSQL', $vars['PG_PORT']))) {
        docker run --rm --add-host "host.docker.internal:host-gateway" busybox:1.37 `
            nc -z -w 3 host.docker.internal $t[1] 2>$null | Out-Null
        $ok = ($LASTEXITCODE -eq 0)
        Report $ok "Contenedor -> host.docker.internal:$($t[1]) ($($t[0]))" `
            '' "Ejecuta como ADMIN: scripts\firewall-fix.ps1 (abre 3306/5433 a la subred WSL/Docker)."
    }
    $ErrorActionPreference = 'Stop'
} else { Write-Host "  (omitido: docker caido)" -ForegroundColor DarkGray }

Write-Host "`n=== 4. Configuracion CDC en las BDs ==="
if (-not $SkipDbAuth) {
    if (-not $vars['MYSQL_ROOT_PASSWORD'] -or -not $vars['PG_SUPERUSER_PASSWORD']) {
        Write-Host "  (omitido: completa MYSQL_ROOT_PASSWORD y PG_SUPERUSER_PASSWORD en .env)" -ForegroundColor DarkGray
    } else {
        # mysql/psql escriben warnings a stderr; con EAP=Stop PS5.1 los vuelve excepcion
        $ErrorActionPreference = 'Continue'
        try {
            $mysql = Find-MysqlExe
            $out = & $mysql -h 127.0.0.1 -P $vars['MYSQL_PORT'] -u root "-p$($vars['MYSQL_ROOT_PASSWORD'])" -N -B -e "SELECT CONCAT(@@log_bin,'|',@@binlog_format,'|',@@binlog_row_image, '|', @@server_id)" 2>$null
            if ($LASTEXITCODE -ne 0) { Report $false "MySQL (auth root)" "exit=$LASTEXITCODE" "Verifica MYSQL_ROOT_PASSWORD en .env" }
            else {
                $parts = ("$out").Trim().Split('|')
                Report ($parts[0] -eq '1') "MySQL log_bin" "log_bin=$($parts[0])" "log_bin=mysql-bin en [mysqld] de my.ini"
                Report ($parts[1] -eq 'ROW') "MySQL binlog_format" $parts[1] "binlog_format=ROW en my.ini"
                Report ($parts[2] -eq 'FULL') "MySQL binlog_row_image" $parts[2] "binlog_row_image=FULL en my.ini"
            }
        } catch { Report $false "MySQL (auth root)" $_.Exception.Message "Verifica MYSQL_ROOT_PASSWORD en .env" }

        try {
            $psql = Find-PsqlExe
            $env:PGPASSWORD = $vars['PG_SUPERUSER_PASSWORD']
            $wl = & $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_SUPERUSER'] -d postgres -t -A -c "SHOW wal_level" 2>$null
            Report ("$wl".Trim() -eq 'logical') "Postgres wal_level" "$wl".Trim() "wal_level=logical en postgresql.conf + reinicio del servicio"
            $slots = & $psql -h 127.0.0.1 -p $vars['PG_PORT'] -U $vars['PG_SUPERUSER'] -d postgres -t -A -c "SHOW max_replication_slots" 2>$null
            Report ([int]("$slots".Trim()) -ge 5) "Postgres max_replication_slots" "$slots".Trim() "max_replication_slots>=10"
        } catch { Report $false "Postgres (auth superuser)" $_.Exception.Message "Verifica PG_SUPERUSER / PG_SUPERUSER_PASSWORD en .env" }
        $ErrorActionPreference = 'Stop'
    }
}

Write-Host ""
if ($failures -eq 0) { Write-Host "PRE-FLIGHT: todo OK" -ForegroundColor Green }
else { Write-Host "PRE-FLIGHT: $failures fallo(s) — corrige antes de continuar" -ForegroundColor Red }
exit $(if ($failures -eq 0) { 0 } else { 1 })
