# Abre el firewall de Windows para que los contenedores (subred WSL/Docker)
# alcancen MySQL y PostgreSQL del host. REQUIERE EJECUTAR COMO ADMINISTRADOR.
# Uso (en PowerShell elevado): scripts/firewall-fix.ps1
param()
. (Join-Path $PSScriptRoot 'common.ps1')

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Ejecuta este script en un PowerShell ELEVADO (Run as Administrator)." }

$vars = Read-DotEnv
$subnets = @('172.16.0.0/12', '192.168.0.0/16')

foreach ($t in @(@{ N = 'CDC-MySQL'; P = $vars['MYSQL_PORT'] }, @{ N = 'CDC-PostgreSQL'; P = $vars['PG_PORT'] })) {
    $name = "$($t.N)-desde-Docker"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  OK   regla '$name' ya existe" -ForegroundColor Green
    } else {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $t.P -RemoteAddress $subnets -Profile Any | Out-Null
        Write-Host "  +    regla '$name' creada (TCP $($t.P) desde $($subnets -join ', '))" -ForegroundColor Yellow
    }
}
Write-Host "`nListo. Re-verifica con: scripts\preflight-host.ps1" -ForegroundColor Green
