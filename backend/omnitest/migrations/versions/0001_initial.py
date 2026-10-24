"""Initial resource and immutable execution tables."""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "resources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("project_id", sa.String()),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_resources_kind", "resources", ["kind"])
    op.create_index("ix_resources_project_id", "resources", ["project_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("started_at", sa.Float()),
        sa.Column("finished_at", sa.Float()),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
    )
    for field in ["project_id", "status", "created_at"]:
        op.create_index(f"ix_runs_{field}", "runs", [field])
    op.create_table(
        "case_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "run_id", sa.String(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("adapter", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    for field in ["run_id", "adapter"]:
        op.create_index(f"ix_case_results_{field}", "case_results", [field])


def downgrade():
    op.drop_table("case_results")
    op.drop_table("runs")
    op.drop_table("resources")
