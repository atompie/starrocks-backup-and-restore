"""drop cluster database and repository

Revision ID: 8c4f5dcf3c2c
Revises: ff654976aa0e
Create Date: 2026-09-26 00:00:00.000000

Removes `clusters.database` and `clusters.repository`. Neither can actually
be known at cluster-registration time: which database(s) to back up is
decided per inventory group (whose table memberships already carry a
`database_name` per row), and which repository to use is decided per job -
see openspec/changes/decouple-database-and-repository-from-cluster/design.md.
No production data exists yet, so this is a clean cutover with no data
migration.

Uses batch mode because SQLite cannot drop a column via plain ALTER TABLE
in all versions Alembic targets here.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c4f5dcf3c2c'
down_revision: str | Sequence[str] | None = 'ff654976aa0e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('clusters', recreate='always') as batch_op:
        batch_op.drop_column('database')
        batch_op.drop_column('repository')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('clusters', recreate='always') as batch_op:
        batch_op.add_column(sa.Column('repository', sa.String(length=128), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('database', sa.String(length=128), nullable=False, server_default=''))
        batch_op.alter_column('repository', server_default=None)
        batch_op.alter_column('database', server_default=None)
