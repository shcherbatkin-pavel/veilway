"""Keep only the latest heartbeat component states."""
from alembic import op
import sqlalchemy as sa

revision = "0007_heartbeat_details"
down_revision = "0006_legacy_profiles"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crl_agents", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vm_heartbeats", sa.Column("containers", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    op.drop_column("vm_heartbeats", "containers")
    op.drop_column("crl_agents", "acknowledged_at")
