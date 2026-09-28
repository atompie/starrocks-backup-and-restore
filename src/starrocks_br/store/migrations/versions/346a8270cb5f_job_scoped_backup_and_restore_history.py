"""job-scoped backup and restore history

Revision ID: 346a8270cb5f
Revises: b7bf2d5f2365
Create Date: 2026-09-28 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '346a8270cb5f'
down_revision: str | Sequence[str] | None = 'b7bf2d5f2365'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    `backup_history`/`restore_history` change from a single best-effort
    summary row keyed by (cluster_id, label) into an append-only log of
    state-change rows keyed by job_id. Existing rows have no derivable
    job_id, so both tables are dropped and recreated rather than migrated
    (see design.md Migration Plan).
    """
    op.add_column('jobs', sa.Column('label', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('repository', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_jobs_label'), 'jobs', ['label'], unique=False)

    op.drop_table('backup_history')
    op.drop_table('restore_history')

    op.create_table(
        'backup_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_backup_history_job_id'), 'backup_history', ['job_id'], unique=False)

    op.create_table(
        'restore_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_restore_history_job_id'), 'restore_history', ['job_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema back to the pre-job-scoped summary tables."""
    op.drop_index(op.f('ix_restore_history_job_id'), table_name='restore_history')
    op.drop_table('restore_history')
    op.drop_index(op.f('ix_backup_history_job_id'), table_name='backup_history')
    op.drop_table('backup_history')

    op.create_table(
        'backup_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('backup_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('repository', sa.String(length=128), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cluster_id', 'label', name='uq_backup_history_cluster_label'),
    )
    op.create_index(op.f('ix_backup_history_cluster_id'), 'backup_history', ['cluster_id'], unique=False)

    op.create_table(
        'restore_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=128), nullable=False),
        sa.Column('backup_label', sa.String(length=255), nullable=False),
        sa.Column('restore_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('repository', sa.String(length=128), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('verification_checksum', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cluster_id', 'job_id', name='uq_restore_history_cluster_job'),
    )
    op.create_index(op.f('ix_restore_history_cluster_id'), 'restore_history', ['cluster_id'], unique=False)

    op.drop_index(op.f('ix_jobs_label'), table_name='jobs')
    op.drop_column('jobs', 'repository')
    op.drop_column('jobs', 'label')
