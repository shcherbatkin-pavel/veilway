"""Authenticated CRL distribution and durable node acknowledgements."""
from alembic import op
import sqlalchemy as sa
revision = "0005_crl_delivery"
down_revision = "0004_profile_api"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("crl_publications",
        sa.Column("version", sa.BigInteger(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("ca_sha256", sa.String(64), nullable=False),
        sa.Column("pem", sa.LargeBinary(), nullable=False),
        sa.Column("this_update", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_update", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("crl_agents",
        sa.Column("slug", sa.String(32), primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_version", sa.BigInteger()),
        sa.Column("acknowledged_sha256", sa.String(64)),
        sa.Column("acknowledged_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(32)),
        sa.CheckConstraint("slug IN ('aws-direct', 'yc-direct')", name="ck_crl_agent_slug"))
    op.create_table("crl_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(32)),
        sa.CheckConstraint("id = 1", name="ck_crl_sync_singleton"))
    op.execute(sa.text("INSERT INTO crl_sync_state (id) VALUES (1)"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM crl_publications) OR EXISTS (SELECT 1 FROM crl_agents)")):
        raise RuntimeError("CRL versions and receipts require an operator-preserving migration")
    op.drop_table("crl_sync_state")
    op.drop_table("crl_agents")
    op.drop_table("crl_publications")
