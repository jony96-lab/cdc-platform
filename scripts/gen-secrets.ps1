# Renderiza templates SQL y genera secrets/default.properties
# Uso: scripts/gen-secrets.ps1
param()
. (Join-Path $PSScriptRoot 'common.ps1')

$vars = Read-DotEnv

$required = @('MYSQL_CDC_PASSWORD', 'PG_CDC_PASSWORD', 'MYSQL_APP_PASSWORD', 'DEMO_DB_ROOT_PASSWORD')
foreach ($k in $required) {
    if (-not $vars[$k]) { throw "$k esta vacio en .env (deberia haberse generado; revisa .env)" }
}
foreach ($k in @('MYSQL_CDC_USER', 'PG_CDC_ROLE', 'MYSQL_APP_USER', 'CDC_SOURCE_DB', 'PG_TARGET_DB')) {
    if (-not $vars[$k]) { throw "$k falta en .env" }
}

$root = $script:RepoRoot

# --- secrets para FileConfigProvider de Kafka Connect ---
$secretsDir = Join-Path $root 'secrets'
if (-not (Test-Path $secretsDir)) { New-Item -ItemType Directory -Path $secretsDir | Out-Null }
New-Item -ItemType File -Force -Path (Join-Path $secretsDir '.gitkeep') | Out-Null
$props = @(
    "mysql_cdc_password=$($vars['MYSQL_CDC_PASSWORD'])",
    "pg_cdc_password=$($vars['PG_CDC_PASSWORD'])",
    "mysql_app_password=$($vars['MYSQL_APP_PASSWORD'])"
) -join "`n"
[System.IO.File]::WriteAllText((Join-Path $secretsDir 'default.properties'), $props, (New-Object System.Text.UTF8Encoding($false)))

# --- my.cnf para mysqld-exporter v0.20+ (DATA_SOURCE_NAME fue removido) ---
$mycnf = "[client]`nuser=$($vars['MYSQL_CDC_USER'])`npassword=$($vars['MYSQL_CDC_PASSWORD'])`n"
[System.IO.File]::WriteAllText((Join-Path $secretsDir 'mysqld-exporter.my.cnf'), $mycnf, (New-Object System.Text.UTF8Encoding($false)))

# --- SQL Server (OPCIONAL: solo si hay credenciales en .env) ---
if ($vars['SQLSERVER_CDC_PASSWORD']) {
    $props += "`nsqlserver_cdc_password=$($vars['SQLSERVER_CDC_PASSWORD'])"
    [System.IO.File]::WriteAllText((Join-Path $secretsDir 'default.properties'), $props, (New-Object System.Text.UTF8Encoding($false)))
    if (-not $vars['SQLSERVER_APP_USER']) { $vars['SQLSERVER_APP_USER'] = 'demo_app' }
    Render-Template -Src (Join-Path $root 'sql\templates\sqlserver-01-init.sql.tmpl') -Dst (Join-Path $root 'build\demodb-init\sqlserver\01-init.sql') -Vars $vars
    Write-Host "OK  SQL Server listo (perfil sqlserver de demodb)" -ForegroundColor Green
} else {
    Write-Host "OK  SQL Server omitido (sin credenciales en .env - opcional)" -ForegroundColor DarkGray
}

# --- SQL para provisioning en host ---
Render-Template -Src (Join-Path $root 'sql\templates\mysql-cdc-user.sql.tmpl')       -Dst (Join-Path $root 'build\host-sql\mysql\01-cdc-user.sql')          -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\mysql-seed-inventory.sql.tmpl') -Dst (Join-Path $root 'build\host-sql\mysql\02-seed-inventory.sql')    -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\pg-cdc-role-db.sql.tmpl')       -Dst (Join-Path $root 'build\host-sql\postgres\01-cdc-role-db.sql')     -Vars $vars

# --- init SQL para el modo demodb (contenedores) ---
Render-Template -Src (Join-Path $root 'sql\templates\demodb-mysql-01-users.sql.tmpl') -Dst (Join-Path $root 'build\demodb-init\mysql\01-users.sql')   -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\mysql-seed-inventory.sql.tmpl')  -Dst (Join-Path $root 'build\demodb-init\mysql\02-seed.sql')    -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\demodb-postgres-01-init.sql.tmpl') -Dst (Join-Path $root 'build\demodb-init\postgres\01-init.sql') -Vars $vars

Write-Host ""
Write-Host "OK  secrets\default.properties generado" -ForegroundColor Green
Write-Host "OK  SQL renderizado en build\host-sql\ y build\demodb-init\" -ForegroundColor Green
Write-Host ""
