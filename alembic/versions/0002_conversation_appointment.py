# Alembic revision for the dedicated AI Postgres: 0002 conversation appointment.
# Applies only to the AI database, never to CGS clinic tables.
from alembic import op
import sqlalchemy as sa

revision = "0002_conversation_appointment"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("last_appointment_id", sa.String(length=36), nullable=True))
    op.add_column("conversations", sa.Column("last_appointment_date", sa.String(length=10), nullable=True))
    op.add_column("conversations", sa.Column("awaiting_rebook", sa.Boolean(), nullable=True))
    op.add_column("conversations", sa.Column("rebook_asked_date", sa.String(length=10), nullable=True))


def downgrade():
    op.drop_column("conversations", "rebook_asked_date")
    op.drop_column("conversations", "awaiting_rebook")
    op.drop_column("conversations", "last_appointment_date")
    op.drop_column("conversations", "last_appointment_id")
