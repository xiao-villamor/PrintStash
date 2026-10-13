"""Generate immutable system database snapshots with DBOS 2.31.1."""

import importlib.metadata
import sqlite3
import tempfile
from pathlib import Path

from dbos import DBOS, SetWorkflowID
from testcontainers.community.postgres import PostgresContainer

assert importlib.metadata.version("dbos") == "2.31.1"
output = Path("tests/fixtures/dbos-2.31.1")
output.mkdir(parents=True, exist_ok=True)


def seed(url, schema=None):
    config = {
        "name": "printstash",
        "system_database_url": url,
        "application_version": "contract-sdk-upgrade",
        "executor_id": "legacy-executor",
        "run_admin_server": False,
        "log_level": "ERROR",
    }
    if schema:
        config["dbos_system_schema"] = schema
    DBOS(config=config)

    @DBOS.workflow(name="printstash.job")
    def job_workflow(job_id, attempt):
        return {"job_id": job_id, "attempt": attempt}

    DBOS.listen_queues([])
    DBOS.launch()
    with SetWorkflowID("dbos-231-result:1"):
        DBOS.start_workflow(job_workflow, "dbos-231-result", 1).get_result()
    queue = DBOS.register_queue(
        "derive.light", worker_concurrency=4, priority_enabled=True
    )
    with SetWorkflowID("dbos-231-job:1"):
        queue.enqueue(job_workflow, "dbos-231-job", 1)
    assert DBOS.get_workflow_status("dbos-231-job:1").status == "ENQUEUED"
    DBOS.destroy(destroy_registry=True)


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "engine.sqlite"
    seed(f"sqlite:///{path}")
    with sqlite3.connect(path) as connection:
        output.joinpath("sqlite.sql").write_text(
            "\n".join(connection.iterdump()) + "\n"
        )

with PostgresContainer(
    "postgres:16-alpine",
    username="printstash",
    password="printstash",
    dbname="printstash",
) as container:
    url = container.get_connection_url(driver=None).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    seed(url, "dbos")
    result = container.exec(
        "pg_dump -U printstash -d printstash --schema=dbos --no-owner --no-acl --inserts"
    )
    assert result.exit_code == 0, result.output
    sql = "\n".join(
        line
        for line in result.output.decode().splitlines()
        if not line.startswith("\\")
    )
    output.joinpath("postgres.sql").write_text(sql + "\n")
