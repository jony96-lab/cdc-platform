"""Ejemplo de consumo del lakehouse desde Python.

Requiere: pip install httpx  (y pandas si queres DataFrame)
Corre en cualquier maquina que alcance Trino: http://localhost:8086

Ejecutar dentro del stack (prueba):
  docker compose -f docker-compose.yml --profile tools run --rm tester \\
      python /examples/consumir_lakehouse.py
"""
import httpx

TRINO = "http://trino:8080"
HEADERS = {"X-Trino-User": "analista"}


def query(sql: str) -> tuple[list[str], list[list]]:
    """Ejecuta SQL en Trino y devuelve (columnas, filas). Sigue nextUri (API async)."""
    r = httpx.post(f"{TRINO}/v1/statement", content=sql, timeout=60, headers=HEADERS)
    r.raise_for_status()
    cols, rows = [], []
    while True:
        data = r.json()
        if data.get("error"):
            raise RuntimeError(f"Trino: {data['error'].get('message')}")
        if data.get("columns") and not cols:
            cols = [c["name"] for c in data["columns"]]
        rows.extend(data.get("data", []))
        nxt = data.get("nextUri")
        if not nxt:
            return cols, rows
        r = httpx.get(nxt, timeout=60, headers=HEADERS)


def main() -> None:
    # 1. Estado actual del lakehouse
    cols, rows = query("SELECT * FROM iceberg.cdc.lh_cdclh_public_ventas ORDER BY id")
    print(f"== ventas ({len(rows)} filas) columnas: {cols}")
    for r in rows:
        print("  ", r)

    # 2. Analitica: lo que el formato columnar hace rapido
    cols, rows = query(
        "SELECT __op, count(*) AS eventos FROM iceberg.cdc.lh_cdclh_public_ventas "
        "GROUP BY __op ORDER BY eventos DESC")
    print("\n== eventos por tipo de operacion (auditoria CDC) ==")
    for r in rows:
        print(f"  {r[0]}: {r[1]}")

    # 3. En pandas (si esta instalado):
    #    import pandas as pd
    #    cols, rows = query("SELECT * FROM iceberg.cdc.lh_cdclh_public_ventas")
    #    df = pd.DataFrame(rows, columns=cols)
    #    print(df.describe())


if __name__ == "__main__":
    main()
