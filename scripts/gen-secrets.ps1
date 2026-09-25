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

# --- my.cnf para mysqld-exporter v0.20+ (DATA_SOURCE_NAME fue removido) ---
$mycnf = "[client]`nuser=$($vars['MYSQL_CDC_USER'])`npassword=$($vars['MYSQL_CDC_PASSWORD'])`n"
[System.IO.File]::WriteAllText((Join-Path $secretsDir 'mysqld-exporter.my.cnf'), $mycnf, (New-Object System.Text.UTF8Encoding($false)))

# --- SQL Server (OPCIONAL: solo si hay credenciales en .env) ---
if ($vars['SQLSERVER_CDC_PASSWORD']) {
    $props += "`nsqlserver_cdc_password=$($vars['SQLSERVER_CDC_PASSWORD'])"
    if (-not $vars['SQLSERVER_APP_USER']) { $vars['SQLSERVER_APP_USER'] = 'demo_app' }
    Render-Template -Src (Join-Path $root 'sql\templates\sqlserver-01-init.sql.tmpl') -Dst (Join-Path $root 'build\demodb-init\sqlserver\01-init.sql') -Vars $vars
    Write-Host "OK  SQL Server listo (perfil sqlserver de demodb)" -ForegroundColor Green
} else {
    Write-Host "OK  SQL Server omitido (sin credenciales en .env - opcional)" -ForegroundColor DarkGray
}
[System.IO.File]::WriteAllText((Join-Path $secretsDir 'default.properties'), $props, (New-Object System.Text.UTF8Encoding($false)))

# --- SQL para provisioning en host ---
Render-Template -Src (Join-Path $root 'sql\templates\mysql-cdc-user.sql.tmpl')       -Dst (Join-Path $root 'build\host-sql\mysql\01-cdc-user.sql')          -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\mysql-seed-inventory.sql.tmpl') -Dst (Join-Path $root 'build\host-sql\mysql\02-seed-inventory.sql')    -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\pg-cdc-role-db.sql.tmpl')       -Dst (Join-Path $root 'build\host-sql\postgres\01-cdc-role-db.sql')     -Vars $vars

# --- init SQL para el modo demodb (contenedores) ---
Render-Template -Src (Join-Path $root 'sql\templates\demodb-mysql-01-users.sql.tmpl') -Dst (Join-Path $root 'build\demodb-init\mysql\01-users.sql')   -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\mysql-seed-inventory.sql.tmpl')  -Dst (Join-Path $root 'build\demodb-init\mysql\02-seed.sql')    -Vars $vars
Render-Template -Src (Join-Path $root 'sql\templates\demodb-postgres-01-init.sql.tmpl') -Dst (Join-Path $root 'build\demodb-init\postgres\01-init.sql') -Vars $vars

# --- Alertmanager: config renderizada con/ sin email ---
# Si el Portal ya gestiona la config (portal-notif.json), NO pisar: fuente de verdad = UI
$notifMarker = Join-Path $root 'build\alertmanager\portal-notif.json'
if (Test-Path $notifMarker) {
    Write-Host "OK  Alertmanager: gestionado por el Portal (Notificaciones) - no se pisa desde .env" -ForegroundColor DarkGray
} else {
$emailEnabled = ($vars['ALERT_EMAIL_ENABLED'] -eq 'true')
$emailGlobal = ""
$emailRoutes = ""
$emailReceivers = ""
if ($emailEnabled) {
    foreach ($k in @('ALERT_SMTP_HOST', 'ALERT_SMTP_FROM', 'ALERT_SMTP_USER', 'ALERT_SMTP_PASSWORD', 'ALERT_EMAIL_TO')) {
        if (-not $vars[$k]) {
            Write-Host "AVISO: ALERT_EMAIL_ENABLED=true pero falta $k en .env -> email DESHABILITADO en este render" -ForegroundColor Yellow
            $emailEnabled = $false
            break
        }
    }
}
if ($emailEnabled) {
    $port = if ($vars['ALERT_SMTP_PORT']) { $vars['ALERT_SMTP_PORT'] } else { '587' }

    $emailRoutes = @'
    - matchers: [ 'severity = "critical"' ]
      receiver: critical-all
      group_wait: 0s
      repeat_interval: 4h
    - matchers: [ 'severity = "warning"' ]
      receiver: warning-all
      group_wait: 10m
      group_interval: 1h
      repeat_interval: 4h
'@

    # Plantilla LITERAL (single-quote here-string): los templates Go de Alertmanager
    # ({{ ... }}) quedan intactos; los valores se inyectan via tokens __TOKEN__.
    $emailTemplate = @'
global:
  resolve_timeout: 5m
  smtp_smarthost: '__SMTP_HOST__:__SMTP_PORT__'
  smtp_from: '__SMTP_FROM__'
  smtp_auth_username: '__SMTP_USER__'
  smtp_auth_password: '__SMTP_PASSWORD__'
  smtp_require_tls: true
'@
    $emailGlobal = $emailTemplate

    $receiverTemplate = @'
- name: critical-all
  email_configs:
    - to: '__ALERT_TO__'
      from: '__SMTP_FROM__'
      smarthost: '__SMTP_HOST__:__SMTP_PORT__'
      auth_username: '__SMTP_USER__'
      auth_password: '__SMTP_PASSWORD__'
      send_resolved: true
      headers:
        Subject: '[CDC CRITICAL] {{ .CommonLabels.alertname }} - {{ .CommonLabels.instance }}'
      text: |-
        ESTADO: {{ .Status | toUpper }}

        {{ range .Alerts }}
        Alerta:     {{ .Labels.alertname }}
        Severidad:  {{ .Labels.severity }}
        Resumen:    {{ .Annotations.summary }}
        Detalle:    {{ .Annotations.description }}
        Desde:      {{ .StartsAt }}
        {{ end }}

        Portal:     http://localhost:8085/alerts
  webhook_configs:
    - url: http://portal:8085/alerts/webhook
      send_resolved: true
- name: warning-all
  email_configs:
    - to: '__ALERT_TO__'
      from: '__SMTP_FROM__'
      smarthost: '__SMTP_HOST__:__SMTP_PORT__'
      auth_username: '__SMTP_USER__'
      auth_password: '__SMTP_PASSWORD__'
      send_resolved: true
      headers:
        Subject: '[CDC warning] {{ .CommonLabels.alertname }} - {{ .CommonLabels.instance }}'
      text: |-
        ESTADO: {{ .Status | toUpper }}

        {{ range .Alerts }}
        Alerta:     {{ .Labels.alertname }}
        Severidad:  {{ .Labels.severity }}
        Resumen:    {{ .Annotations.summary }}
        Detalle:    {{ .Annotations.description }}
        Desde:      {{ .StartsAt }}
        {{ end }}

        Portal:     http://localhost:8085/alerts
  webhook_configs:
    - url: http://portal:8085/alerts/webhook
      send_resolved: true
'@
    $subs = @{
        '__SMTP_HOST__'     = $vars['ALERT_SMTP_HOST']
        '__SMTP_PORT__'     = $port
        '__SMTP_FROM__'     = $vars['ALERT_SMTP_FROM']
        '__SMTP_USER__'     = $vars['ALERT_SMTP_USER']
        '__SMTP_PASSWORD__' = $vars['ALERT_SMTP_PASSWORD']
        '__ALERT_TO__'      = $vars['ALERT_EMAIL_TO']
    }
    $emailReceivers = $receiverTemplate
    foreach ($k in $subs.Keys) { $emailReceivers = $emailReceivers.Replace($k, $subs[$k]) }
}

$content = (Get-Content -Raw (Join-Path $root 'config\alertmanager\alertmanager.yml.tmpl'))
$content = $content.Replace('{{EMAIL_GLOBAL_BLOCK}}', $emailGlobal).Replace('{{EMAIL_ROUTES_BLOCK}}', $emailRoutes).Replace('{{EMAIL_RECEIVERS_BLOCK}}', $emailReceivers)
$amDir = Join-Path $root 'build\alertmanager'
if (-not (Test-Path $amDir)) { New-Item -ItemType Directory -Path $amDir | Out-Null }
$amOut = Join-Path $amDir 'alertmanager.yml'
if (Test-Path $amOut) { Remove-Item $amOut -Recurse -Force }
[System.IO.File]::WriteAllText($amOut, $content, (New-Object System.Text.UTF8Encoding($false)))
if ($emailEnabled) { Write-Host "OK  Alertmanager: email habilitado ($($vars['ALERT_SMTP_HOST']))" -ForegroundColor Green }
else { Write-Host "OK  Alertmanager: solo webhook Portal (email deshabilitado)" -ForegroundColor DarkGray }
}

# --- Lakehouse: configs renderizadas (origen parametrizable mysql|postgres) ---
if ($vars['LH_SOURCE_ENGINE'] -and (Test-Path (Join-Path $root 'docker-compose.lakehouse.yml'))) {
    if (-not $vars['LH_SOURCE_DB']) { $vars['LH_SOURCE_DB'] = $vars['CDC_SOURCE_DB'] }
    if (-not $vars['LH_CATALOG_DB']) { $vars['LH_CATALOG_DB'] = 'iceberg_catalog' }
    $lhm = ""
    $lhp = ""
    if ($vars['LH_SOURCE_ENGINE'] -eq 'postgres') {
        $lhp = @"
debezium.source.connector.class=io.debezium.connector.postgresql.PostgresConnector
debezium.source.database.hostname=$($vars['PG_HOST'])
debezium.source.database.port=$($vars['PG_PORT'])
debezium.source.database.user=$($vars['PG_CDC_ROLE'])
debezium.source.database.password=$($vars['PG_CDC_PASSWORD'])
debezium.source.database.dbname=$($vars['LH_SOURCE_DB'])
debezium.source.plugin.name=pgoutput
debezium.source.slot.name=debezium_lh
debezium.source.publication.name=cdc_pub_lh
debezium.source.publication.autocreate.mode=filtered
debezium.source.topic.prefix=cdclh
debezium.source.heartbeat.interval.ms=10000
"@
    } else {
        $lhm = @"
debezium.source.connector.class=io.debezium.connector.mysql.MySqlConnector
debezium.source.database.hostname=$($vars['MYSQL_HOST'])
debezium.source.database.port=$($vars['MYSQL_PORT'])
debezium.source.database.user=$($vars['MYSQL_CDC_USER'])
debezium.source.database.password=$($vars['MYSQL_CDC_PASSWORD'])
debezium.source.database.include.list=$($vars['LH_SOURCE_DB'])
debezium.source.database.server.id=7401
debezium.source.include.schema.changes=false
debezium.source.topic.prefix=cdclh
debezium.source.heartbeat.interval.ms=10000
"@
    }
    Render-Template -Src (Join-Path $root 'config\lakehouse\debezium\application.properties.tmpl') -Dst (Join-Path $root 'build\lakehouse\debezium\application.properties') -Vars $vars
    # inyectar bloque de origen con valores reales
    $appPath = Join-Path $root 'build\lakehouse\debezium\application.properties'
    $app = Get-Content -Raw $appPath
    $app = $app.Replace('{{MYSQL_SOURCE_BLOCK}}', $lhm).Replace('{{PG_SOURCE_BLOCK}}', $lhp)
    [System.IO.File]::WriteAllText($appPath, $app, (New-Object System.Text.UTF8Encoding($false)))

    Render-Template -Src (Join-Path $root 'config\lakehouse\trino\etc\catalog\iceberg.properties.tmpl') -Dst (Join-Path $root 'build\lakehouse\trino\etc\catalog\iceberg.properties') -Vars $vars
    # copiar etc estatico de Trino (config/jvm/node) junto al catalogo renderizado
    $trinoEtcDst = Join-Path $root 'build\lakehouse\trino\etc'
    New-Item -ItemType Directory -Force -Path (Join-Path $trinoEtcDst 'catalog') | Out-Null
    foreach ($f in @('config.properties', 'jvm.config', 'node.properties')) {
        Copy-Item (Join-Path $root "config\lakehouse\trino\etc\$f") $trinoEtcDst -Force
    }
    Render-Template -Src (Join-Path $root 'sql\templates\pg-iceberg-catalog.sql.tmpl') -Dst (Join-Path $root 'build\host-sql\postgres\03-iceberg-catalog.sql') -Vars $vars
    Write-Host "OK  Lakehouse: config renderizada (origen = $($vars['LH_SOURCE_ENGINE']))" -ForegroundColor Green
} else {
    Write-Host "OK  Lakehouse: omitido (LH_SOURCE_ENGINE sin definir)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "OK  secrets\default.properties generado" -ForegroundColor Green
Write-Host "OK  SQL renderizado en build\host-sql\ y build\demodb-init\" -ForegroundColor Green
Write-Host ""
