"""Persist remote node presence and fenced run leases."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.create_table(
        "node_states",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("inventory", sa.JSON(), nullable=False),
    )
    op.create_table(
        "node_leases",
        sa.Column(
            "run_id", sa.String(), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("claim_id", sa.String(), nullable=True),
        sa.Column("heartbeat_at", sa.Float(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("evidence_bytes", sa.Integer(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_node_leases_node_id", "node_leases", ["node_id"])


def downgrade():
    op.drop_table("node_leases")
    op.drop_table("node_states")
