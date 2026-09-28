"""schedule retention expiry and job linkage

Revision ID: 15e118a64b09
Revises: 346a8270cb5f
Create Date: 2026-09-28 09:05:53.019126

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15e118a64b09'
down_revision: Union[str, Sequence[str], None] = '346a8270cb5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    `Job.schedule_id`/`baseline_job_id` and `Schedule.retention`/`expire_after_days`/nullable
    `cadence`/`next_run_at` all need SQLite's batch-mode (copy-and-move) strategy, since SQLite
    cannot ALTER an existing column's nullability or ADD a foreign-key constraint in place (see
    design.md's Migration Plan). Both FKs use `ondelete="SET NULL"`, not `CASCADE`: a Job is a
    permanent historical record regardless of what happens to the schedule/baseline that produced
    it (SPEC.md §13-14), and a one-shot schedule's DELETE must keep succeeding even though its one
    job always references it.
    """
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('schedule_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('baseline_job_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_jobs_schedule_id'), ['schedule_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_baseline_job_id'), ['baseline_job_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_jobs_schedule_id_schedules', 'schedules', ['schedule_id'], ['id'], ondelete='SET NULL'
        )
        batch_op.create_foreign_key(
            'fk_jobs_baseline_job_id_jobs', 'jobs', ['baseline_job_id'], ['id'], ondelete='SET NULL'
        )

    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('retention', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('expire_after_days', sa.Integer(), nullable=True))
        batch_op.alter_column('cadence', existing_type=sa.VARCHAR(length=128), nullable=True)
        batch_op.alter_column('next_run_at', existing_type=sa.DATETIME(), nullable=True)


def downgrade() -> None:
    """Downgrade schema.

    Reverting `cadence`/`next_run_at` to `NOT NULL` fails if any one-shot schedule (with either
    column null) exists - the same caveat any nullable-to-required downgrade carries; there is no
    data-preserving way to reverse "this schedule never had a cadence."
    """
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.alter_column('next_run_at', existing_type=sa.DATETIME(), nullable=False)
        batch_op.alter_column('cadence', existing_type=sa.VARCHAR(length=128), nullable=False)
        batch_op.drop_column('expire_after_days')
        batch_op.drop_column('retention')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_jobs_baseline_job_id_jobs', type_='foreignkey')
        batch_op.drop_constraint('fk_jobs_schedule_id_schedules', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_jobs_baseline_job_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_schedule_id'))
        batch_op.drop_column('baseline_job_id')
        batch_op.drop_column('schedule_id')
