"""add schedule repository

Revision ID: d96bf30a5582
Revises: 8c4f5dcf3c2c
Create Date: 2026-09-26 00:00:01.000000

Adds a required `repository` column to `schedules`. Discovered during
implementation of decouple-database-and-repository-from-cluster:
`run_due_schedules` submits full/incremental backup jobs on a schedule's
behalf, and those job types now require a `repository` in their params -
a schedule needs its own copy of that decision, since there is no
per-invocation client to ask at run-due time. See
openspec/changes/decouple-database-and-repository-from-cluster/design.md
"`Schedule` gains its own `repository`". No production data exists yet,
so this is a clean cutover with no data migration.

Uses batch mode because SQLite cannot add a NOT NULL column without a
server default via plain ALTER TABLE.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd96bf30a5582'
down_revision: str | Sequence[str] | None = '8c4f5dcf3c2c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('schedules', recreate='always') as batch_op:
        batch_op.add_column(sa.Column('repository', sa.String(length=128), nullable=False, server_default=''))
        batch_op.alter_column('repository', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('schedules', recreate='always') as batch_op:
        batch_op.drop_column('repository')
