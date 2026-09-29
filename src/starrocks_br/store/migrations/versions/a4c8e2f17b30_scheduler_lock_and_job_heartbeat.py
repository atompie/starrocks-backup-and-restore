"""scheduler lock and job heartbeat

Revision ID: a4c8e2f17b30
Revises: 9f1c2a7d4e21
Create Date: 2026-09-29 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4c8e2f17b30'
down_revision: str | Sequence[str] | None = '9f1c2a7d4e21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the singleton `scheduler_lock` table (seeded with its one row, `id = 1`, so lock
    acquisition is always an UPDATE, never an INSERT race) and the nullable `jobs.heartbeat_at`
    column used to tell live jobs from jobs whose owning process died.
    """
    op.create_table(
        'scheduler_lock',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('holder', sa.String(length=255), nullable=True),
        sa.Column('acquired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_tick_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute("INSERT INTO scheduler_lock (id) VALUES (1)")

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('heartbeat_at')

    op.drop_table('scheduler_lock')
