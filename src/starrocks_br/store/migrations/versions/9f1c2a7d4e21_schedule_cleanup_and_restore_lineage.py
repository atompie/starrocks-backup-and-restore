"""schedule cleanup state and restore source-backup lineage

Revision ID: 9f1c2a7d4e21
Revises: 15e118a64b09
Create Date: 2026-09-29 00:00:00.000000

"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9f1c2a7d4e21'
down_revision: str | Sequence[str] | None = '3e071d3ca1ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds `Schedule.deletion_requested_at` (marks a schedule pending the async
    `schedule_cleanup` job) and `Job.source_backup_job_id` (a durable restore-to-backup
    FK, replacing repeated `params_json` parsing - see design.md "Use a durable
    restore-to-backup relationship"). `Schedule.last_run_job_id` gains `ondelete="SET
    NULL"` so deleting a schedule's own backup jobs during cleanup does not fail the
    FK; both changes need SQLite's batch-mode strategy, same as the prior migration.
    """
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_backup_job_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_jobs_source_backup_job_id'), ['source_backup_job_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_jobs_source_backup_job_id_jobs',
            'jobs',
            ['source_backup_job_id'],
            ['id'],
            ondelete='CASCADE',
        )

    naming_convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table(
        'schedules', schema=None, naming_convention=naming_convention
    ) as batch_op:
        batch_op.add_column(sa.Column('deletion_requested_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_constraint('fk_schedules_last_run_job_id_jobs', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_schedules_last_run_job_id_jobs', 'jobs', ['last_run_job_id'], ['id'], ondelete='SET NULL'
        )

    _backfill_restore_source_backup_job_id()


def _backfill_restore_source_backup_job_id() -> None:
    """Match each restore Job's `target_label` (in `params_json`) against a unique
    successful backup Job with the same label on the same cluster. A label with zero
    or more than one matching successful backup is left unmatched (design.md "backfill
    only unambiguous successful backup matches and retain unmatched history").
    """
    connection = op.get_bind()
    jobs = sa.table(
        'jobs',
        sa.column('id', sa.Integer()),
        sa.column('cluster_id', sa.Integer()),
        sa.column('job_type', sa.String()),
        sa.column('status', sa.String()),
        sa.column('label', sa.String()),
        sa.column('params_json', sa.Text()),
        sa.column('source_backup_job_id', sa.Integer()),
    )

    restore_rows = connection.execute(
        sa.select(jobs.c.id, jobs.c.cluster_id, jobs.c.params_json).where(jobs.c.job_type == 'restore')
    ).all()

    for restore_id, cluster_id, params_json in restore_rows:
        try:
            target_label = (json.loads(params_json) if params_json else {}).get('target_label')
        except (TypeError, ValueError):
            continue
        if not target_label:
            continue

        candidates = connection.execute(
            sa.select(jobs.c.id).where(
                jobs.c.cluster_id == cluster_id,
                jobs.c.label == target_label,
                jobs.c.status == 'SUCCESS',
                jobs.c.job_type.in_(['backup_full', 'backup_incremental']),
            )
        ).all()

        if len(candidates) != 1:
            continue

        connection.execute(
            sa.update(jobs).where(jobs.c.id == restore_id).values(source_backup_job_id=candidates[0][0])
        )


def downgrade() -> None:
    """Downgrade schema."""
    naming_convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table(
        'schedules', schema=None, naming_convention=naming_convention
    ) as batch_op:
        batch_op.drop_constraint('fk_schedules_last_run_job_id_jobs', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_schedules_last_run_job_id_jobs', 'jobs', ['last_run_job_id'], ['id']
        )
        batch_op.drop_column('deletion_requested_at')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_jobs_source_backup_job_id_jobs', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_jobs_source_backup_job_id'))
        batch_op.drop_column('source_backup_job_id')
