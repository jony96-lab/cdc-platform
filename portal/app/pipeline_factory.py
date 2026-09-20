"""Construccion y despliegue de pipelines CDC (par source + sink)."""
import asyncio
import logging
import os
import re

from .config import settings
from .connect_client import ConnectClient, ConnectError

log = logging.getLogger("portal.factory")

SOURCE_CLASSES = {
    "mysql": "io.debezium.connector.mysql.MySqlConnector",
    "postgres": "io.debezium.connector.postgresql.PostgresConnector",
    "sqlserver": "io.debezium.connector.sqlserver.SqlServerConnector",
}
SINK_CLASS = "io.debezium.connector.jdbc.JdbcSinkConnector"

JDBC_URLS = {
    "postgres": "jdbc:postgresql://{host}:{port}/{db}",
    "mysql": "jdbc:mysql://{host}:{port}/{db}",
    "sqlserver": "jdbc:sqlserver://{host}:{port};databaseName={db}",
}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:40] or "pipeline"


def build_source_config(p: dict, secrets_file: str) -> dict:
    engine = p["source_engine"]
    prefix = p["topic_prefix"]
    common = {
        "connector.class": SOURCE_CLASSES[engine],
        "tasks.max": "1",
        "topic.prefix": prefix,
        "database.hostname": p["source_host"],
        "database.port": str(p["source_port"]),
        "database.user": p["source_user"],
        "database.password": "${file:/opt/connect-secrets/" + secrets_file + ":source_password}",
        "database.dbname": p["source_db"] if engine == "postgres" else "",
        "snapshot.mode": "initial",
        "heartbeat.interval.ms": "10000",
        "include.schema.changes": "true",
    }
    if engine == "postgres":
        common["plugin.name"] = "pgoutput"
        common["slot.name"] = f"debezium_{p['slug']}"
        common["publication.name"] = f"cdc_pub_{p['slug']}"
        common["publication.autocreate.mode"] = "filtered"
        common.pop("database.dbname", None)
        common["database.dbname"] = p["source_db"]
        if p.get("source_tables"):
            tables = [t.strip() for t in p["source_tables"].split(",") if t.strip()]
            common["table.include.list"] = ",".join(tables)
    else:  # mysql
        common.pop("database.dbname")
        common["database.server.id"] = str(p.get("server_id") or settings.mysql_source_server_id)
        common["database.include.list"] = p["source_db"]
        if p.get("source_tables"):
            tables = [t.strip() for t in p["source_tables"].split(",") if t.strip()]
            common["table.include.list"] = ",".join(
                t if "." in t else f"{p['source_db']}.{t}" for t in tables
            )
        common["schema.history.internal.kafka.bootstrap.servers"] = settings.kafka_bootstrap
        common["schema.history.internal.kafka.topic"] = f"_schema_history_{p['slug']}"
    return {k: v for k, v in common.items() if v not in ("", None)}


def build_sink_config(p: dict, secrets_file: str) -> dict:
    engine = p["target_engine"]
    prefix = p["topic_prefix"]
    cfg = {
        "connector.class": SINK_CLASS,
        "tasks.max": "1",
        "topics.regex": re.escape(prefix) + r"\..*",
        "connection.url": JDBC_URLS[engine].format(
            host=p["target_host"], port=p["target_port"], db=p["target_db"]),
        "connection.username": p["target_user"],
        "connection.password": "${file:/opt/connect-secrets/" + secrets_file + ":target_password}",
        "insert.mode": "upsert",
        "delete.enabled": "true",
        "primary.key.mode": "record_key",
        "schema.evolution": "basic",
        "errors.tolerance": "all",
        "errors.log.enable": "true",
        "errors.deadletterqueue.topic.name": f"_dlq_{p['slug']}_sink",
        "errors.deadletterqueue.topic.replication.factor": "1",
        "errors.deadletterqueue.context.headers.enable": "true",
    }
    return cfg


def write_secrets_file(slug: str, source_password: str, target_password: str) -> str:
    fname = f"pipeline_{slug}.properties"
    path = os.path.join(settings.secrets_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"source_password={source_password}\n")
        f.write(f"target_password={target_password}\n")
    os.chmod(path, 0o600)
    return fname


async def deploy_pipeline(client: ConnectClient, p: dict,
                          source_password: str, target_password: str) -> dict:
    """Escribe secrets, crea source+sink y espera RUNNING. Rollback si falla."""
    secrets_file = write_secrets_file(p["slug"], source_password, target_password)
    source_cfg = build_source_config(p, secrets_file)
    sink_cfg = build_sink_config(p, secrets_file)

    src_name, snk_name = p["source_connector"], p["sink_connector"]
    created = []
    try:
        await client.create_or_update(src_name, source_cfg)
        created.append(src_name)
        st = await client.wait_for_status(src_name, "RUNNING", timeout=90)
        if st.get("connector", {}).get("state") != "RUNNING" or any(
                t.get("state") == "FAILED" for t in st.get("tasks", [])):
            raise ConnectError(f"source no llego a RUNNING: {st}")

        await client.create_or_update(snk_name, sink_cfg)
        created.append(snk_name)
        st = await client.wait_for_status(snk_name, "RUNNING", timeout=90)
        if st.get("connector", {}).get("state") != "RUNNING" or any(
                t.get("state") == "FAILED" for t in st.get("tasks", [])):
            raise ConnectError(f"sink no llego a RUNNING: {st}")
    except Exception:
        for name in created:
            try:
                await client.delete(name)
            except Exception:
                log.exception("rollback: no se pudo borrar %s", name)
        raise

    # Espera extra a que el sink cree/consuma
    await asyncio.sleep(2)
    return {"source": src_name, "sink": snk_name, "secrets_file": secrets_file}
