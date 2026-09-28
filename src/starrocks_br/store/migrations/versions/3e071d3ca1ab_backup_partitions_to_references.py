"""backup partitions to references

Revision ID: 3e071d3ca1ab
Revises: 15e118a64b09
Create Date: 2026-09-28 12:16:32.201077

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e071d3ca1ab'
down_revision: Union[str, Sequence[str], None] = '15e118a64b09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    `backup_partitions` (written before a backup ran, keyed by `(cluster_id, key_hash)`) becomes
    `backup_references` (written only once a backup job's StarRocks operation reaches `FINISHED`,
    keyed by `job_id`) - see backup-references/design.md Decision 1. Existing rows are backfilled
    by resolving `job_id`/`repository`/`snapshot_timestamp` from the `SUCCESS` `Job` matching
    `(cluster_id, label)`; a row that can't be resolved this way belonged to a job that never
    succeeded (or predates job-scoped history) and is dropped, matching the new rule that only a
    successful job has references (SPEC.md §16).
    """
    op.rename_table('backup_partitions', 'backup_references')

    with op.batch_alter_table('backup_references', schema=None) as batch_op:
        batch_op.add_column(sa.Column('job_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('repository', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('snapshot_timestamp', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column('label', new_column_name='snapshot_label', existing_type=sa.String(length=255))

    op.execute(
        """
        UPDATE backup_references
        SET job_id = (
                SELECT j.id FROM jobs j
                WHERE j.cluster_id = backup_references.cluster_id
                  AND j.label = backup_references.snapshot_label
                  AND j.status = 'SUCCESS'
            ),
            repository = (
                SELECT j.repository FROM jobs j
                WHERE j.cluster_id = backup_references.cluster_id
                  AND j.label = backup_references.snapshot_label
                  AND j.status = 'SUCCESS'
            ),
            snapshot_timestamp = (
                SELECT j.finished_at FROM jobs j
                WHERE j.cluster_id = backup_references.cluster_id
                  AND j.label = backup_references.snapshot_label
                  AND j.status = 'SUCCESS'
            )
        """
    )
    op.execute("DELETE FROM backup_references WHERE job_id IS NULL")

    with op.batch_alter_table('backup_references', schema=None) as batch_op:
        batch_op.alter_column('job_id', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('repository', existing_type=sa.String(length=255), nullable=False)
        batch_op.alter_column(
            'snapshot_timestamp', existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.alter_column('partition_name', existing_type=sa.String(length=255), nullable=True)
        batch_op.drop_constraint('uq_backup_partitions_cluster_key_hash', type_='unique')
        batch_op.drop_index('ix_backup_partitions_cluster_label')
        batch_op.drop_index('ix_backup_partitions_cluster_id')
        batch_op.drop_column('key_hash')
        batch_op.drop_column('cluster_id')
        batch_op.create_index(batch_op.f('ix_backup_references_job_id'), ['job_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_backup_references_job_id_jobs', 'jobs', ['job_id'], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    """Downgrade schema.

    Reconstructs the `backup_partitions` shape, but not its original data fidelity: `cluster_id` is
    recovered via `job_id -> Job.cluster_id`, while `key_hash` is regenerated as an opaque unique
    value rather than the original content hash (its original composite-key inputs are no longer
    separately available once `job_id`-keyed rows have replaced them). Any reference whose `job_id`
    no longer resolves to a `Job` (should not happen given the `ON DELETE CASCADE`) is dropped.
    """
    with op.batch_alter_table('backup_references', schema=None) as batch_op:
        batch_op.drop_constraint('fk_backup_references_job_id_jobs', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_backup_references_job_id'))

    with op.batch_alter_table('backup_references', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cluster_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('key_hash', sa.String(length=32), nullable=True))

    op.execute(
        """
        UPDATE backup_references
        SET cluster_id = (SELECT j.cluster_id FROM jobs j WHERE j.id = backup_references.job_id)
        """
    )
    op.execute("DELETE FROM backup_references WHERE cluster_id IS NULL")
    op.execute("UPDATE backup_references SET key_hash = lower(hex(randomblob(16)))")

    with op.batch_alter_table('backup_references', schema=None) as batch_op:
        batch_op.alter_column('cluster_id', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('key_hash', existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column('snapshot_label', new_column_name='label', existing_type=sa.String(length=255))
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('snapshot_timestamp')
        batch_op.drop_column('repository')
        batch_op.drop_column('job_id')

    with op.batch_alter_table('backup_references', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_backup_partitions_cluster_key_hash', ['cluster_id', 'key_hash']
        )
        batch_op.create_index('ix_backup_partitions_cluster_label', ['cluster_id', 'label'], unique=False)
        batch_op.create_index('ix_backup_partitions_cluster_id', ['cluster_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_backup_partitions_cluster_id_clusters', 'clusters', ['cluster_id'], ['id'], ondelete='CASCADE'
        )

    op.rename_table('backup_references', 'backup_partitions')
