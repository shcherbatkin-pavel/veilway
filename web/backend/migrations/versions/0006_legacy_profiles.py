"""Original profile provenance and operator import audit; no private material."""
from alembic import op
import sqlalchemy as sa

revision = "0006_legacy_profiles"
down_revision = "0005_crl_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vpn_profiles", sa.Column("legacy_import_sha256", sa.String(64), nullable=True))
    op.drop_constraint("ck_profile_audit_action", "profile_audit_events", type_="check")
    op.create_check_constraint("ck_profile_audit_action", "profile_audit_events",
        "action IN ('create', 'rename', 'assign', 'download', 'revoke', 'issue_result', 'revoke_result', 'import')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM vpn_profiles WHERE legacy_import_sha256 IS NOT NULL) OR EXISTS (SELECT 1 FROM profile_audit_events WHERE action = 'import')")):
        raise RuntimeError("legacy profiles require an operator-preserving migration")
    op.drop_constraint("ck_profile_audit_action", "profile_audit_events", type_="check")
    op.create_check_constraint("ck_profile_audit_action", "profile_audit_events",
        "action IN ('create', 'rename', 'assign', 'download', 'revoke', 'issue_result', 'revoke_result')")
    op.drop_column("vpn_profiles", "legacy_import_sha256")
