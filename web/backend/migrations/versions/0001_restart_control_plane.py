"""Create the minimal restart control-plane schema."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_restart_control_plane"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login"),
    )
    op.create_table(
        "vpn_vms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("restart_order", sa.Integer(), nullable=False),
        sa.Column("heartbeat_token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(slug = 'aws-direct' AND provider = 'aws' AND restart_order = 1) OR "
            "(slug = 'yc-direct' AND provider = 'yandex' AND restart_order = 2)",
            name="ck_vpn_vm_exact_allowlist",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "instance_id", name="uq_vpn_vm_provider_instance"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("csrf_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_admin_sessions_admin_id", "admin_sessions", ["admin_id"])
    op.create_table(
        "restart_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active_guard", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "active_guard IS NULL OR active_guard = 1",
            name="ck_restart_job_active_guard",
        ),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restart_jobs_admin_id", "restart_jobs", ["admin_id"])
    op.create_index(
        "uq_restart_jobs_one_active",
        "restart_jobs",
        ["active_guard"],
        unique=True,
        postgresql_where=sa.text("active_guard IS NOT NULL"),
    )
    op.create_table(
        "vm_heartbeats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vm_id", sa.Uuid(), nullable=False),
        sa.Column("boot_id", sa.String(length=36), nullable=False),
        sa.Column("healthy", sa.Boolean(), nullable=False),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vm_id"], ["vpn_vms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vm_id"),
    )
    op.create_table(
        "restart_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("vm_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("previous_boot_id", sa.String(length=36), nullable=False),
        sa.Column("cloud_request_id", sa.String(length=160), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["restart_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vm_id"], ["vpn_vms.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "vm_id", name="uq_restart_target_job_vm"),
    )
    op.create_index("ix_restart_targets_job_id", "restart_targets", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_restart_targets_job_id", table_name="restart_targets")
    op.drop_table("restart_targets")
    op.drop_table("vm_heartbeats")
    op.drop_index("uq_restart_jobs_one_active", table_name="restart_jobs")
    op.drop_index("ix_restart_jobs_admin_id", table_name="restart_jobs")
    op.drop_table("restart_jobs")
    op.drop_index("ix_admin_sessions_admin_id", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("vpn_vms")
    op.drop_table("admins")
