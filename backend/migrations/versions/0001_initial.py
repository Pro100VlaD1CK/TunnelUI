"""Initial schema. Frozen independently of runtime models."""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("admins", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("username", sa.String(64), nullable=False, unique=True),
                    sa.Column("password_hash", sa.Text(), nullable=False))
    op.create_table("admin_sessions",
                    sa.Column("token_hash", sa.String(64), primary_key=True),
                    sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="CASCADE")),
                    sa.Column("csrf_token", sa.String(64), nullable=False),
                    sa.Column("expires_at", sa.Integer(), nullable=False))
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_table("clients",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("username", sa.String(64), nullable=False, unique=True),
                    sa.Column("display_name", sa.String(160), nullable=False),
                    sa.Column("enabled", sa.Boolean(), nullable=False),
                    sa.Column("comment", sa.Text(), nullable=False),
                    sa.Column("expires_at", sa.Integer()),
                    sa.Column("created_at", sa.Integer(), nullable=False),
                    sa.Column("updated_at", sa.Integer(), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False))
    op.create_table("inbounds",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("name", sa.String(160), nullable=False),
                    sa.Column("kind", sa.String(32), nullable=False),
                    sa.Column("registry_id", sa.String(128), unique=True),
                    sa.Column("enabled", sa.Boolean(), nullable=False),
                    sa.Column("metadata_json", sa.Text(), nullable=False),
                    sa.Column("hashes_json", sa.Text(), nullable=False))
    op.create_table("attachments",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False),
                    sa.Column("inbound_id", sa.String(36), sa.ForeignKey("inbounds.id", ondelete="RESTRICT"), nullable=False),
                    sa.Column("secret_ciphertext", sa.Text()),
                    sa.Column("max_http2_conns", sa.Integer()),
                    sa.Column("max_http3_conns", sa.Integer()),
                    sa.Column("sync_state", sa.String(32), nullable=False),
                    sa.UniqueConstraint("client_id", "inbound_id"))
    op.create_table("audit_events",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("timestamp", sa.Integer(), nullable=False),
                    sa.Column("admin", sa.String(64), nullable=False),
                    sa.Column("action", sa.String(64), nullable=False),
                    sa.Column("entity_type", sa.String(32), nullable=False),
                    sa.Column("entity_id", sa.String(64), nullable=False),
                    sa.Column("result", sa.String(16), nullable=False),
                    sa.Column("summary", sa.String(256), nullable=False))
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])


def downgrade():
    for table in ("audit_events", "attachments", "inbounds", "clients", "admin_sessions", "admins"):
        op.drop_table(table)
