# Genera mutaciones de ejemplo en el MySQL fuente para ver el CDC en accion.
# Uso: scripts/mutate.ps1 [-Action all|insert|update|delete] [-Times 1]
param([ValidateSet('all', 'insert', 'update', 'delete', 'batch')][string]$Action = 'all', [int]$Times = 1)
. (Join-Path $PSScriptRoot 'common.ps1')
$ErrorActionPreference = 'Continue'   # mysql.exe imprime warnings por stderr
$vars = Read-DotEnv
$mysql = Find-MysqlExe
$db = $vars['CDC_SOURCE_DB']

function Run-Sql([string]$sql) {
    $out = & $mysql -h 127.0.0.1 -P $vars['MYSQL_PORT'] -u $vars['MYSQL_APP_USER'] "-p$($vars['MYSQL_APP_PASSWORD'])" $db -N -B -e $sql 2>$null
    if ($LASTEXITCODE -ne 0) { throw "SQL fallo: $sql" }
    return $out
}

for ($i = 1; $i -le $Times; $i++) {
    $stamp = Get-Date -Format 'HHmmss'
    $rand = Get-Random -Minimum 1000 -Maximum 999999

    if ($Action -in @('all', 'insert')) {
        Run-Sql "INSERT INTO customers (first_name,last_name,email,phone,city) VALUES ('Demo$rand','Usuario','demo.$rand.$stamp@example.com','+54 11 0000 $rand','CABA')" | Out-Null
        $cid = Run-Sql "SELECT id FROM customers WHERE email='demo.$rand.$stamp@example.com'"
        Run-Sql "INSERT INTO orders (customer_id,product_id,quantity,unit_price,status,notes) VALUES ($cid, (SELECT id FROM products ORDER BY RAND() LIMIT 1), $(Get-Random -Minimum 1 -Maximum 4), 49.50, 'pending', 'mutate.ps1 $stamp')" | Out-Null
        Write-Host "  INSERT customer id=$cid + order" -ForegroundColor Green
    }
    if ($Action -in @('all', 'update')) {
        Run-Sql "UPDATE products SET stock = stock + $(Get-Random -Minimum -5 -Maximum 20), price = ROUND(price * (1 + (RAND()-0.5)/50), 2) WHERE id = $(Get-Random -Minimum 1 -Maximum 11)" | Out-Null
        Run-Sql "UPDATE orders SET status = ELT(FLOOR(1 + RAND()*5),'pending','paid','shipped','delivered','cancelled'), notes = CONCAT(COALESCE(notes,''),' [upd $stamp]') WHERE id = (SELECT max_id FROM (SELECT MAX(id) max_id FROM orders) t)" | Out-Null
        Write-Host "  UPDATE producto (stock/precio) + ultimo pedido (status)" -ForegroundColor Yellow
    }
    if ($Action -in @('all', 'delete')) {
        $oid = Run-Sql "SELECT MAX(id) FROM orders"
        if ($oid -and $oid -ne 'NULL') {
            Run-Sql "DELETE FROM orders WHERE id = $oid" | Out-Null
            Write-Host "  DELETE order id=$oid" -ForegroundColor Red
        }
    }
    if ($Action -eq 'batch') {
        $sqls = @()
        1..50 | ForEach-Object {
            $r = Get-Random -Minimum 100000 -Maximum 999999
            $sqls += "INSERT INTO orders (customer_id,product_id,quantity,unit_price,status) VALUES ($(Get-Random -Minimum 1 -Maximum 16), $(Get-Random -Minimum 1 -Maximum 11), $(Get-Random -Minimum 1 -Maximum 5), $(Get-Random -Minimum 10 -Maximum 400).99, 'pending')"
        }
        Run-Sql ($sqls -join '; ') | Out-Null
        Write-Host "  BATCH 50 orders insertadas" -ForegroundColor Green
    }
}
Write-Host "`nMira la propagacion en el Portal (http://localhost:$($vars['PORTAL_PORT'])) o en Grafana → CDC Overview." -ForegroundColor Cyan
