"""API del Portal de Pipelines: salud, metricas, preflight con credenciales malas."""
import httpx

from conftest import TestEnv


def test_portal_healthz():
    r = httpx.get(f"{TestEnv.portal_url}/healthz", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_portal_dashboard():
    r = httpx.get(f"{TestEnv.portal_url}/", timeout=10)
    assert r.status_code == 200
    assert "CDC Platform" in r.text


def test_portal_metrics_exposed():
    r = httpx.get(f"{TestEnv.portal_url}/metrics", timeout=10)
    assert r.status_code == 200
    assert "cdc_portal_up" in r.text
    # el poller debe haber registrado los conectores bootstrap
    assert "cdc_connector_running" in r.text


def test_portal_system_page():
    r = httpx.get(f"{TestEnv.portal_url}/system", timeout=20)
    assert r.status_code == 200
    assert "Kafka Connect REST" in r.text


def test_preflight_detects_bad_credentials():
    """Preflight con password incorrecto debe fallar sin crear nada."""
    form = {
        "source_engine": "mysql",
        "source_host": TestEnv.mysql_host,
        "source_port": str(TestEnv.mysql_port),
        "source_db": TestEnv.mysql_db,
        "source_user": TestEnv.mysql_user,
        "source_password": "password-incorrecta-a-proposito",
        "target_engine": "postgres",
        "target_host": TestEnv.pg_host,
        "target_port": str(TestEnv.pg_port),
        "target_db": TestEnv.pg_db,
        "target_user": TestEnv.pg_user,
        "target_password": "password-incorrecta-a-proposito",
    }
    r = httpx.post(f"{TestEnv.portal_url}/pipelines/preflight", data=form, timeout=60)
    assert r.status_code == 200
    assert "row-fail" in r.text
    assert "Autenticacion MySQL" in r.text


def test_preflight_sqlserver_fails_gracefully():
    """SQL Server sin instancia: los checks deben fallar con guia clara, sin romper."""
    form = {
        "source_engine": "sqlserver",
        "source_host": TestEnv.mysql_host,          # host sin SQL Server
        "source_port": "1433",
        "source_db": "inventory",
        "source_user": "cdc_user",
        "source_password": "lo-que-sea",
        "target_engine": "postgres",
        "target_host": TestEnv.pg_host,
        "target_port": str(TestEnv.pg_port),
        "target_db": TestEnv.pg_db,
        "target_user": TestEnv.pg_user,
        "target_password": TestEnv.pg_password,
    }
    r = httpx.post(f"{TestEnv.portal_url}/pipelines/preflight", data=form, timeout=60)
    assert r.status_code == 200
    assert "TCP" in r.text and "row-fail" in r.text


def test_preflight_ok_with_good_credentials():
    """Preflight con las credenciales reales del pipeline debe pasar (criticos en OK)."""
    import os
    form = {
        "source_engine": "mysql",
        "source_host": TestEnv.mysql_host,
        "source_port": str(TestEnv.mysql_port),
        "source_db": TestEnv.mysql_db,
        "source_user": os.environ.get("TEST_MYSQL_CDC_USER", "cdc_user"),
        "source_password": os.environ.get("TEST_MYSQL_CDC_PASSWORD", ""),
        "target_engine": "postgres",
        "target_host": TestEnv.pg_host,
        "target_port": str(TestEnv.pg_port),
        "target_db": TestEnv.pg_db,
        "target_user": TestEnv.pg_user,
        "target_password": TestEnv.pg_password,
    }
    if not form["source_password"]:
        import pytest
        pytest.skip("TEST_MYSQL_CDC_PASSWORD no disponible en el tester")
    r = httpx.post(f"{TestEnv.portal_url}/pipelines/preflight", data=form, timeout=60)
    assert r.status_code == 200
    assert "Todas las verificaciones" in r.text or "binlog_format=ROW" in r.text
