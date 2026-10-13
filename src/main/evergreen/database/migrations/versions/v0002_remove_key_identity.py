"""Remove API-key fingerprints without resetting execution history."""

from alembic import op
from sqlalchemy import text

revision = "0002_remove_key_identity"
down_revision = "0001_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        text(
            "UPDATE evergreen_execution_state SET payload = JSON_REMOVE(payload, '$.identity') "
            "WHERE JSON_VALID(payload)"
        )
    )
    connection.execute(
        text(
            "UPDATE evergreen_execution_event "
            "SET payload = JSON_REMOVE(payload, '$.identity', '$.state.identity') "
            "WHERE JSON_VALID(payload)"
        )
    )


def downgrade() -> None:
    raise RuntimeError("제거한 API 키 해시는 복원할 수 없습니다")
