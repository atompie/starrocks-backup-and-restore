"""add job group id

Revision ID: fcb00bbb052c
Revises: d96bf30a5582
Create Date: 2026-09-27 12:46:14.425873

Adds a nullable, indexed `group_id` column to `jobs`, populated going
forward by `submit_job` for job types that carry an inventory group
(`backup_full`, `backup_incremental`). Existing rows are left `NULL`; no
backfill, since group id for prior jobs is only recoverable by parsing
`params_json`. See openspec/changes/add-backup-history-filters/design.md.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fcb00bbb052c'
down_revision: str | Sequence[str] | None = 'd96bf30a5582'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_jobs_group_id'), ['group_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_group_id'))
        batch_op.drop_column('group_id')
