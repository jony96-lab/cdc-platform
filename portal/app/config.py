import os


class Settings:
    portal_db_path: str = os.getenv("PORTAL_DB_PATH", "/data/portal.db")
    secrets_dir: str = os.getenv("SECRETS_DIR", "/opt/connect-secrets")

    connect_url: str = os.getenv("CONNECT_URL", "http://connect:8083")
    connect_metrics_url: str = os.getenv("CONNECT_METRICS_URL", "http://connect:7072/metrics")
    kafka_bootstrap: str = os.getenv("KAFKA_BOOTSTRAP", "kafka:19092")
    prom_url: str = os.getenv("PROM_URL", "http://prometheus:9090")
    alertmanager_url: str = os.getenv("ALERTMANAGER_URL", "http://alertmanager:9093")

    grafana_ext_url: str = os.getenv("GRAFANA_EXT_URL", "http://localhost:3000")
    kafbat_ext_url: str = os.getenv("KAFBAT_EXT_URL", "http://localhost:8080")
    connect_ext_url: str = os.getenv("CONNECT_EXT_URL", "http://localhost:8083")
    prom_ext_url: str = os.getenv("PROM_EXT_URL", "http://localhost:9090")
    alertmanager_ext_url: str = os.getenv("ALERTMANAGER_EXT_URL", "http://localhost:9093")

    # BDs del host (defaults para el wizard y checks de sistema)
    mysql_host: str = os.getenv("MYSQL_HOST", "host.docker.internal")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    pg_host: str = os.getenv("PG_HOST", "host.docker.internal")
    pg_port: int = int(os.getenv("PG_PORT", "5433"))
    pg_target_db: str = os.getenv("PG_TARGET_DB", "cdc_target")

    mysql_cdc_user: str = os.getenv("MYSQL_CDC_USER", "cdc_user")
    pg_cdc_role: str = os.getenv("PG_CDC_ROLE", "cdc_role")

    cdc_topic_prefix: str = os.getenv("CDC_TOPIC_PREFIX", "cdc")
    cdc_source_db: str = os.getenv("CDC_SOURCE_DB", "inventory")
    mysql_source_server_id: str = os.getenv("MYSQL_SOURCE_SERVER_ID", "5401")

    poll_interval_sec: int = int(os.getenv("PORTAL_POLL_INTERVAL_SEC", "10"))

    def default_secrets(self) -> dict:
        """Lee secrets/default.properties (credenciales del pipeline bootstrap)."""
        return read_properties(os.path.join(self.secrets_dir, "default.properties"))


def read_properties(path: str) -> dict:
    props = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return props


settings = Settings()
