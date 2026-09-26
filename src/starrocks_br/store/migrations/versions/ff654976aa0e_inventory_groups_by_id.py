"""inventory groups by id

Revision ID: ff654976aa0e
Revises: 059d2b79d525
Create Date: 2026-09-26 09:46:51.207239

Introduces a first-class `inventory_groups` table with a surrogate id, and
repoints `table_inventory` and `schedules` at it via `inventory_group_id`
instead of the free-form `inventory_group`/`group_name` strings they used
before. No production data exists yet, so this is a clean cutover with no
data migration - see openspec/changes/inventory-groups-by-id/design.md.

Column adds/drops on `table_inventory` and `schedules` use batch mode
because SQLite cannot add a NOT NULL column without a server default, or
drop/add constraints, via plain ALTER TABLE.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ff654976aa0e'
down_revision: str | Sequence[str] | None = '059d2b79d525'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'inventory_groups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cluster_id', 'name', name='uq_inventory_groups_cluster_name'),
    )
    op.create_index(op.f('ix_inventory_groups_cluster_id'), 'inventory_groups', ['cluster_id'], unique=False)

    with op.batch_alter_table('table_inventory', recreate='always') as batch_op:
        batch_op.add_column(sa.Column('inventory_group_id', sa.Integer(), nullable=False))
        batch_op.drop_index('ix_table_inventory_cluster_group')
        batch_op.drop_constraint('uq_table_inventory_membership', type_='unique')
        batch_op.drop_column('inventory_group')
        batch_op.create_foreign_key(
            'fk_table_inventory_inventory_group_id_inventory_groups',
            'inventory_groups',
            ['inventory_group_id'],
            ['id'],
            ondelete='CASCADE',
        )
        batch_op.create_unique_constraint(
            'uq_table_inventory_membership',
            ['cluster_id', 'inventory_group_id', 'database_name', 'table_name'],
        )
        batch_op.create_index('ix_table_inventory_cluster_group', ['cluster_id', 'inventory_group_id'])

    with op.batch_alter_table('schedules', recreate='always') as batch_op:
        batch_op.add_column(sa.Column('inventory_group_id', sa.Integer(), nullable=False))
        batch_op.drop_column('group_name')
        batch_op.create_foreign_key(
            'fk_schedules_inventory_group_id_inventory_groups',
            'inventory_groups',
            ['inventory_group_id'],
            ['id'],
            ondelete='RESTRICT',
        )
        batch_op.create_index(op.f('ix_schedules_inventory_group_id'), ['inventory_group_id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('schedules', recreate='always') as batch_op:
        batch_op.drop_index(op.f('ix_schedules_inventory_group_id'))
        batch_op.drop_constraint('fk_schedules_inventory_group_id_inventory_groups', type_='foreignkey')
        batch_op.add_column(sa.Column('group_name', sa.String(length=128), nullable=False, server_default=''))
        batch_op.drop_column('inventory_group_id')
        batch_op.alter_column('group_name', server_default=None)

    with op.batch_alter_table('table_inventory', recreate='always') as batch_op:
        batch_op.drop_index('ix_table_inventory_cluster_group')
        batch_op.drop_constraint('uq_table_inventory_membership', type_='unique')
        batch_op.drop_constraint('fk_table_inventory_inventory_group_id_inventory_groups', type_='foreignkey')
        batch_op.add_column(sa.Column('inventory_group', sa.String(length=128), nullable=False, server_default=''))
        batch_op.drop_column('inventory_group_id')
        batch_op.alter_column('inventory_group', server_default=None)
        batch_op.create_unique_constraint(
            'uq_table_inventory_membership',
            ['cluster_id', 'inventory_group', 'database_name', 'table_name'],
        )
        batch_op.create_index('ix_table_inventory_cluster_group', ['cluster_id', 'inventory_group'])

    op.drop_index(op.f('ix_inventory_groups_cluster_id'), table_name='inventory_groups')
    op.drop_table('inventory_groups')
