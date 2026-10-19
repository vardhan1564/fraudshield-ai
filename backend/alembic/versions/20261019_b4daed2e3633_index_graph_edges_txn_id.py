"""index graph_edges txn_id (T3.6: the graph_edges consumer replaces rows by txn_id on retry)

Revision ID: b4daed2e3633
Revises: 4cf60e4bd39d
Create Date: 2026-10-19 13:16:05.880622+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4daed2e3633"
down_revision: str | None = "4cf60e4bd39d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_graph_edges_txn_id"), "graph_edges", ["txn_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_graph_edges_txn_id"), table_name="graph_edges")
