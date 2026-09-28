"""Lakehouse: CDC -> Iceberg/Parquet en MinIO, consultado via Trino.

Consciente del origen (LH_SOURCE_ENGINE = mysql | postgres, parametrizable en .env):
  - postgres: captura demo_lh (PG del host), tabla demo: lh_cdclh_public_ventas
  - mysql: captura inventory (MySQL del host), tabla demo: lh_cdclh_inventory_customers

Los tests usan la API HTTP de Trino (POST /v1/statement + paginacion nextUri).
"""
import json
import os
import time
import uuid

import httpx

TRINO = "http://trino:8080"
CATALOG = "iceberg"
SCHEMA = "cdc"
ENGINE = os.environ.get("LH_SOURCE_ENGINE", "mysql").lower()


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


def _pg_conn(db: str):
    import psycopg
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=int(os.environ["TEST_PG_PORT"]),
                           user=os.environ["TEST_PG_USER"], password=os.environ["TEST_PG_PASSWORD"],
                           dbname=db, autocommit=True,
                           connect_timeout=10)


def _mysql_conn(db: str):
    import pymysql
    return pymysql.connect(host=os.environ["TEST_MYSQL_HOST"], port=int(os.environ["TEST_MYSQL_PORT"]),
                           user=os.environ["TEST_MYSQL_APP_USER"],
                           password=os.environ["TEST_MYSQL_APP_PASSWORD"],
                           database=db, autocommit=True,
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=10)


def _sql_exec(sql: str, params: tuple = ()) -> None:
    if ENGINE == "postgres":
        with _pg_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    else:
        with _mysql_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute(sql, params)


# --- configuracion por motor ---

if ENGINE == "postgres":
    LH_TABLE = "lh_cdclh_public_ventas"          # tabla demo que vive en demo_lh.ventas
    LH_KEY = "id"
    LH_COLUMNS = "(producto, cantidad, precio)"
    LH_INSERT = ("INSERT INTO ventas (producto, cantidad, precio) VALUES (%s, %s, %s)",
                 lambda uid: (f"LH {uid}", 1, 9.99))
else:
    LH_TABLE = "lh_cdclh_inventory_customers"
    LH_KEY = "id"
    LH_COLUMNS = "(first_name, last_name, email)"
    LH_INSERT = ("INSERT INTO customers (first_name,last_name,email) VALUES (%s, %s, %s)",
                 lambda uid: ("LH", "Test", f"lh.{uid}@example.com"))


def _insert_demo_row() -> int:
    """Inserta una fila demo en el ORIGEN y devuelve su clave."""
    uid = uuid.uuid4().hex[:8]
    if ENGINE == "postgres":
        with _pg_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ventas (producto, cantidad, precio) VALUES (%s, %s, %s) RETURNING id",
                        (f"LH {uid}", 1, 9.99))
            return cur.fetchone()[0]
    else:
        email = f"lh.{uid}@example.com"
        with _mysql_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO customers (first_name,last_name,email) VALUES ('LH','Test',%s)",
                        (email,))
            return cur.lastrowid


def _source_count(table_query: str) -> int:
    if ENGINE == "postgres":
        with _pg_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table_query}")
            return cur.fetchone()[0]
    else:
        with _mysql_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) n FROM {table_query}")
            return cur.fetchone()["n"]


def test_lakehouse_tables_exist():
    tables = trino_query(f"SHOW TABLES FROM {CATALOG}.{SCHEMA}")
    names = [r[0] for r in tables]
    assert LH_TABLE in names, f"tabla lakehouse {LH_TABLE} no existe (hay: {names})"


def test_lakehouse_counts_match_source():
    if ENGINE == "postgres":
        src = _source_count("ventas")
    else:
        src = _source_count("customers")

    deadline = time.time() + 150
    while time.time() < deadline:
        lh = int(trino_query(f"SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.{LH_TABLE}")[0][0])
        if lh == src:
            return
        time.sleep(6)
    raise AssertionError(f"lakehouse ({lh}) != source ({src}) tras 150s")


def test_lakehouse_live_dml():
    """INSERT en el origen -> aparece en el lakehouse (parquet) en tiempo razonable."""
    cid = _insert_demo_row()
    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        rows = trino_query(
            f"SELECT * FROM {CATALOG}.{SCHEMA}.{LH_TABLE} WHERE {LH_KEY} = {cid}")
        if rows:
            return
        time.sleep(6)
    raise AssertionError(f"INSERT id={cid} no llego al lakehouse en 180s (ultimo: {last})")


def test_lakehouse_delete_propagates():
    cid = _insert_demo_row()
    deadline = time.time() + 180
    while time.time() < deadline:
        if trino_query(f"SELECT {LH_KEY} FROM {CATALOG}.{SCHEMA}.{LH_TABLE} WHERE {LH_KEY} = {cid}"):
            break
        time.sleep(6)

    if ENGINE == "postgres":
        with _pg_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ventas WHERE id=%s", (cid,))
    else:
        with _mysql_conn(os.environ["LH_SOURCE_DB"]) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM customers WHERE id=%s", (cid,))

    deadline = time.time() + 180
    while time.time() < deadline:
        rows = trino_query(f"SELECT {LH_KEY} FROM {CATALOG}.{SCHEMA}.{LH_TABLE} WHERE {LH_KEY} = {cid}")
        if not rows:
            return  # eliminado en el lakehouse (upsert-keep-deletes=false)
        time.sleep(6)
    raise AssertionError(f"DELETE id={cid} no se propago al lakehouse en 180s")
