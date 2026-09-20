import asyncio
import json
import logging
import socket
import time
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import db, metrics, preflight
from .config import settings
from .connect_client import ConnectClient
from .pipeline_factory import deploy_pipeline, slugify

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portal")

templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    client = ConnectClient(settings.connect_url)
    app.state.client = client
    task = asyncio.create_task(metrics.poller_loop(client))
    log.info("Portal iniciado. Connect=%s", settings.connect_url)
    yield
    task.cancel()
    await client.close()


app = FastAPI(title="CDC Pipelines Portal", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _ctx(request: Request, **extra) -> dict:
    base = {
        "request": request,
        "settings": settings,
        "nav_alerts": db.count_active_alerts(),
        "connect_up": metrics.CACHE.get("connect_up", False),
    }
    base.update(extra)
    return base


# ============================ Salud ============================

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "ts": time.time()}


@app.get("/metrics")
async def prom_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================ Dashboard ============================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request))


@app.get("/partials/pipelines", response_class=HTMLResponse)
async def pipelines_partial(request: Request):
    pipelines = db.list_pipelines()
    statuses = metrics.CACHE.get("connectors", {})
    known = set()
    rows = []
    for p in pipelines:
        src = statuses.get(p["source_connector"])
        snk = statuses.get(p["sink_connector"])
        known.update([p["source_connector"], p["sink_connector"]])
        rows.append({
            "pipeline": p,
            "source_state": (src or {}).get("connector", {}).get("state", "?"),
            "sink_state": (snk or {}).get("connector", {}).get("state", "?"),
            "lag_ms": metrics.CACHE.get("lag", {}).get(p["topic_prefix"]),
        })
    unmanaged = []
    for name, st in statuses.items():
        if name not in known:
            unmanaged.append({"name": name,
                              "state": st.get("connector", {}).get("state", "?")})
    return templates.TemplateResponse(
        request, "partials/pipelines_rows.html",
        {"rows": rows, "unmanaged": unmanaged,
         "last_poll": metrics.CACHE.get("last_poll", 0),
         "connect_up": metrics.CACHE.get("connect_up", False)})


# ============================ Wizard ============================

@app.get("/pipelines/new", response_class=HTMLResponse)
async def wizard(request: Request):
    return templates.TemplateResponse(request, "wizard.html", _ctx(
        request,
        defaults={
            "mysql_host": settings.mysql_host, "mysql_port": settings.mysql_port,
            "pg_host": settings.pg_host, "pg_port": settings.pg_port,
            "pg_target_db": settings.pg_target_db,
        }))


@app.post("/pipelines/preflight", response_class=HTMLResponse)
async def run_preflight(
    request: Request,
    source_engine: str = Form(...), source_host: str = Form(...),
    source_port: int = Form(...), source_db: str = Form(...),
    source_user: str = Form(...), source_password: str = Form(...),
    target_engine: str = Form(...), target_host: str = Form(...),
    target_port: int = Form(...), target_db: str = Form(...),
    target_user: str = Form(...), target_password: str = Form(...),
):
    checks = []
    checks += await anyio.to_thread.run_sync(
        lambda: preflight.db_checks(source_engine, "source", source_host,
                                    source_port, source_user, source_password, source_db))
    checks += await anyio.to_thread.run_sync(
        lambda: preflight.db_checks(target_engine, "target", target_host,
                                    target_port, target_user, target_password, target_db))
    # plugins disponibles en Connect
    from .pipeline_factory import SINK_CLASS, SOURCE_CLASSES
    try:
        plugins = await app.state.client.connector_plugins()
        names = {pl.get("class") for pl in plugins}
        src_cls = SOURCE_CLASSES.get(source_engine, "")
        checks.append({"name": f"Plugin source ({source_engine})", "ok": src_cls in names,
                       "detail": src_cls, "fix": "Falta el plugin en /kafka/connect de la imagen Connect."})
        checks.append({"name": "Plugin JDBC sink", "ok": SINK_CLASS in names,
                       "detail": SINK_CLASS,
                       "fix": "Rebuildea la imagen connect (images/connect/Dockerfile)."})
    except Exception as e:
        checks.append({"name": "Kafka Connect REST", "ok": False, "detail": str(e)[:150],
                       "fix": "El worker Connect no responde en " + settings.connect_url})

    ok_all = all(c["ok"] for c in checks if "opcional" not in c["name"])
    return templates.TemplateResponse(
        request, "partials/preflight.html",
        {"checks": checks, "ok_all": ok_all}, status_code=200)


@app.post("/pipelines", response_class=HTMLResponse)
async def create_pipeline(
    request: Request,
    name: str = Form(...),
    source_engine: str = Form(...), source_host: str = Form(...),
    source_port: int = Form(...), source_db: str = Form(...),
    source_user: str = Form(...), source_password: str = Form(...),
    source_tables: str = Form(""),
    target_engine: str = Form(...), target_host: str = Form(...),
    target_port: int = Form(...), target_db: str = Form(...),
    target_user: str = Form(...), target_password: str = Form(...),
):
    slug = slugify(name)
    server_id = str(100000 + (hash(slug) % 800000))
    p = {
        "name": name, "slug": slug,
        "source_engine": source_engine, "source_host": source_host,
        "source_port": source_port, "source_db": source_db, "source_user": source_user,
        "source_tables": source_tables,
        "topic_prefix": f"p_{slug}", "server_id": server_id,
        "target_engine": target_engine, "target_host": target_host,
        "target_port": target_port, "target_db": target_db, "target_user": target_user,
        "source_connector": f"{slug}-source", "sink_connector": f"{slug}-sink",
        "secrets_file": "", "created_at": time.time(),
    }
    try:
        result = await deploy_pipeline(app.state.client, p, source_password, target_password)
    except Exception as e:
        log.exception("deploy_pipeline fallo")
        return templates.TemplateResponse(
            request, "partials/error.html",
            {"error": f"No se pudo crear el pipeline: {e}"}, status_code=400)
    p["secrets_file"] = result["secrets_file"]
    try:
        pid = db.insert_pipeline(p)
    except Exception as e:
        log.exception("insert_pipeline fallo (conectores YA creados)")
        return templates.TemplateResponse(
            request, "partials/error.html",
            {"error": f"Conectores creados pero fallo el registro: {type(e).__name__}: {e}"},
            status_code=400)
    log.info("Pipeline creado: id=%s name=%s", pid, name)
    return RedirectResponse(url=f"/pipelines/{pid}", status_code=303)


# ============================ Detalle ============================

@app.get("/pipelines/{pid}", response_class=HTMLResponse)
async def pipeline_detail(request: Request, pid: int):
    p = db.get_pipeline(pid)
    if not p:
        return HTMLResponse("Pipeline no encontrado", status_code=404)
    client: ConnectClient = app.state.client
    detail = {}
    for key, cname in (("source", p["source_connector"]), ("sink", p["sink_connector"])):
        try:
            detail[key] = {"status": await client.get_status(cname),
                           "config": await client.get_connector(cname),
                           "topics": await client.topics(cname)}
        except Exception as e:
            detail[key] = {"error": str(e)[:200]}
    return templates.TemplateResponse(request, "pipeline_detail.html",
                                      _ctx(request, p=p, detail=detail))


@app.post("/pipelines/{pid}/action/{action}", response_class=HTMLResponse)
async def pipeline_action(request: Request, pid: int, action: str):
    p = db.get_pipeline(pid)
    if not p:
        return HTMLResponse("Pipeline no encontrado", status_code=404)
    client: ConnectClient = app.state.client
    names = [p["source_connector"], p["sink_connector"]]
    try:
        if action == "pause":
            for n in names:
                await client.pause(n)
        elif action == "resume":
            for n in names:
                await client.resume(n)
        elif action == "restart":
            for n in names:
                await client.restart(n)
        elif action == "delete":
            for n in names:
                await client.delete(n)
            db.delete_pipeline(pid)
            import os
            try:
                os.remove(os.path.join(settings.secrets_dir, p["secrets_file"]))
            except OSError:
                pass
            return RedirectResponse(url="/", status_code=303)
        else:
            return HTMLResponse(f"Accion desconocida: {action}", status_code=400)
    except Exception as e:
        log.exception("accion %s fallo", action)
    await metrics.poll_once(client)
    return RedirectResponse(url=f"/pipelines/{pid}", status_code=303)


# ============================ Alertas ============================

@app.post("/alerts/webhook")
async def alerts_webhook(request: Request):
    payload = await request.json()
    for a in payload.get("alerts", []):
        labels = a.get("labels", {})
        annotations = a.get("annotations", {})
        db.upsert_alert({
            "fingerprint": a.get("fingerprint", json.dumps(labels, sort_keys=True)),
            "alertname": labels.get("alertname", "?"),
            "severity": labels.get("severity", "warning"),
            "status": a.get("status", "firing"),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "labels_json": json.dumps(labels),
            "starts_at": a.get("startsAt", ""),
            "ends_at": a.get("endsAt", ""),
        })
    return {"received": len(payload.get("alerts", []))}


@app.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request):
    return templates.TemplateResponse(request, "alerts.html", _ctx(
        request, alerts=db.list_alerts(), active=db.count_active_alerts()))


# ============================ Sistema ============================

@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    comps = []

    def _db_checks():
        out = []
        secrets = settings.default_secrets()
        for p in db.list_pipelines() + [{
            "name": "bootstrap(host)", "source_engine": "mysql",
            "source_host": settings.mysql_host, "source_port": settings.mysql_port,
            "target_engine": "postgres", "target_host": settings.pg_host,
            "target_port": settings.pg_port,
        }]:
            out.append((f"{p['name']}: source {p['source_host']}:{p['source_port']}",
                        _tcp_ok(p["source_host"], p["source_port"])))
            out.append((f"{p['name']}: target {p['target_host']}:{p['target_port']}",
                        _tcp_ok(p["target_host"], p["target_port"])))
        return out

    db_results = await anyio.to_thread.run_sync(_db_checks)

    comps.append(("Kafka Connect REST", metrics.CACHE.get("connect_up", False),
                  f"version {metrics.CACHE.get('connect_version', '?')}"))
    comps.append(("Kafka broker (kafka:19092)", _tcp_ok("kafka", 19092), ""))
    comps.append(("Prometheus", await _http_ok("prometheus", 9090, "/-/healthy"), ""))
    comps.append(("Grafana", await _http_ok("grafana", 3000, "/api/health"), ""))
    comps.append(("Alertmanager", await _http_ok("alertmanager", 9093, "/-/healthy"), ""))
    for label, ok in db_results:
        comps.append((label, ok, ""))

    plugins = []
    try:
        pls = await app.state.client.connector_plugins()
        plugins = sorted(pl.get("type", "?") + ": " + pl.get("class", "?").split(".")[-1]
                         for pl in pls)
    except Exception:
        pass
    return templates.TemplateResponse(request, "system.html", _ctx(
        request, comps=comps, plugins=plugins))


async def _http_ok(host: str, port: int, path: str) -> bool:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"http://{host}:{port}{path}")
            return r.status_code < 500
    except Exception:
        return False
