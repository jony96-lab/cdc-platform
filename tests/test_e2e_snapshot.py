"""Snapshot inicial: las tablas del source deben replicarse completas al destino."""
from conftest import TestEnv, pg_count, pg_table_exists, wait_for

TABLES = ["customers", "products", "orders"]


def test_target_tables_created(tgt):
    """El JDBC sink con schema.evolution=basic crea las tablas destino."""
    for t in TABLES:
        target = TestEnv.target_table(t)
        wait_for(lambda tg=target: pg_table_exists(tgt, tg),
                 timeout=180, desc=f"tabla destino {target} creada")


def test_row_counts_match(src, tgt):
    """El snapshot inicial debe traer TODAS las filas de cada tabla."""
    for t in TABLES:
        with src.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) n FROM `{t}`")
            src_n = cur.fetchone()["n"]
        target = TestEnv.target_table(t)
        wait_for(lambda tg=target, n=src_n: pg_count(tgt, tg) >= n,
                 timeout=180, desc=f"{target} tenga >= {src_n} filas")
        assert pg_count(tgt, target) == src_n, f"{target}: conteo no coincide con source"


def test_sample_row_values(src, tgt):
    """Spot-check de valores de una fila (tipos: INT, VARCHAR, DECIMAL, DATETIME)."""
    with src.cursor() as cur:
        cur.execute("SELECT id, first_name, email FROM customers ORDER BY id LIMIT 1")
        row = cur.fetchone()
    target = TestEnv.target_table("customers")

    def check():
        with tgt.cursor() as c2:
            c2.execute(f'SELECT id, first_name, email FROM "{target}" WHERE id = %s', (row["id"],))
            return c2.fetchone()

    trow = wait_for(check, timeout=120, desc=f"fila id={row['id']} en {target}")
    assert trow[1] == row["first_name"]
    assert trow[2] == row["email"]


def test_connectors_running():
    """Ambos conectores bootstrap deben estar RUNNING."""
    import httpx
    for name in ("mysql-source-inventory", "jdbc-sink-postgres"):
        def check(n=name):
            r = httpx.get(f"{TestEnv.connect_url}/connectors/{n}/status", timeout=10)
            if r.status_code != 200:
                return False
            st = r.json()
            return (st["connector"]["state"] == "RUNNING"
                    and all(t["state"] == "RUNNING" for t in st.get("tasks", [])))
        wait_for(check, timeout=120, desc=f"conector {name} RUNNING")
