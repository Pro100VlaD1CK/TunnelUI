"""Durable, secret-free client deletion replay records."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(sa.Column("applied_max_http2_conns", sa.Integer()))
        batch.add_column(sa.Column("applied_max_http3_conns", sa.Integer()))
        batch.add_column(sa.Column("applied_client_enabled", sa.Boolean()))
    op.execute(
        "UPDATE attachments SET applied_max_http2_conns = max_http2_conns, "
        "applied_max_http3_conns = max_http3_conns WHERE applied_state = 'active'"
    )
    op.execute(
        "UPDATE attachments SET applied_client_enabled = "
        "(SELECT enabled FROM clients WHERE clients.id = attachments.client_id) "
        "WHERE sync_state IN ('active', 'disabled')"
    )
    op.create_table(
        "client_deletions",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_client_deletions_client_id", "client_deletions", ["client_id"])
    op.create_table(
        "attachment_intents",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("attachment_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("inbound_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "client_access_intents",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("inbound_ids_json", sa.Text(), nullable=False),
        sa.Column("attachment_revisions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("client_access_intents")
    op.drop_table("attachment_intents")
    op.drop_index("ix_client_deletions_client_id", table_name="client_deletions")
    op.drop_table("client_deletions")
    with op.batch_alter_table("attachments") as batch:
        batch.drop_column("applied_client_enabled")
        batch.drop_column("applied_max_http3_conns")
        batch.drop_column("applied_max_http2_conns")
