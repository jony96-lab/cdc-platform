# Consola SQL de Trino sobre el lakehouse.
# Uso:
#   scripts\lakehouse-sql.ps1                              # shell interactivo
#   scripts\lakehouse-sql.ps1 "SELECT count(*) FROM iceberg.cdc.lh_inventory_customers"
param([Parameter(Position = 0)][string]$Query)
. (Join-Path $PSScriptRoot 'common.ps1')
$ErrorActionPreference = 'Continue'
Set-Location $script:RepoRoot

$vars = Read-DotEnv
$catalog = 'iceberg'
$schema = 'cdc'

if ($Query) {
    docker exec -i cdc-trino trino --output-format TABLE --catalog $catalog --schema $schema --execute $Query
} else {
    docker exec -it cdc-trino trino --catalog $catalog --schema $schema
}
