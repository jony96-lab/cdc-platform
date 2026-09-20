"""DML: INSERT / UPDATE / DELETE deben propagarse al destino (espejo exacto)."""
import time
import uuid

from conftest import TestEnv, wait_for


def _fetch_one(tgt, table, where_sql, params):
    with tgt.cursor() as cur:
        cur.execute(f'SELECT * FROM "{table}" WHERE {where_sql}', params)
        cols = [d.name for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None


def test_insert_propagates(src, tgt):
    email = f"e2e.insert.{uuid.uuid4().hex[:10]}@example.com"
    with src.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (first_name,last_name,email,phone,city) "
            "VALUES ('E2E','Insert',%s,'+00 000','TestCity')", (email,))
        cid = cur.lastrowid

    table = TestEnv.target_table("customers")
    row = wait_for(lambda: _fetch_one(tgt, table, "id = %s", (cid,)),
                   timeout=90, desc=f"INSERT id={cid} en destino")
    assert row["first_name"] == "E2E"
    assert row["city"] == "TestCity"


def test_update_propagates(src, tgt):
    email = f"e2e.update.{uuid.uuid4().hex[:10]}@example.com"
    with src.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (first_name,last_name,email,city) VALUES ('E2E','Update',%s,'Antes')",
            (email,))
        cid = cur.lastrowid

    table = TestEnv.target_table("customers")
    wait_for(lambda: _fetch_one(tgt, table, "id = %s", (cid,)), timeout=90,
             desc=f"fila {cid} creada")

    with src.cursor() as cur:
        cur.execute("UPDATE customers SET city='Despues', first_name='E2E2' WHERE id=%s", (cid,))

    def check():
        r = _fetch_one(tgt, table, "id = %s", (cid,))
        return r if r and r["city"] == "Despues" and r["first_name"] == "E2E2" else None

    wait_for(check, timeout=90, desc=f"UPDATE de id={cid} propagado")


def test_delete_propagates(src, tgt):
    email = f"e2e.delete.{uuid.uuid4().hex[:10]}@example.com"
    with src.cursor() as cur:
        cur.execute("INSERT INTO customers (first_name,last_name,email) VALUES ('E2E','Delete',%s)",
                    (email,))
        cid = cur.lastrowid

    table = TestEnv.target_table("customers")
    wait_for(lambda: _fetch_one(tgt, table, "id = %s", (cid,)), timeout=90,
             desc=f"fila {cid} creada")

    with src.cursor() as cur:
        cur.execute("DELETE FROM customers WHERE id=%s", (cid,))

    def gone():
        return True if _fetch_one(tgt, table, "id = %s", (cid,)) is None else None

    wait_for(gone, timeout=90, desc=f"DELETE de id={cid} propagado (delete.enabled=true)")


def test_numeric_and_json_types(src, tgt):
    """DECIMAL/JSON/ENUM viajan bien (mapeo de tipos del JDBC sink)."""
    sku = f"E2E-{uuid.uuid4().hex[:8].upper()}"
    with src.cursor() as cur:
        cur.execute(
            "INSERT INTO products (sku,name,description,price,stock,metadata) "
            "VALUES (%s,'Prod E2E','desc',123.45,7,'{\"k\": \"v\", \"n\": 42}')", (sku,))
        pid = cur.lastrowid

    table = TestEnv.target_table("products")
    row = wait_for(lambda: _fetch_one(tgt, table, "id = %s", (pid,)), timeout=90,
                   desc=f"producto {pid} en destino")
    assert float(row["price"]) == 123.45
    assert row["stock"] == 7


def test_enum_status_update(src, tgt):
    with src.cursor() as cur:
        cur.execute("SELECT id FROM orders ORDER BY id LIMIT 1")
        oid = cur.fetchone()["id"]
        cur.execute("UPDATE orders SET status='cancelled' WHERE id=%s", (oid,))

    table = TestEnv.target_table("orders")

    def check():
        r = _fetch_one(tgt, table, "id = %s", (oid,))
        return r if r and str(r["status"]) == "cancelled" else None

    wait_for(check, timeout=90, desc=f"ENUM update en order {oid}")
