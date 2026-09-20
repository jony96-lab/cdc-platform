"""DDL / schema evolution: ALTER TABLE ADD COLUMN debe propagarse (schema.evolution=basic)."""
import uuid

from conftest import TestEnv, wait_for

COL = "e2e_flag_col"


def _column_exists(tgt, table, column) -> bool:
    with tgt.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
            (table, column))
        return cur.fetchone() is not None


def test_add_column_propagates(src, tgt):
    table = TestEnv.target_table("customers")

    # ADD COLUMN en source (idempotente manual: MySQL 8.0 no soporta IF NOT EXISTS)
    with src.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) n FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='customers' AND column_name=%s", (COL,))
        exists_src = cur.fetchone()["n"] > 0
        if not exists_src:
            cur.execute(f"ALTER TABLE customers ADD COLUMN {COL} VARCHAR(64) NULL")

    # El JDBC sink aplica schema evolution de forma LAZY: crea la columna destino
    # cuando llega el primer registro con el schema nuevo (no consume el topic de DDL).
    # Forzamos un UPDATE para generar ese registro.
    with src.cursor() as cur:
        cur.execute(f"UPDATE customers SET {COL} = CONCAT(COALESCE({COL},''), '') "
                    "WHERE id = (SELECT min_id FROM (SELECT MIN(id) min_id FROM customers) t)")

    wait_for(lambda: _column_exists(tgt, table, COL), timeout=120,
             desc=f"columna {COL} creada en destino por schema.evolution=basic")


def test_insert_with_new_column(src, tgt):
    table = TestEnv.target_table("customers")
    val = f"flag-{uuid.uuid4().hex[:8]}"
    email = f"e2e.ddl.{uuid.uuid4().hex[:10]}@example.com"
    with src.cursor() as cur:
        cur.execute(
            f"INSERT INTO customers (first_name,last_name,email,{COL}) "
            "VALUES ('E2E','DDL',%s,%s)", (email, val))
        cid = cur.lastrowid

    def check():
        with tgt.cursor() as c2:
            c2.execute(f'SELECT "{COL}" FROM "{table}" WHERE id=%s', (cid,))
            row = c2.fetchone()
            return row[0] if row and row[0] == val else None

    got = wait_for(check, timeout=90, desc=f"valor de {COL} propagado para id={cid}")
    assert got == val
