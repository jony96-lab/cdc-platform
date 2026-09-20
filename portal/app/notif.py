"""Modulo de notificaciones: config del canal email + render de alertmanager.yml.

- La config vive en SQLite (tabla notif_config, singleton id=1) y se refleja
  en build/alertmanager/portal-notif.json (marca de que el Portal gestiona la
  config: gen-secrets.ps1 la respeta y no la pisa desde .env).
- El Portal renderiza alertmanager.yml y dispara el reload de Alertmanager
  (POST /-/reload con --web.enable-lifecycle). Config invalida -> Alertmanager
  conserva la anterior y el Portal muestra el error.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import httpx

from .config import settings

log = logging.getLogger("portal.notif")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notif_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    smtp_host TEXT DEFAULT '',
    smtp_port INTEGER DEFAULT 587,
    smtp_tls INTEGER NOT NULL DEFAULT 1,
    smtp_user TEXT DEFAULT '',
    smtp_password TEXT DEFAULT '',
    smtp_from TEXT DEFAULT '',
    alert_to TEXT DEFAULT '',
    updated_at REAL
);
"""

# Plantilla LITERAL: los {{ }} de los templates Go de Alertmanager quedan intactos.
FULL_TEMPLATE = """global:
  resolve_timeout: 5m
  smtp_smarthost: '__SMTP_HOST__:__SMTP_PORT__'
  smtp_from: '__SMTP_FROM__'
  smtp_auth_username: '__SMTP_USER__'
  smtp_auth_password: '__SMTP_PASSWORD__'
  smtp_require_tls: __SMTP_TLS__
route:
  receiver: default-portal
  group_by: ["alertname", "severity"]
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers: [ 'severity = "critical"' ]
      receiver: critical-all
      group_wait: 0s
      repeat_interval: 4h
    - matchers: [ 'severity = "warning"' ]
      receiver: warning-all
      group_wait: 10m
      group_interval: 1h
      repeat_interval: 4h
receivers:
- name: default-portal
  webhook_configs:
    - url: http://portal:8085/alerts/webhook
      send_resolved: true
- name: critical-all
  email_configs:
    - to: '__ALERT_TO__'
      from: '__SMTP_FROM__'
      smarthost: '__SMTP_HOST__:__SMTP_PORT__'
      auth_username: '__SMTP_USER__'
      auth_password: '__SMTP_PASSWORD__'
      send_resolved: true
      headers:
        Subject: '[CDC CRITICAL] {{ .CommonLabels.alertname }} - {{ .CommonLabels.instance }}'
      text: |-
        ESTADO: {{ .Status | toUpper }}

        {{ range .Alerts }}
        Alerta:     {{ .Labels.alertname }}
        Severidad:  {{ .Labels.severity }}
        Resumen:    {{ .Annotations.summary }}
        Detalle:    {{ .Annotations.description }}
        Desde:      {{ .StartsAt }}
        {{ end }}

        Portal:     http://localhost:8085/alerts
  webhook_configs:
    - url: http://portal:8085/alerts/webhook
      send_resolved: true
- name: warning-all
  email_configs:
    - to: '__ALERT_TO__'
      from: '__SMTP_FROM__'
      smarthost: '__SMTP_HOST__:__SMTP_PORT__'
      auth_username: '__SMTP_USER__'
      auth_password: '__SMTP_PASSWORD__'
      send_resolved: true
      headers:
        Subject: '[CDC warning] {{ .CommonLabels.alertname }} - {{ .CommonLabels.instance }}'
      text: |-
        ESTADO: {{ .Status | toUpper }}

        {{ range .Alerts }}
        Alerta:     {{ .Labels.alertname }}
        Severidad:  {{ .Labels.severity }}
        Resumen:    {{ .Annotations.summary }}
        Detalle:    {{ .Annotations.description }}
        Desde:      {{ .StartsAt }}
        {{ end }}

        Portal:     http://localhost:8085/alerts
  webhook_configs:
    - url: http://portal:8085/alerts/webhook
      send_resolved: true
inhibit_rules:
  - source_matchers: [ 'alertname = "ConnectWorkerDown"' ]
    target_matchers: [ 'severity = "warning"' ]
"""

PORTAL_ONLY_TEMPLATE = """route:
  receiver: default-portal
  group_by: ["alertname", "severity"]
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
receivers:
- name: default-portal
  webhook_configs:
    - url: http://portal:8085/alerts/webhook
      send_resolved: true
inhibit_rules:
  - source_matchers: [ 'alertname = "ConnectWorkerDown"' ]
    target_matchers: [ 'severity = "warning"' ]
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(settings.portal_db_path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)


def get_config() -> dict:
    """Config guardada en SQLite (sin password). None si nunca se configuro por UI."""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM notif_config WHERE id=1").fetchone()
    if not row:
        return None
    d = dict(row)
    d.pop("smtp_password", None)  # nunca sale de la API
    d["enabled"] = bool(d["enabled"])
    d["smtp_tls"] = bool(d["smtp_tls"])
    return d


def save_config(data: dict) -> None:
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO notif_config (id, enabled, smtp_host, smtp_port, smtp_tls,
                                        smtp_user, smtp_password, smtp_from, alert_to, updated_at)
               VALUES (1,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 enabled=excluded.enabled, smtp_host=excluded.smtp_host,
                 smtp_port=excluded.smtp_port, smtp_tls=excluded.smtp_tls,
                 smtp_user=excluded.smtp_user, smtp_from=excluded.smtp_from,
                 alert_to=excluded.alert_to, updated_at=excluded.updated_at""",
            (1 if data.get("enabled") else 0, data.get("smtp_host", ""),
             int(data.get("smtp_port") or 587), 1 if data.get("smtp_tls", True) else 0,
             data.get("smtp_user", ""), data.get("smtp_password", ""),
             data.get("smtp_from", ""), data.get("alert_to", ""), time.time()),
        )
    # si el form vino sin password y ya habia una guardada, conservarla
    if not data.get("smtp_password"):
        with _lock, _conn() as c:
            c.execute(
                """UPDATE notif_config SET smtp_password =
                     COALESCE((SELECT smtp_password FROM notif_config WHERE id=1
                               AND smtp_password != '' LIMIT 1), '')
                   WHERE id=1"""
            )


def _render(cfg: dict) -> str:
    subs = {
        "__SMTP_HOST__": cfg.get("smtp_host", ""),
        "__SMTP_PORT__": str(cfg.get("smtp_port") or 587),
        "__SMTP_FROM__": cfg.get("smtp_from", ""),
        "__SMTP_USER__": cfg.get("smtp_user", ""),
        "__SMTP_PASSWORD__": cfg.get("smtp_password", ""),
        "__ALERT_TO__": cfg.get("alert_to", ""),
        "__SMTP_TLS__": "true" if cfg.get("smtp_tls", True) else "false",
    }
    out = FULL_TEMPLATE
    for k, v in subs.items():
        out = out.replace(k, v)
    return out


def _conf_dir() -> str:
    d = os.getenv("ALERTMANAGER_CONF_DIR", "/etc/cdc-alertmanager")
    os.makedirs(d, exist_ok=True)
    return d


def apply_config(data: dict) -> dict:
    """Guarda config + escribe alertmanager.yml + marca portal-notif.json + reload."""
    save_config(data)
    cfg = _conn().execute("SELECT * FROM notif_config WHERE id=1").fetchone()
    cfg = dict(cfg)
    enabled = bool(cfg["enabled"])
    conf = _render(cfg) if enabled else PORTAL_ONLY_TEMPLATE

    conf_dir = _conf_dir()
    yml = os.path.join(conf_dir, "alertmanager.yml")
    old = yml + ".old"
    if os.path.exists(yml):
        os.replace(yml, old)
    try:
        with open(yml, "w", encoding="utf-8") as f:
            f.write(conf)
        with open(os.path.join(conf_dir, "portal-notif.json"), "w", encoding="utf-8") as f:
            json.dump({"managed_by": "portal", "enabled": enabled,
                       "smtp_host": cfg["smtp_host"], "updated_at": time.time()}, f)
        if old and os.path.exists(old):
            os.remove(old)
    except Exception:
        if os.path.exists(old):
            os.replace(old, yml)
        raise

    # reload de Alertmanager (config invalida -> conserva la anterior y devuelve error)
    reload_err = None
    try:
        r = httpx.post(f"{settings.alertmanager_url}/-/reload", timeout=10)
        if r.status_code >= 400:
            reload_err = f"Alertmanager rechazo la config (HTTP {r.status_code}): {r.text[:300]}"
            log.warning(reload_err)
    except Exception as e:
        reload_err = f"No pude recargar Alertmanager: {e}"
        log.warning(reload_err)

    return {"enabled": enabled, "reload_ok": reload_err is None, "reload_error": reload_err}


def send_test_alert() -> dict:
    """Dispara una alerta sintetica critical -> llega por email (y Portal)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload = [{
        "labels": {"alertname": "NotificacionDePrueba", "severity": "critical",
                   "instance": "portal-test", "job": "manual"},
        "annotations": {
            "summary": "Notificacion de prueba desde el Portal CDC",
            "description": ("Si lees esto, el canal de email configurado desde el Portal "
                            "funciona. Esta alerta de prueba expira sola en unos minutos."),
        },
        "startsAt": now,
    }]
    r = httpx.post(f"{settings.alertmanager_url}/api/v2/alerts", json=payload, timeout=15)
    return {"status": r.status_code, "ok": r.status_code < 400}
