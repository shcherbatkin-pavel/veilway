"""Durable PKI job claims and secret-free profile action journal."""
from alembic import op
import sqlalchemy as sa

revision = "0004_profile_api"
down_revision = "0003_google_sign_in"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vpn_profiles", sa.Column("certificate_serial", sa.String(40), nullable=True))
    op.add_column("vpn_profiles", sa.Column("certificate_sha256", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_profile_job_kind", "profile_jobs", ["profile_id", "kind"])
    for column in (
        sa.Column("request_hash", sa.LargeBinary(32), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("crl_number", sa.BigInteger(), nullable=True),
    ):
        op.add_column("profile_jobs", column)
    # Pre-worker metadata has no live leases; preserve keys/history and resume it.
    op.execute(sa.text("UPDATE profile_jobs SET status = 'queued' WHERE status = 'running'"))
    op.create_table("profile_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("action IN ('create', 'rename', 'assign', 'download', 'revoke', 'issue_result', 'revoke_result')", name="ck_profile_audit_action"),
        sa.CheckConstraint("result IN ('accepted', 'succeeded', 'denied', 'unavailable', 'failed', 'needs_review', 'local_revocation_applied')", name="ck_profile_audit_result"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM profile_audit_events) OR EXISTS (SELECT 1 FROM profile_jobs WHERE claim_token IS NOT NULL OR request_hash IS NOT NULL OR crl_number IS NOT NULL) OR EXISTS (SELECT 1 FROM vpn_profiles WHERE certificate_serial IS NOT NULL OR certificate_sha256 IS NOT NULL)")):
        raise RuntimeError("profile jobs and audit history require an operator-preserving migration")
    op.drop_table("profile_audit_events")
    op.drop_constraint("uq_profile_job_kind", "profile_jobs", type_="unique")
    op.drop_column("vpn_profiles", "certificate_sha256")
    op.drop_column("vpn_profiles", "certificate_serial")
    for name in ("crl_number", "attempts", "retry_at", "lease_until", "claim_token", "request_hash"):
        op.drop_column("profile_jobs", name)
