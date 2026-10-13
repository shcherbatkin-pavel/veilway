"""Scoped Android sessions and one-use device authorization."""
from alembic import op
import sqlalchemy as sa

revision = "0009_mobile_client"
down_revision = "0008_business_analytics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_device_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("code_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("poll_seconds", sa.Integer(), nullable=False))
    op.create_index("ix_mobile_device_attempts_expires_at", "mobile_device_attempts", ["expires_at"])
    op.create_table("mobile_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_mobile_sessions_user_id", "mobile_sessions", ["user_id"])
    op.create_index("ix_mobile_sessions_expires_at", "mobile_sessions", ["expires_at"])
    op.create_table("mobile_rate_buckets", sa.Column("key", sa.String(96), primary_key=True),
        sa.Column("window", sa.Integer(), nullable=False), sa.Column("count", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("mobile_rate_buckets")
    op.drop_table("mobile_sessions")
    op.drop_table("mobile_device_attempts")
