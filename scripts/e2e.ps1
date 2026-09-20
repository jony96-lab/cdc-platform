# Corre la suite E2E (pytest) en un contenedor efimero contra el stack levantado.
# Uso: scripts/e2e.ps1
param([string]$Filter = '')
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $script:RepoRoot

$ordered = @(
    '/tests/test_e2e_snapshot.py',
    '/tests/test_e2e_dml.py',
    '/tests/test_e2e_ddl.py',
    '/tests/test_portal_api.py'
)
$pytestArgs = if ($Filter) { @('pytest', '-v', '--tb=short', '-p', 'no:cacheprovider', '-k', $Filter, '/tests') }
              else { @('pytest', '-v', '--tb=short', '-p', 'no:cacheprovider') + $ordered }

& docker compose --profile tools -f docker-compose.yml -f docker-compose.monitoring.yml run --rm tester @pytestArgs
exit $LASTEXITCODE
