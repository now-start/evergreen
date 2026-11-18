"""Initial PostgreSQL schema for Evergreen 3."""

from alembic import op
from sqlalchemy import BigInteger, Column, Identity, Integer, String, Text

revision = "0001_postgresql"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evergreen_execution_state",
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("payload", Text, nullable=False),
    )
    op.create_table(
        "evergreen_execution_event",
        Column("id", BigInteger, Identity(), primary_key=True),
        Column("time", String(40), nullable=False),
        Column("event", String(40), nullable=False),
        Column("payload", Text, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("주문 복구 이력 보호: 삭제 downgrade 대신 전진 마이그레이션을 작성하세요")
