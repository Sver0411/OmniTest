"""Short-lived SQLite transactions, immutable run snapshots."""

import time

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .core.ir import uid


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String, index=True)
    project_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class CaseResult(Base):
    __tablename__ = "case_results"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    adapter: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    duration: Mapped[float] = mapped_column(Float, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    actor: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    method: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status_code: Mapped[int] = mapped_column()
    request_id: Mapped[str] = mapped_column(String)
    duration_ms: Mapped[float] = mapped_column(Float)


class NodeState(Base):
    __tablename__ = "node_states"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen: Mapped[float] = mapped_column(Float)
    inventory: Mapped[dict] = mapped_column(JSON, default=dict)


class NodeLease(Base):
    __tablename__ = "node_leases"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    node_id: Mapped[str] = mapped_column(String, index=True)
    claim_id: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_bytes: Mapped[int] = mapped_column(default=0)
    evidence_count: Mapped[int] = mapped_column(default=0)


class Member(Base):
    __tablename__ = "members"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    token_digest: Mapped[str] = mapped_column(String, unique=True)
    project_roles: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    rotated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(String, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    project_roles: Mapped[dict] = mapped_column(JSON, default=dict)


class TeamMember(Base):
    __tablename__ = "team_members"
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Database:
    def __init__(self, url):
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def pragmas(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
        cfg.set_main_option("sqlalchemy.url", self.engine.url.render_as_string(hide_password=False))
        command.upgrade(cfg, "head")
