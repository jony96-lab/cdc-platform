# Funciones compartidas por los scripts de la plataforma CDC
$ErrorActionPreference = 'Stop'
$script:RepoRoot = Split-Path -Parent $PSScriptRoot

function Read-DotEnv {
    param([string]$Path)
    if (-not $Path) { $Path = Join-Path $script:RepoRoot '.env' }
    if (-not (Test-Path $Path)) { throw "No existe $Path. Copia .env.example a .env y completalo." }
    $vars = @{}
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line -split '=', 2
            $vars[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $vars
}

function Get-ComposeFileArgs {
    param([switch]$WithDemoDb)
    $files = @('-f', 'docker-compose.yml', '-f', 'docker-compose.monitoring.yml')
    if ($WithDemoDb) { $files += @('-f', 'docker-compose.demodb.yml') }
    return ,$files
}

function Render-Template {
    param([string]$Src, [string]$Dst, [hashtable]$Vars)
    $content = Get-Content -Raw -Path $Src
    foreach ($k in $Vars.Keys) { $content = $content.Replace('{{' + $k + '}}', $Vars[$k]) }
    $dir = Split-Path $Dst -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($Dst, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function Wait-Http {
    param([string]$Url, [int]$TimeoutSec = 120, [string]$Label = $Url)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "Timeout esperando $Label en $Url ($TimeoutSec s)"
}

function Wait-ContainerHealthy {
    param([string]$Name, [int]$TimeoutSec = 240)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $state = docker inspect --format '{{.State.Health.Status}}' $Name 2>$null
        if ($state -eq 'healthy') { return $true }
        $running = docker inspect --format '{{.State.Running}}' $Name 2>$null
        if ($running -eq 'false') { throw "Contenedor $Name no esta corriendo (revisa: docker logs $Name)" }
        Start-Sleep -Seconds 3
    }
    throw "Timeout esperando health de $Name ($TimeoutSec s)"
}

function Find-MysqlExe {
    $candidates = @(
        'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe',
        'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe'
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command mysql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "No encuentro mysql.exe. Instala MySQL client o agrega mysql al PATH."
}

function Find-PsqlExe {
    $candidates = @('D:\postgres\bin\psql.exe', 'C:\Program Files\PostgreSQL\18\bin\psql.exe',
                    'C:\Program Files\PostgreSQL\17\bin\psql.exe')
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "No encuentro psql.exe."
}
