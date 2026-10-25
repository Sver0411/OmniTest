"""Store member credential digests and project grants inherited from teams."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.create_table(
        "members",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("token_digest", sa.String(), nullable=False, unique=True),
        sa.Column("project_roles", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("rotated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("project_roles", sa.JSON(), nullable=False),
    )
    op.create_table(
        "team_members",
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "member_id",
            sa.String(),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_team_members_member_id", "team_members", ["member_id"])


def downgrade():
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("members")
