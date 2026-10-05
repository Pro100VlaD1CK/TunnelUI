"""Durable operations and desired/effective attachment state."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inbounds") as batch:
        batch.add_column(sa.Column("public_address", sa.String(255)))
        batch.add_column(sa.Column("config_state", sa.String(32), nullable=False,
                                   server_default="synced"))
        batch.add_column(sa.Column("created_at", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("updated_at", sa.Integer(), nullable=False,
                                   server_default="0"))
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        batch.add_column(sa.Column("desired_state", sa.String(32), nullable=False,
                                   server_default="active"))
        batch.add_column(sa.Column("applied_state", sa.String(32), nullable=False,
                                   server_default="pending"))
        batch.add_column(sa.Column("created_at", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("updated_at", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False,
                                   server_default="1"))
    op.create_table(
        "operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("inbound_id", sa.String(36),
                  sa.ForeignKey("inbounds.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("expected_hashes_json", sa.Text(), nullable=False),
        sa.Column("result_hashes_json", sa.Text(), nullable=False),
        sa.Column("backup_id", sa.String(36)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer()),
    )
    op.create_index("ix_operations_inbound_id", "operations", ["inbound_id"])
    op.create_index("ix_operations_state", "operations", ["state"])


def downgrade():
    op.drop_index("ix_operations_state", table_name="operations")
    op.drop_index("ix_operations_inbound_id", table_name="operations")
    op.drop_table("operations")
    with op.batch_alter_table("attachments") as batch:
        for name in ("revision", "updated_at", "created_at", "applied_state",
                     "desired_state", "enabled"):
            batch.drop_column(name)
    with op.batch_alter_table("inbounds") as batch:
        for name in ("updated_at", "created_at", "config_state", "public_address"):
            batch.drop_column(name)
