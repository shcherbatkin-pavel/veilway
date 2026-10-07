"""Switch to Google-only sessions and reserve the administrator binding."""

from alembic import op
import sqlalchemy as sa


revision = "0003_google_sign_in"
down_revision = "0002_users_and_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_login_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("browser_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("nonce_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
        sa.UniqueConstraint("browser_hash"),
    )
    op.create_index("ix_oauth_login_attempts_expires_at", "oauth_login_attempts", ["expires_at"])
    op.create_table(
        "google_admin_binding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("id = 1", name="ck_google_admin_singleton"),
    )
    op.execute(sa.text("INSERT INTO google_admin_binding (id) VALUES (1)"))
    op.execute(sa.text("DELETE FROM user_sessions"))
    op.execute(sa.text("UPDATE users SET is_active = false WHERE google_sub IS NULL"))
    op.execute(sa.text("UPDATE users SET role = 'USER' WHERE google_sub IS NOT NULL"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT user_id IS NOT NULL FROM google_admin_binding WHERE id = 1")):
        raise RuntimeError("rollback must preserve the Google administrator binding; operator migration required")
    op.drop_table("google_admin_binding")
    op.drop_index("ix_oauth_login_attempts_expires_at", table_name="oauth_login_attempts")
    op.drop_table("oauth_login_attempts")
    # Never restore sessions or reactivate old password credentials.
