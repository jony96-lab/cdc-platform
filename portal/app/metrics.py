"""Metricas expuestas por el portal (/metrics) y poller de estado.

El portal consulta la REST API de Kafka Connect cada N segundos y expone:
  cdc_connector_running{connector,type,pipeline}   1/0
  cdc_task_running{connector,task}                 1/0
  cdc_task_failed{connector,task}                  1/0
  cdc_pipeline_info{...}                           1
Ademas re-expone el lag de Debezium parseado del JMX exporter de Connect:
  cdc_milli_seconds_behind_source{server}
"""
import asyncio
import logging
import re

import httpx
from prometheus_client import Gauge, Info

from . import db
from .config import settings

log = logging.getLogger("portal.metrics")

CONNECTOR_RUNNING = Gauge(
    "cdc_connector_running", "1 si el conector esta RUNNING",
    ["connector", "type", "pipeline"])
TASK_RUNNING = Gauge(
    "cdc_task_running", "1 si la task esta RUNNING", ["connector", "task"])
TASK_FAILED = Gauge(
    "cdc_task_failed", "1 si la task esta FAILED", ["connector", "task"])
PIPELINE_INFO = Gauge(
    "cdc_pipeline_info", "Pipeline registrado en el portal",
    ["pipeline", "source_connector", "sink_connector", "source_engine", "target_engine"])
MILLIS_BEHIND_SOURCE = Gauge(
    "cdc_milli_seconds_behind_source",
    "Lag del source Debezium (ms) re-expuesto desde JMX de Connect", ["server"])
COMPONENT_UP = Gauge(
    "cdc_component_up", "1 si el componente esta alcanzable", ["component"])
PORTAL_UP = Gauge("cdc_portal_up", "Portal vivo", [])

_LAG_PATTERN = re.compile(
    r'^debezium_(?:mysql|postgres|sql_server)_streaming_millisecondsbehindsource'
    r'\{[^}]*server="([^"]+)"[^}]*\}\s+([\d.eE+-]+)', re.MULTILINE)

# estado en memoria para las vistas
CACHE: dict = {"connectors": {}, "lag": {}, "connect_up": False,
               "connect_version": "", "last_poll": 0.0}


async def _scrape_lag() -> dict:
    out = {}
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(settings.connect_metrics_url)
            if r.status_code == 200:
                for m in _LAG_PATTERN.finditer(r.text):
                    out[m.group(1)] = float(m.group(2))
    except Exception as e:
        log.debug("scrape lag fallo: %s", e)
    return out


async def poll_once(client) -> None:
    import time
    try:
        root = await client.root()
        CACHE["connect_up"] = True
        CACHE["connect_version"] = root.get("version", "?")
        statuses = await client.get_all_statuses()
    except Exception as e:
        CACHE["connect_up"] = False
        log.warning("Connect REST no disponible: %s", e)
        return

    pipelines = db.list_pipelines()
    pipe_of = {}
    for p in pipelines:
        pipe_of[p["source_connector"]] = p["name"]
        pipe_of[p["sink_connector"]] = p["name"]

    seen_connectors = set()
    for name, st in statuses.items():
        seen_connectors.add(name)
        ctype = "sink" if "jdbc" in name or "sink" in name else "source"
        state = st.get("connector", {}).get("state", "UNKNOWN")
        CONNECTOR_RUNNING.labels(connector=name, type=ctype,
                                 pipeline=pipe_of.get(name, "-")).set(
            1 if state == "RUNNING" else 0)
        for t in st.get("tasks", []):
            tid = str(t.get("id"))
            tstate = t.get("state", "UNKNOWN")
            TASK_RUNNING.labels(connector=name, task=tid).set(1 if tstate == "RUNNING" else 0)
            TASK_FAILED.labels(connector=name, task=tid).set(1 if tstate == "FAILED" else 0)
        CACHE["connectors"][name] = st

    # limpiar series de conectores borrados
    for name in list(CACHE["connectors"].keys()):
        if name not in seen_connectors:
            CACHE["connectors"].pop(name, None)

    lag = await _scrape_lag()
    CACHE["lag"] = lag
    for server, ms in lag.items():
        MILLIS_BEHIND_SOURCE.labels(server=server).set(ms)

    for p in pipelines:
        PIPELINE_INFO.labels(pipeline=p["name"], source_connector=p["source_connector"],
                             sink_connector=p["sink_connector"],
                             source_engine=p["source_engine"],
                             target_engine=p["target_engine"]).set(1)
    PORTAL_UP.set(1)
    CACHE["last_poll"] = time.time()


import socket as _socket
_LH_COMPONENTS = [("minio", "minio", 9000), ("trino", "trino", 8080),
                  ("debezium-lakehouse", "debezium-lakehouse", 8080)]


def _tcp_up(host: str, port: int) -> bool:
    try:
        with _socket.create_connection((host, int(port)), timeout=3):
            return True
    except OSError:
        return False


async def check_lakehouse_components() -> None:
    for label, host, port in _LH_COMPONENTS:
        ok = await asyncio.to_thread(_tcp_up, host, port)
        COMPONENT_UP.labels(component=label).set(1 if ok else 0)


async def poller_loop(client) -> None:
    while True:
        try:
            await poll_once(client)
            await check_lakehouse_components()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll_once fallo")
        await asyncio.sleep(settings.poll_interval_sec)
