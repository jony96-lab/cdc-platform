"""Chequeos de pre-vuelo para pipelines CDC.

Cada check devuelve: {name, ok, detail, fix}
"""
import socket

import pymysql


def tcp_check(host: str, port: int, timeout: float = 4.0) -> dict:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"name": f"TCP {host}:{port}", "ok": True,
                    "detail": "Alcanzable desde el contenedor", "fix": ""}
    except Exception as e:
        return {
            "name": f"TCP {host}:{port}",
            "ok": False,
            "detail": f"No alcanzable: {e}",
            "fix": ("Verifica que el servicio este corriendo, que escuche en 0.0.0.0, "
                    "y que el firewall de Windows permita el trafico desde la subred "
                    "WSL/Docker (172.16.0.0/12 y 192.168.0.0/16)."),
        }


def mysql_source_checks(host: str, port: int, user: str, password: str,
                        database: str) -> list[dict]:
    results = [tcp_check(host, port)]
    if not results[0]["ok"]:
        return results
    try:
        conn = pymysql.connect(host=host, port=int(port), user=user, password=password,
                               connect_timeout=5, cursorclass=pymysql.cursors.DictCursor)
    except Exception as e:
        results.append({"name": "Autenticacion MySQL", "ok": False,
                        "detail": str(e)[:200],
                        "fix": "Revisa usuario/password y que el usuario exista como 'user'@'%'."})
        return results

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT @@log_bin AS lb, @@binlog_format AS fmt, "
                        "@@binlog_row_image AS img, @@server_id AS sid, @@gtid_mode AS gtid")
            row = cur.fetchone()
        lb_ok = row["lb"] == 1
        results.append({"name": "Binlog habilitado (log_bin)", "ok": lb_ok,
                        "detail": f"log_bin={row['lb']}",
                        "fix": "Agrega log_bin=mysql-bin en [mysqld] y reinicia el servicio."})
        fmt_ok = str(row["fmt"]).upper() == "ROW"
        results.append({"name": "binlog_format=ROW", "ok": fmt_ok,
                        "detail": f"binlog_format={row['fmt']}",
                        "fix": "Configura binlog_format=ROW en my.ini y reinicia."})
        img_ok = str(row["img"]).upper() == "FULL"
        results.append({"name": "binlog_row_image=FULL", "ok": img_ok,
                        "detail": f"binlog_row_image={row['img']}",
                        "fix": "Configura binlog_row_image=FULL (UPDATE/DELETE necesitan el before completo)."})
        results.append({"name": "GTID (opcional)", "ok": str(row["gtid"]).upper() == "ON",
                        "detail": f"gtid_mode={row['gtid']} (sin GTID los snapshots incrementales requieren LOCK TABLES)",
                        "fix": "Opcional: habilita gtid_mode=ON + enforce_gtid_consistency=ON."})

        with conn.cursor() as cur:
            cur.execute("SHOW GRANTS")
            grants = " | ".join(str(list(g.values())[0]) for g in cur.fetchall()).upper()
        needed = ["REPLICATION SLAVE", "REPLICATION CLIENT", "SELECT"]
        missing = [g for g in needed if g not in grants]
        results.append({"name": "Privilegios de replicacion", "ok": not missing,
                        "detail": f"faltan: {', '.join(missing)}" if missing else "OK",
                        "fix": "GRANT SELECT, SHOW DATABASES, RELOAD, SHOW VIEW, REPLICATION SLAVE, "
                               "REPLICATION CLIENT, LOCK TABLES, PROCESS ON *.* TO 'usuario'@'%'"})

        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            dbs = [list(d.values())[0] for d in cur.fetchall()]
        db_ok = database in dbs
        results.append({"name": f"Base de datos '{database}' existe", "ok": db_ok,
                        "detail": f"disponibles: {', '.join(dbs[:10])}",
                        "fix": f"Crea la base '{database}' o corrige el nombre."})
    finally:
        conn.close()
    return results


def postgres_source_checks(host: str, port: int, user: str, password: str,
                           database: str) -> list[dict]:
    import psycopg

    results = [tcp_check(host, port)]
    if not results[0]["ok"]:
        return results
    try:
        conn = psycopg.connect(host=host, port=int(port), user=user, password=password,
                               dbname=database, connect_timeout=5)
    except Exception as e:
        results.append({"name": "Autenticacion PostgreSQL", "ok": False,
                        "detail": str(e)[:200],
                        "fix": "Revisa pg_hba.conf: necesita 'host all <rol> 172.16.0.0/12 scram-sha-256' "
                               "y 'host replication <rol> 172.16.0.0/12 scram-sha-256' + reload."})
        return results
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW wal_level")
            wl = cur.fetchone()[0]
        results.append({"name": "wal_level=logical", "ok": wl == "logical",
                        "detail": f"wal_level={wl}",
                        "fix": "Configura wal_level=logical en postgresql.conf y reinicia el servicio."})
        with conn.cursor() as cur:
            cur.execute("SELECT rolreplication FROM pg_roles WHERE rolname=current_user")
            row = cur.fetchone()
        rep_ok = bool(row and row[0])
        results.append({"name": "Rol con REPLICATION", "ok": rep_ok,
                        "detail": f"rolreplication={row[0] if row else '?'}",
                        "fix": "ALTER ROLE <rol> REPLICATION LOGIN;"})
    finally:
        conn.close()
    return results


def postgres_target_checks(host: str, port: int, user: str, password: str,
                           database: str) -> list[dict]:
    import psycopg

    results = [tcp_check(host, port)]
    if not results[0]["ok"]:
        return results
    try:
        conn = psycopg.connect(host=host, port=int(port), user=user, password=password,
                               dbname=database, connect_timeout=5)
    except Exception as e:
        results.append({"name": "Autenticacion PostgreSQL (destino)", "ok": False,
                        "detail": str(e)[:200],
                        "fix": "Verifica rol/password y pg_hba.conf para la subred Docker."})
        return results
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")
            can_create = cur.fetchone()[0]
        results.append({"name": "Permiso CREATE en BD destino", "ok": can_create,
                        "detail": "el rol puede crear tablas (schema.evolution=basic)",
                        "fix": "ALTER DATABASE <db> OWNER TO <rol>; o GRANT CREATE ON SCHEMA public TO <rol>;"})
        with conn.cursor() as cur:
            cur.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            schema_ok = cur.fetchone()[0]
        results.append({"name": "Permiso CREATE en schema public", "ok": schema_ok,
                        "detail": "necesario para auto-crear tablas destino",
                        "fix": "GRANT CREATE ON SCHEMA public TO <rol>; (PG15+ revoco este permiso por defecto)"})
    finally:
        conn.close()
    return results


def mysql_target_checks(host: str, port: int, user: str, password: str,
                        database: str) -> list[dict]:
    results = [tcp_check(host, port)]
    if not results[0]["ok"]:
        return results
    try:
        conn = pymysql.connect(host=host, port=int(port), user=user, password=password,
                               connect_timeout=5)
    except Exception as e:
        results.append({"name": "Autenticacion MySQL (destino)", "ok": False,
                        "detail": str(e)[:200], "fix": "Revisa usuario/password."})
        return results
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            dbs = [list(d.values())[0] for d in cur.fetchall()]
        ok = database in dbs
        results.append({"name": f"Base destino '{database}' existe", "ok": ok,
                        "detail": f"disponibles: {', '.join(dbs[:10])}",
                        "fix": f"CREATE DATABASE {database};"})
    finally:
        conn.close()
    return results


def sqlserver_source_checks(host: str, port: int, user: str, password: str,
                            database: str) -> list[dict]:
    """SQL Server como fuente: requiere CDC habilitado (BD + tablas) y SQL Agent corriendo."""
    import pymssql

    results = [tcp_check(host, port)]
    if not results[0]["ok"]:
        return results
    try:
        conn = pymssql.connect(server=host, port=str(port), user=user, password=password,
                               database=database, login_timeout=5)
    except Exception as e:
        results.append({"name": "Autenticacion SQL Server", "ok": False,
                        "detail": str(e)[:200],
                        "fix": "Verifica login/password. El usuario Debezium debe ser sysadmin, "
                               "o db_owner + db_datareader + VIEW SERVER STATE."})
        return results
    try:
        with conn.cursor(as_dict=False) as cur:
            cur.execute("SELECT is_cdc_enabled FROM sys.databases WHERE name=%s", (database,))
            row = cur.fetchone()
        cdc_db_ok = bool(row and row[0])
        results.append({"name": f"CDC habilitado en BD '{database}'", "ok": cdc_db_ok,
                        "detail": f"is_cdc_enabled={row[0] if row else '?'}",
                        "fix": f"USE [{database}]; EXEC sys.sp_cdc_enable_db; (requiere rol sysadmin)"})
        if cdc_db_ok:
            with conn.cursor(as_dict=False) as cur:
                cur.execute("SELECT COUNT(*) FROM sys.tables WHERE is_tracked_by_cdc=1")
                n = cur.fetchone()[0]
            results.append({"name": "Tablas bajo CDC (capture instances)", "ok": n > 0,
                            "detail": f"{n} tabla(s) con is_tracked_by_cdc=1",
                            "fix": "EXEC sys.sp_cdc_enable_table @source_schema='dbo', "
                                   "@source_name='<tabla>', @role_name=NULL;"})
        results.append({"name": "SQL Server Agent (capture job)", "ok": None,
                        "detail": "verifica que el servicio 'SQL Server Agent' esté corriendo: "
                                  "sin él los capture jobs no leen el log",
                        "fix": "Start-Service SQLSERVERAGENT (o verificá en SSMS)"})
    finally:
        conn.close()
    return results


def sqlserver_target_checks(host: str, port: int, user: str, password: str,
                            database: str) -> list[dict]:
    """SQL Server como destino: el rol necesita poder crear tablas (schema.evolution=basic)."""
    import pymssql

    results = [tcp_check(host, port)]
    if not results[0]["ok"]:
        return results
    try:
        conn = pymssql.connect(server=host, port=str(port), user=user, password=password,
                               database=database, login_timeout=5)
    except Exception as e:
        results.append({"name": "Autenticacion SQL Server (destino)", "ok": False,
                        "detail": str(e)[:200],
                        "fix": "Verifica login/password y que la BD exista."})
        return results
    try:
        with conn.cursor(as_dict=False) as cur:
            cur.execute("SELECT HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE TABLE')")
            can = bool(cur.fetchone()[0])
        results.append({"name": "Permiso CREATE TABLE en BD destino", "ok": can,
                        "detail": "necesario para auto-crear tablas (schema.evolution=basic)",
                        "fix": "ALTER ROLE db_owner ADD MEMBER [<usuario>]; o GRANT CREATE TABLE TO <usuario>;"})
    finally:
        conn.close()
    return results


def db_checks(engine: str, role: str, host: str, port: int, user: str,
              password: str, database: str) -> list[dict]:
    if engine == "mysql":
        fn = mysql_source_checks if role == "source" else mysql_target_checks
    elif engine == "postgres":
        fn = postgres_source_checks if role == "source" else postgres_target_checks
    elif engine == "sqlserver":
        fn = sqlserver_source_checks if role == "source" else sqlserver_target_checks
    else:
        return [{"name": "Motor", "ok": False, "detail": f"motor desconocido: {engine}", "fix": ""}]
    return fn(host, port, user, password, database)
