"""Track confirmed credentials and opt-in expiry execution."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("expiry_managed", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(sa.Column("applied_secret_ciphertext", sa.Text()))
        batch.add_column(sa.Column("applied_username", sa.String(64)))
        batch.add_column(sa.Column("applied_expires_at", sa.Integer()))
        batch.add_column(sa.Column("applied_expiry_managed", sa.Boolean()))
        batch.add_column(sa.Column("applied_expired", sa.Boolean()))
    op.execute(
        "UPDATE attachments SET applied_secret_ciphertext = secret_ciphertext, "
        "applied_username = (SELECT username FROM clients WHERE clients.id = attachments.client_id), "
        "applied_expires_at = (SELECT expires_at FROM clients WHERE clients.id = attachments.client_id), "
        "applied_expiry_managed = 0, applied_expired = 0 "
        "WHERE sync_state IN ('active', 'disabled')"
    )
    op.create_table(
        "credential_intents",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("inbound_ids_json", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "expiry_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO expiry_settings (id, enabled, updated_at) VALUES (1, 0, 0)")
    op.create_table(
        "expiry_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("inbound_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.Integer()),
        sa.Column("operation_id", sa.String(36)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_expiry_attempts_client_id", "expiry_attempts", ["client_id"])
    op.create_index("ix_expiry_attempts_inbound_id", "expiry_attempts", ["inbound_id"])


def downgrade():
    op.drop_index("ix_expiry_attempts_inbound_id", table_name="expiry_attempts")
    op.drop_index("ix_expiry_attempts_client_id", table_name="expiry_attempts")
    op.drop_table("expiry_attempts")
    op.drop_table("expiry_settings")
    op.drop_table("credential_intents")
    with op.batch_alter_table("attachments") as batch:
        batch.drop_column("applied_expiry_managed")
        batch.drop_column("applied_expired")
        batch.drop_column("applied_expires_at")
        batch.drop_column("applied_username")
        batch.drop_column("applied_secret_ciphertext")
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("expiry_managed")
