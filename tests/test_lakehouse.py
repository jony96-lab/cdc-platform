"""Lakehouse: CDC -> Iceberg/Parquet en MinIO, consultado via Trino.

Los tests usan la API HTTP de Trino (POST /v1/statement) desde el tester.
Asumen que el pipeline lakehouse esta levantado (scripts/lakehouse-up.ps1).
"""
import json
import time
import uuid

import httpx

TRINO = "http://trino:8080"
CATALOG = "iceberg"
SCHEMA = "cdc"


def trino_query(sql: str) -> list[tuple]:
    """La API de Trino es asincrona: hay que seguir nextUri hasta el bloque final."""
    headers = {"X-Trino-User": "cdc-tester"}
    r = httpx.post(f"{TRINO}/v1/statement", content=sql, timeout=60, headers=headers)
    r.raise_for_status()
    rows = []
    while True:
        data = r.json()
        if data.get("error"):
            raise RuntimeError(f"Trino error: {data['error'].get('message')}")
        for rec in data.get("data", []):
            rows.append(tuple(rec))
        nxt = data.get("nextUri")
        if not nxt:
            break
        r = httpx.get(nxt, timeout=60, headers=headers)
        r.raise_for_status()
    return rows


def mysql_counts(cursor_factory=None):
    import os
    import pymysql
    conn = pymysql.connect(
        host=os.environ["TEST_MYSQL_HOST"], port=int(os.environ["TEST_MYSQL_PORT"]),
        user=os.environ["TEST_MYSQL_APP_USER"], password=os.environ["TEST_MYSQL_APP_PASSWORD"],
        database=os.environ["TEST_MYSQL_SOURCE_DB"], autocommit=True,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) n FROM customers")
            c = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) n FROM orders")
            o = cur.fetchone()["n"]
        return c, o
    finally:
        conn.close()


def test_lakehouse_tables_exist():
    tables = trino_query(f"SHOW TABLES FROM {CATALOG}.{SCHEMA}")
    names = [r[0] for r in tables]
    for t in ("lh_cdclh_inventory_customers", "lh_cdclh_inventory_products",
              "lh_cdclh_inventory_orders"):
        assert t in names, f"tabla lakehouse {t} no existe (hay: {names})"


def test_lakehouse_counts_match_source():
    src_c, src_o = mysql_counts()

    def check():
        lh_c = int(trino_query(f"SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.lh_cdclh_inventory_customers")[0][0])
        lh_o = int(trino_query(f"SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.lh_cdclh_inventory_orders")[0][0])
        return (lh_c, lh_o) if (lh_c, lh_o) == (src_c, src_o) else None

    assert check() is not None, (
        f"lakehouse != source: {(src_c, src_o)} vs {check()}")


def test_lakehouse_live_dml():
    """INSERT en MySQL -> aparece en el lakehouse (parquet) en tiempo razonable."""
    email = f"lh.test.{uuid.uuid4().hex[:10]}@example.com"
    import os
    import pymysql
    conn = pymysql.connect(
        host=os.environ["TEST_MYSQL_HOST"], port=int(os.environ["TEST_MYSQL_PORT"]),
        user=os.environ["TEST_MYSQL_APP_USER"], password=os.environ["TEST_MYSQL_APP_PASSWORD"],
        database=os.environ["TEST_MYSQL_SOURCE_DB"], autocommit=True,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customers (first_name,last_name,email,city) "
                "VALUES ('LH','Test',%s,'LakehouseCity')", (email,))
            cid = cur.lastrowid
    finally:
        conn.close()

    deadline = time.time() + 120
    last = None
    while time.time() < deadline:
        rows = trino_query(
            f"SELECT first_name, city, __op, __deleted FROM {CATALOG}.{SCHEMA}."
            f"lh_cdclh_inventory_customers WHERE id = {cid}")
        if rows:
            assert rows[0][0] == "LH" and rows[0][1] == "LakehouseCity"
            return
        time.sleep(5)
    raise AssertionError(f"INSERT id={cid} no llego al lakehouse en 120s (ultimo: {last})")


def test_lakehouse_delete_propagates():
    email = f"lh.del.{uuid.uuid4().hex[:10]}@example.com"
    import os
    import pymysql
    conn = pymysql.connect(
        host=os.environ["TEST_MYSQL_HOST"], port=int(os.environ["TEST_MYSQL_PORT"]),
        user=os.environ["TEST_MYSQL_APP_USER"], password=os.environ["TEST_MYSQL_APP_PASSWORD"],
        database=os.environ["TEST_MYSQL_SOURCE_DB"], autocommit=True,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO customers (first_name,last_name,email) VALUES ('LH','Del',%s)",
                        (email,))
            cid = cur.lastrowid
    finally:
        conn.close()

    deadline = time.time() + 120
    while time.time() < deadline:
        if trino_query(f"SELECT id FROM {CATALOG}.{SCHEMA}.lh_cdclh_inventory_customers WHERE id = {cid}"):
            break
        time.sleep(5)

    with pymysql.connect(
            host=os.environ["TEST_MYSQL_HOST"], port=int(os.environ["TEST_MYSQL_PORT"]),
            user=os.environ["TEST_MYSQL_APP_USER"], password=os.environ["TEST_MYSQL_APP_PASSWORD"],
            database=os.environ["TEST_MYSQL_SOURCE_DB"], autocommit=True,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=10).cursor() as cur:
        cur.execute("DELETE FROM customers WHERE id=%s", (cid,))

    deadline = time.time() + 120
    while time.time() < deadline:
        rows = trino_query(
            f"SELECT id FROM {CATALOG}.{SCHEMA}.lh_cdclh_inventory_customers WHERE id = {cid}")
        if not rows:
            return  # eliminado en el lakehouse (upsert-keep-deletes=false)
        time.sleep(5)
    raise AssertionError(f"DELETE id={cid} no se propago al lakehouse en 120s")
