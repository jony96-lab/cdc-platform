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


def test_notifications_page_renders():
    r = httpx.get(f"{TestEnv.portal_url}/notifications", timeout=10)
    assert r.status_code == 200
    assert "Notificaciones por email" in r.text


def test_notifications_save_and_reload():
    """Guardar config debe renderizar yml + recargar Alertmanager sin error.

    SOLO se ejecuta si el Portal ya gestiona la config (si no, el marker
    portal-notif.json pisaria la config renderizada desde .env). Al terminar
    RESTAURA la configuracion previa (password vacia = conserva la guardada).
    """
    page = httpx.get(f"{TestEnv.portal_url}/notifications", timeout=10).text
    if "todavía no fue configurado" in page:
        print("  (skip: portal sin config de notificaciones; se evita pisar la de .env)")
        return

    def _field(name, text, default=""):
        import re
        m = re.search(rf'name="{name}"[^>]*value="([^"]*)"', text)
        return m.group(1) if m else default

    original = {
        "enabled": "on", "smtp_host": _field("smtp_host", page),
        "smtp_port": _field("smtp_port", page, "587") or "587",
        "smtp_tls": "on", "smtp_user": _field("smtp_user", page),
        "smtp_password": "",  # vacia = conserva la ya guardada en SQLite
        "smtp_from": _field("smtp_from", page), "alert_to": _field("alert_to", page),
    }

    fake = dict(original, smtp_host="smtp.gmail.com", smtp_user="test-cdc@gmail.com",
                smtp_from="test-cdc@gmail.com", alert_to="oncall@example.com")
    r = httpx.post(f"{TestEnv.portal_url}/notifications/save", data=fake, timeout=30,
                   follow_redirects=False)
    assert r.status_code == 303 and "/notifications?saved=1" in r.headers["location"]
    page2 = httpx.get(f"{TestEnv.portal_url}/notifications", timeout=10).text
    assert "test-cdc@gmail.com" in page2

    # restaurar
    r2 = httpx.post(f"{TestEnv.portal_url}/notifications/save", data=original,
                    timeout=30, follow_redirects=False)
    assert r2.status_code == 303 and "saved=1" in r2.headers["location"]
    page3 = httpx.get(f"{TestEnv.portal_url}/notifications", timeout=10).text
    assert original["smtp_host"] in page3


def test_notifications_validation_missing_fields():
    form = {"enabled": "on", "smtp_host": "", "smtp_port": "587", "smtp_tls": "on",
            "smtp_user": "", "smtp_password": "", "smtp_from": "", "alert_to": ""}
    r = httpx.post(f"{TestEnv.portal_url}/notifications/save", data=form, timeout=30,
                   follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]


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
