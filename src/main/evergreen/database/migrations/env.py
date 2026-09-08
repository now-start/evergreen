"""Use only the connection supplied by the lock-owning migration runner."""

from alembic import context
from sqlalchemy import Connection

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("python -m evergreen.database 명령으로 실행하세요")
context.configure(connection=connection, version_table="evergreen_alembic_version")
with context.begin_transaction():
    context.run_migrations()
