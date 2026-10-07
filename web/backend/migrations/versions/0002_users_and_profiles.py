"""Preserve legacy identities/history and add users and profile metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0002_users_and_profiles"
down_revision = "0001_restart_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename in place: UUIDs, passwords, sessions and restart authors survive.
    op.rename_table("admins", "users")
    op.alter_column("users", "login", existing_type=sa.String(64), nullable=True)
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)
    op.add_column("users", sa.Column("google_sub", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(16), nullable=False, server_default="ADMIN"))
    # Existing rows were administrators; future accounts default to USER.
    op.alter_column("users", "role", server_default="USER")
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])
    op.create_check_constraint("ck_user_role", "users", "role IN ('ADMIN', 'USER')")
    op.create_check_constraint(
        "ck_user_identity", "users",
        "(google_sub IS NOT NULL AND email IS NOT NULL AND login IS NULL AND password_hash IS NULL) OR "
        "(google_sub IS NULL AND email IS NULL AND login IS NOT NULL AND password_hash IS NOT NULL)",
    )

    op.rename_table("admin_sessions", "user_sessions")
    op.alter_column("user_sessions", "admin_id", new_column_name="user_id", existing_type=sa.Uuid())
    op.drop_index("ix_admin_sessions_admin_id", table_name="user_sessions")
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.alter_column("restart_jobs", "admin_id", new_column_name="user_id", existing_type=sa.Uuid())
    op.drop_index("ix_restart_jobs_admin_id", table_name="restart_jobs")
    op.create_index("ix_restart_jobs_user_id", "restart_jobs", ["user_id"])

    op.create_table(
        "vpn_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_name", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("mode IN ('yc-direct', 'aws-direct', 'yc-aws-multihop')", name="ck_vpn_profile_mode"),
        sa.CheckConstraint(
            "status IN ('issuing', 'active', 'expired', 'revoking', 'revoked', 'failed')",
            name="ck_vpn_profile_status",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_vpn_profile_expiry"),
    )
    op.create_index("ix_vpn_profiles_owner_id", "vpn_profiles", ["owner_id"])
    op.create_table(
        "profile_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.ForeignKeyConstraint(["profile_id"], ["vpn_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("kind IN ('issue', 'revoke')", name="ck_profile_job_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_review')",
            name="ck_profile_job_status",
        ),
    )
    op.create_index("ix_profile_jobs_profile_id", "profile_jobs", ["profile_id"])


def downgrade() -> None:
    connection = op.get_bind()
    # Old code cannot represent Google accounts/profiles or disabled users.
    # Refuse rollback rather than delete records or reactivate credentials.
    incompatible = connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM users WHERE google_sub IS NOT NULL "
        "OR role <> 'ADMIN' OR NOT is_active) "
        "OR EXISTS (SELECT 1 FROM vpn_profiles) "
        "OR EXISTS (SELECT 1 FROM profile_jobs)"
    ))
    if incompatible:
        raise RuntimeError("rollback requires an operator migration of users/profiles; no data was removed")
    op.drop_index("ix_profile_jobs_profile_id", table_name="profile_jobs")
    op.drop_table("profile_jobs")
    op.drop_index("ix_vpn_profiles_owner_id", table_name="vpn_profiles")
    op.drop_table("vpn_profiles")

    op.drop_index("ix_restart_jobs_user_id", table_name="restart_jobs")
    op.alter_column("restart_jobs", "user_id", new_column_name="admin_id", existing_type=sa.Uuid())
    op.create_index("ix_restart_jobs_admin_id", "restart_jobs", ["admin_id"])
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.alter_column("user_sessions", "user_id", new_column_name="admin_id", existing_type=sa.Uuid())
    op.create_index("ix_admin_sessions_admin_id", "user_sessions", ["admin_id"])
    op.rename_table("user_sessions", "admin_sessions")

    op.drop_constraint("ck_user_identity", "users", type_="check")
    op.drop_constraint("ck_user_role", "users", type_="check")
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    for name in ("is_active", "role", "email", "google_sub"):
        op.drop_column("users", name)
    op.alter_column("users", "login", existing_type=sa.String(64), nullable=False)
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    op.rename_table("users", "admins")
