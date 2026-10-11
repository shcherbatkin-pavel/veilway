"""Private VPN session accounting; no VPN materials or addresses."""
from alembic import op
import sqlalchemy as sa
import uuid

revision = "0008_business_analytics"
down_revision = "0007_heartbeat_details"
branch_labels = None
depends_on = None


def upgrade():
    uid = sa.Uuid
    dt = lambda name, **kw: sa.Column(name, sa.DateTime(timezone=True), **kw)
    fk = lambda name, target, **kw: sa.Column(name, uid(), sa.ForeignKey(target), **kw)
    op.create_table("analytics_agents", sa.Column("slug", sa.String(32), primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True), dt("last_contact_at"), dt("pruned_at"),
        sa.CheckConstraint("slug IN ('aws-direct', 'yc-direct')", name="ck_analytics_agent_slug"))
    op.create_table("analytics_sources", sa.Column("mode", sa.String(32), primary_key=True),
        sa.Column("node", sa.String(32), sa.ForeignKey("analytics_agents.slug")), dt("configured_at"), dt("first_observed_at"),
        sa.Column("epoch", uid()), dt("observed_at"), dt("received_at"),
        sa.Column("available", sa.Boolean(), nullable=False), sa.Column("connection_count", sa.Integer()),
        sa.Column("capture_errors", sa.Integer(), nullable=False),
        sa.CheckConstraint("mode IN ('aws-direct','yc-direct','yc-aws-multihop')", name="ck_analytics_source_mode"))
    for mode in ("aws-direct", "yc-direct", "yc-aws-multihop"):
        op.execute(sa.text("INSERT INTO analytics_sources (mode, available, capture_errors) VALUES (:mode, false, 0)").bindparams(mode=mode))
    op.create_table("profile_ownership", sa.Column("id", uid(), primary_key=True),
        fk("profile_id", "vpn_profiles.id", nullable=False), fk("owner_id", "users.id"), dt("assigned_at", nullable=False))
    op.create_index("ix_profile_ownership_profile_id", "profile_ownership", ["profile_id"])
    # Current ownership is known from migration time, not retroactively guessed.
    connection=op.get_bind()
    for row in connection.execute(sa.text("SELECT id, owner_id FROM vpn_profiles")):
        connection.execute(sa.text("INSERT INTO profile_ownership (id, profile_id, owner_id, assigned_at) VALUES (:id,:profile,:owner,CURRENT_TIMESTAMP)"),
            {"id":uuid.uuid4(),"profile":row.id,"owner":row.owner_id})
    op.create_table("vpn_sessions", sa.Column("id", uid(), primary_key=True),
        sa.Column("node", sa.String(32), sa.ForeignKey("analytics_agents.slug"), nullable=False),
        sa.Column("mode", sa.String(32), sa.ForeignKey("analytics_sources.mode"), nullable=False),
        sa.Column("epoch", uid(), nullable=False), sa.Column("certificate_serial", sa.String(40)),
        fk("profile_id", "vpn_profiles.id"), fk("owner_id", "users.id"), dt("started_at", nullable=False),
        dt("last_seen_at", nullable=False), dt("snapshot_at"), dt("ended_at"), sa.Column("duration_seconds", sa.BigInteger()),
        sa.Column("state", sa.String(16), nullable=False), sa.Column("recovered", sa.Boolean(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False), sa.Column("reconnect", sa.Boolean(), nullable=False), sa.Column("bytes_received", sa.BigInteger(), nullable=False),
        sa.Column("bytes_sent", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("state IN ('open','closed','interrupted')", name="ck_vpn_session_state"),
        sa.CheckConstraint("bytes_received >= 0 AND bytes_sent >= 0", name="ck_vpn_session_bytes"))
    for field in ("mode","profile_id","started_at","last_seen_at","snapshot_at"):
        op.create_index("ix_vpn_sessions_"+field,"vpn_sessions",[field])
    op.create_table("analytics_receipts", sa.Column("id",uid(),primary_key=True),
        sa.Column("node",sa.String(32),sa.ForeignKey("analytics_agents.slug"),nullable=False),
        sa.Column("payload_hash",sa.LargeBinary(32),nullable=False),dt("observed_at",nullable=False))
    op.create_index("ix_analytics_receipts_observed_at","analytics_receipts",["observed_at"])
    op.create_table("profile_usage",fk("profile_id","vpn_profiles.id",primary_key=True),
        dt("first_connected_at",nullable=False),dt("last_connected_at",nullable=False),dt("last_seen_at",nullable=False),
        *[sa.Column(name,sa.BigInteger(),nullable=False) for name in ("sessions","bytes_received","bytes_sent")])
    op.create_table("analytics_owners",fk("owner_id","users.id",primary_key=True),dt("first_connected_at",nullable=False))
    op.create_table("analytics_daily_usage",sa.Column("id",uid(),primary_key=True),sa.Column("day",sa.Date(),nullable=False),
        sa.Column("mode",sa.String(32),sa.ForeignKey("analytics_sources.mode"),nullable=False),
        sa.Column("profile_key",sa.String(36),nullable=False),sa.Column("owner_key",sa.String(36),nullable=False),
        fk("profile_id","vpn_profiles.id"),fk("owner_id","users.id"),
        *[sa.Column(name,sa.Integer(),nullable=False) for name in ("sessions_started","sessions_closed","interrupted","reconnects")],
        *[sa.Column(name,sa.BigInteger(),nullable=False) for name in ("duration_seconds","bytes_received","bytes_sent")],
        sa.UniqueConstraint("day","mode","profile_key","owner_key",name="uq_analytics_daily_dimensions"))
    op.create_index("ix_analytics_daily_usage_day","analytics_daily_usage",["day"])
    op.create_table("analytics_coverage",sa.Column("day",sa.Date(),primary_key=True),
        sa.Column("mode",sa.String(32),sa.ForeignKey("analytics_sources.mode"),primary_key=True),
        sa.Column("observations",sa.Integer(),nullable=False))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM vpn_sessions) OR EXISTS (SELECT 1 FROM analytics_daily_usage) OR EXISTS (SELECT 1 FROM analytics_agents)")):
        raise RuntimeError("business analytics requires an operator-preserving migration")
    for name in ("analytics_coverage","analytics_daily_usage","analytics_owners","profile_usage","analytics_receipts","vpn_sessions","profile_ownership","analytics_sources","analytics_agents"):
        op.drop_table(name)
