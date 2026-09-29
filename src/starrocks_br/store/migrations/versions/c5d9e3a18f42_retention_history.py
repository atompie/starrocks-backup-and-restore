"""retention history

Revision ID: c5d9e3a18f42
Revises: a4c8e2f17b30
Create Date: 2026-09-29 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5d9e3a18f42'
down_revision: str | Sequence[str] | None = 'a4c8e2f17b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: add the append-only `retention_history` log."""
    op.create_table(
        'retention_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('retention_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_retention_history_job_id'), ['job_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('retention_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_retention_history_job_id'))

    op.drop_table('retention_history')
