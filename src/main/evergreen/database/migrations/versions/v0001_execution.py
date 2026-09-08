"""Initial execution tables; frozen independently of runtime models.

Revision ID: 0001_execution
Revises: none
"""

from alembic import op
from sqlalchemy import Column, Integer, MetaData, String, Table, Text

revision = "0001_execution"
down_revision = None
branch_labels = None
depends_on = None


def legacy_schema() -> MetaData:
    schema = MetaData()
    Table(
        "evergreen_execution_state",
        schema,
        Column("id", Integer, primary_key=True),
        Column("payload", Text, nullable=False),
        mysql_engine="InnoDB",
    )
    Table(
        "evergreen_execution_event",
        schema,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("time", String(40), nullable=False),
        Column("event", String(40), nullable=False),
        Column("payload", Text, nullable=False),
        mysql_engine="InnoDB",
    )
    return schema


def upgrade() -> None:
    for table in legacy_schema().sorted_tables:
        table.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    raise RuntimeError("주문 복구 이력 보호: 삭제 downgrade 대신 전진 마이그레이션을 작성하세요")
