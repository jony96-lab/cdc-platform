import os
import time

import psycopg
import pymysql
import pytest


class TestEnv:
    mysql_host = os.environ["TEST_MYSQL_HOST"]
    mysql_port = int(os.environ["TEST_MYSQL_PORT"])
    mysql_user = os.environ["TEST_MYSQL_APP_USER"]
    mysql_password = os.environ["TEST_MYSQL_APP_PASSWORD"]
    mysql_db = os.environ["TEST_MYSQL_SOURCE_DB"]

    pg_host = os.environ["TEST_PG_HOST"]
    pg_port = int(os.environ["TEST_PG_PORT"])
    pg_db = os.environ["TEST_PG_DB"]
    pg_user = os.environ["TEST_PG_USER"]
    pg_password = os.environ["TEST_PG_PASSWORD"]

    topic_prefix = os.environ.get("TEST_TOPIC_PREFIX", "cdc")

    portal_url = os.environ.get("PORTAL_URL", "http://portal:8085")
    connect_url = os.environ.get("CONNECT_URL", "http://connect:8083")

    @classmethod
    def target_table(cls, table: str) -> str:
        """DefaultTableNamingStrategy: topic cdc.inventory.customers -> cdc_inventory_customers"""
        return f"{cls.topic_prefix}_{cls.mysql_db}_{table}".replace(".", "_")


def mysql_conn():
    return pymysql.connect(host=TestEnv.mysql_host, port=TestEnv.mysql_port,
                           user=TestEnv.mysql_user, password=TestEnv.mysql_password,
                           database=TestEnv.mysql_db, autocommit=True,
                           cursorclass=pymysql.cursors.DictCursor,
                           connect_timeout=10)


def pg_conn(dbname: str | None = None):
    return psycopg.connect(host=TestEnv.pg_host, port=TestEnv.pg_port,
                           user=TestEnv.pg_user, password=TestEnv.pg_password,
                           dbname=dbname or TestEnv.pg_db, autocommit=True,
                           connect_timeout=10)


def wait_for(predicate, timeout: float = 180.0, interval: float = 2.0, desc: str = ""):
    """Poll hasta que predicate() devuelva truthy. Lanza AssertionError al expirar."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception as e:  # errores transitorios (tabla aun no creada, etc.)
            last = f"exc: {e}"
        time.sleep(interval)
    raise AssertionError(f"Timeout ({timeout}s) esperando: {desc or predicate}. Ultimo: {last}")


def pg_table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
            (table,))
        return cur.fetchone() is not None


def pg_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]


@pytest.fixture(scope="session")
def env():
    return TestEnv


@pytest.fixture(scope="session")
def src():
    c = mysql_conn()
    yield c
    c.close()


@pytest.fixture(scope="session")
def tgt():
    c = pg_conn()
    yield c
    c.close()
