from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from omnitest.db import AuditEvent, Database, Resource


def test_upgrade_existing_database_preserves_projects_and_adds_audit(tmp_path):
    url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "omnitest/migrations")
    )
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0001")
    db = Database(url)
    with db.session.begin() as session:
        session.add(Resource(id="existing", kind="projects", body={"name": "Preserved"}))
    assert "audit_events" not in inspect(db.engine).get_table_names()
    db.migrate()
    db.migrate()
    with db.session.begin() as session:
        assert session.get(Resource, "existing").body["name"] == "Preserved"
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
        session.add(
            AuditEvent(
                actor="admin",
                role="admin",
                method="POST",
                path="/api/runs",
                project_id="existing",
                status_code=202,
                request_id="request",
                duration_ms=1,
            )
        )
    db.engine.dispose()
